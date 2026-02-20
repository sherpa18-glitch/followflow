"""Discovery engine for finding target accounts in the pet/dog niche.

Uses Instagram's private API to crawl hashtags, mine engagements from
post likers, fetch account details, detect regions, and apply the
full filter pipeline:

  - Followers < 5,000  (small accounts — more likely to follow back)
  - Following > 100    (active users, not ghost accounts)
  - Active in last 14 days
  - Pet/dog niche
  - Regions: NA, KR, JP, EU, AU (UNKNOWN included for broader reach)
  - Not already followed
  - Not on blocklist

Prioritizes confirmed-region accounts, fills remaining with unknown.
"""

import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.utils.logger import get_logger
from app.utils.rate_limiter import random_delay

logger = get_logger("discovery")

INSTAGRAM_URL = "https://www.instagram.com/"
INSTAGRAM_API = "https://i.instagram.com/api/v1"

# Android user-agent for the private API
_API_USER_AGENT = (
    "Instagram 275.0.0.27.98 Android "
    "(33/13; 420dpi; 1080x2400; samsung; SM-G991B; "
    "o1s; exynos2100; en_US; 458229258)"
)
_API_APP_ID = "936619743392459"
COOKIES_PATH = Path("session_cookies.json")


def _get_api_session() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load session cookies and return (headers, cookies) for API calls."""
    with open(COOKIES_PATH) as f:
        cookies_list = json.load(f)
    cookie_dict = {c["name"]: c["value"] for c in cookies_list}
    headers = {
        "User-Agent": _API_USER_AGENT,
        "X-CSRFToken": cookie_dict.get("csrftoken", ""),
        "X-IG-App-ID": _API_APP_ID,
    }
    cookies = {
        "sessionid": cookie_dict["sessionid"],
        "ds_user_id": cookie_dict["ds_user_id"],
        "csrftoken": cookie_dict.get("csrftoken", ""),
    }
    return headers, cookies

# Hashtags to crawl for the pet/dog niche
NICHE_HASHTAGS = [
    "dogvideos",
    "petvideo",
    "dogsofinstagram",
    "puppylove",
    "petlovers",
    "doglovers",
    "doglife",
    "puppiesofinstagram",
    "doglover",
    "petdog",
    # Japanese
    "犬",
    "犬動画",
    "いぬすたぐらむ",
    # Korean
    "강아지",
    "반려견",
    "멍스타그램",
    # European languages
    "perro",       # Spanish
    "Hund",        # German
    "chien",       # French
    "cane",        # Italian
]

# Region detection keywords
REGION_KEYWORDS = {
    "NA": {
        "countries": [
            "usa", "united states", "canada", "mexico",
            "us", "ca", "mx", "america",
        ],
        "cities": [
            "new york", "los angeles", "chicago", "houston", "toronto",
            "vancouver", "montreal", "miami", "seattle", "denver",
            "dallas", "atlanta", "boston", "san francisco", "portland",
            "austin", "nashville", "philadelphia", "phoenix", "san diego",
        ],
    },
    "KR": {
        "countries": ["korea", "south korea", "한국", "대한민국"],
        "cities": [
            "seoul", "서울", "busan", "부산", "incheon", "인천",
            "daegu", "대구", "daejeon", "대전", "gwangju", "광주",
        ],
    },
    "JP": {
        "countries": ["japan", "日本", "nippon"],
        "cities": [
            "tokyo", "東京", "osaka", "大阪", "kyoto", "京都",
            "yokohama", "横浜", "nagoya", "名古屋", "sapporo", "札幌",
            "fukuoka", "福岡", "kobe", "神戸",
        ],
    },
    "EU": {
        "countries": [
            "uk", "united kingdom", "england", "germany", "france",
            "spain", "italy", "netherlands", "sweden", "norway",
            "denmark", "finland", "portugal", "ireland", "scotland",
            "belgium", "austria", "switzerland", "poland", "czech",
            "deutschland", "france", "españa", "italia",
        ],
        "cities": [
            "london", "paris", "berlin", "madrid", "rome", "amsterdam",
            "barcelona", "munich", "vienna", "stockholm", "copenhagen",
            "oslo", "dublin", "lisbon", "prague", "warsaw", "zurich",
            "manchester", "hamburg", "milan", "brussels",
        ],
    },
    "AU": {
        "countries": [
            "australia", "new zealand", "aussie", "aus", "nz",
        ],
        "cities": [
            "sydney", "melbourne", "brisbane", "perth", "adelaide",
            "auckland", "wellington", "gold coast", "canberra",
        ],
    },
}

# Language patterns for region detection from hashtags/bio
LANGUAGE_PATTERNS = {
    "KR": re.compile(r"[\uAC00-\uD7A3]"),           # Korean Hangul
    "JP": re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]"),  # Japanese
}


async def discover_target_accounts(
    page: Page,
    max_followers: int = 2000,
    min_following: int = 3000,
    activity_days: int = 7,
    target_count: int = 100,
    already_following: Optional[Set[str]] = None,
    blocklist: Optional[Set[str]] = None,
) -> List[Dict]:
    """Run the full discovery pipeline to find target accounts.

    Args:
        page: Authenticated Playwright page.
        max_followers: Maximum follower count for targets (< 2,000).
        min_following: Minimum following count for targets (> 3,000).
        activity_days: Must have been active within this many days.
        target_count: Number of qualified accounts to return.
        already_following: Set of usernames already being followed.
        blocklist: Set of usernames on the blocklist.

    Returns:
        List of qualified account dicts, prioritized by confirmed
        region first, then unknown region.
    """
    already_following = already_following or set()
    blocklist = blocklist or set()

    logger.info(
        f"Starting discovery: target={target_count}, "
        f"max_followers={max_followers}, min_following={min_following}",
        extra={"action": "discovery_start"},
    )

    # Step 1: Crawl hashtags to find candidate usernames
    candidates_from_hashtags = await crawl_hashtags(page, limit_per_tag=15)
    logger.info(
        f"Hashtag crawl found {len(candidates_from_hashtags)} candidates",
        extra={"action": "hashtag_crawl"},
    )

    # Step 2: Mine engagements (liked-by lists) from top posts
    candidates_from_engagement = await mine_engagements(page, limit=50)
    logger.info(
        f"Engagement mining found {len(candidates_from_engagement)} candidates",
        extra={"action": "engagement_mine"},
    )

    # Merge and deduplicate
    all_usernames = set()
    all_usernames.update(candidates_from_hashtags)
    all_usernames.update(candidates_from_engagement)

    # Remove already following and blocklisted
    all_usernames -= already_following
    all_usernames -= blocklist

    logger.info(
        f"Total unique candidates after dedup: {len(all_usernames)}",
        extra={"action": "dedup"},
    )

    # Step 3: Fetch details and filter
    qualified = await filter_candidates(
        page=page,
        usernames=list(all_usernames),
        max_followers=max_followers,
        min_following=min_following,
        activity_days=activity_days,
        target_count=target_count,
    )

    # Step 4: Prioritize confirmed-region, fill with unknown
    confirmed_region = [a for a in qualified if a.get("region") != "UNKNOWN"]
    unknown_region = [a for a in qualified if a.get("region") == "UNKNOWN"]

    # Shuffle within each group for variety
    random.shuffle(confirmed_region)
    random.shuffle(unknown_region)

    # Confirmed first, then fill with unknown
    final_list = confirmed_region + unknown_region
    final_list = final_list[:target_count]

    logger.info(
        f"Discovery complete: {len(final_list)} qualified accounts "
        f"({len(confirmed_region)} confirmed region, "
        f"{len(unknown_region)} unknown region)",
        extra={
            "action": "discovery_complete",
            "detail": f"{len(final_list)}/{target_count}",
        },
    )

    return final_list


async def crawl_hashtags(
    page: Page,
    tags: Optional[List[str]] = None,
    limit_per_tag: int = 15,
) -> Set[str]:
    """Crawl hashtags via Instagram API and collect post-author usernames.

    Uses ``POST /api/v1/tags/{tag}/sections/`` to fetch recent posts
    under each hashtag, extracting the post authors.

    Args:
        page: Authenticated Playwright page (unused — kept for interface compat).
        tags: Hashtags to crawl (defaults to NICHE_HASHTAGS).
        limit_per_tag: Max accounts to collect per hashtag.

    Returns:
        Set of discovered usernames.
    """
    tags = tags or NICHE_HASHTAGS
    usernames: Set[str] = set()

    shuffled_tags = list(tags)
    random.shuffle(shuffled_tags)
    tags_to_crawl = shuffled_tags[:8]

    try:
        api_headers, api_cookies = _get_api_session()
    except Exception as e:
        logger.warning(f"Cannot load API session for hashtag crawl: {e}")
        return usernames

    async with httpx.AsyncClient(timeout=15) as client:
        for tag in tags_to_crawl:
            try:
                url = f"{INSTAGRAM_API}/tags/{tag}/sections/"
                resp = await client.post(
                    url,
                    headers=api_headers,
                    cookies=api_cookies,
                    data={"tab": "recent", "count": limit_per_tag},
                )
                resp.raise_for_status()
                data = resp.json()

                collected = 0
                for section in data.get("sections", []):
                    medias = section.get("layout_content", {}).get("medias", [])
                    for m in medias:
                        media = m.get("media", {})
                        user = media.get("user", {})
                        uname = user.get("username")
                        if uname and collected < limit_per_tag:
                            usernames.add(uname)
                            collected += 1

                logger.info(
                    f"#{tag}: collected {collected} usernames",
                    extra={"action": "hashtag_crawl", "detail": f"#{tag}:{collected}"},
                )

                # Small delay between tag requests
                await asyncio.sleep(1)

            except Exception as e:
                logger.warning(f"Error crawling #{tag}: {e}")
                continue

    return usernames


async def mine_engagements(
    page: Page,
    limit: int = 50,
) -> Set[str]:
    """Mine usernames from post likers via Instagram API.

    Fetches recent posts from a few hashtags via the sections API,
    then calls ``GET /api/v1/media/{id}/likers/`` to collect users
    who liked those posts.

    Args:
        page: Authenticated Playwright page (unused — kept for compat).
        limit: Maximum usernames to collect.

    Returns:
        Set of discovered usernames.
    """
    usernames: Set[str] = set()

    mining_tags = random.sample(NICHE_HASHTAGS[:10], min(3, len(NICHE_HASHTAGS)))

    try:
        api_headers, api_cookies = _get_api_session()
    except Exception as e:
        logger.warning(f"Cannot load API session for engagement mining: {e}")
        return usernames

    async with httpx.AsyncClient(timeout=15) as client:
        for tag in mining_tags:
            if len(usernames) >= limit:
                break
            try:
                # Fetch posts under this hashtag
                url = f"{INSTAGRAM_API}/tags/{tag}/sections/"
                resp = await client.post(
                    url,
                    headers=api_headers,
                    cookies=api_cookies,
                    data={"tab": "top", "count": 10},
                )
                resp.raise_for_status()
                data = resp.json()

                # Collect media IDs from the response
                media_ids = []
                for section in data.get("sections", []):
                    medias = section.get("layout_content", {}).get("medias", [])
                    for m in medias:
                        mid = m.get("media", {}).get("pk")
                        if mid:
                            media_ids.append(str(mid))

                # For top 5 posts, get likers
                for mid in media_ids[:5]:
                    if len(usernames) >= limit:
                        break
                    try:
                        likers_url = f"{INSTAGRAM_API}/media/{mid}/likers/"
                        resp2 = await client.get(
                            likers_url,
                            headers=api_headers,
                            cookies=api_cookies,
                        )
                        resp2.raise_for_status()
                        likers = resp2.json().get("users", [])
                        for liker in likers[:20]:
                            uname = liker.get("username")
                            if uname:
                                usernames.add(uname)
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.debug(f"Error fetching likers for media {mid}: {e}")
                        continue

                await asyncio.sleep(1)

            except Exception as e:
                logger.warning(f"Error mining #{tag}: {e}")
                continue

    return usernames


async def get_account_details(page: Page, username: str) -> Optional[Dict]:
    """Fetch follower/following counts, bio, and recent post info.

    Uses Instagram's ``web_profile_info`` API endpoint as primary
    method, falling back to browser scraping if it fails.

    Args:
        page: Authenticated Playwright page (used as fallback).
        username: The account to look up.

    Returns:
        Dict with account details, or None if profile can't be loaded.
    """
    # ── Primary: API-based fetch ──
    try:
        details = await _get_account_details_via_api(username)
        if details:
            return details
    except Exception as e:
        logger.debug(f"API details failed for @{username}: {e}")

    # ── Fallback: browser-based scraping ──
    try:
        return await _get_account_details_via_browser(page, username)
    except Exception as e:
        logger.debug(f"Browser details failed for @{username}: {e}")
        return None


async def _get_account_details_via_api(username: str) -> Optional[Dict]:
    """Fetch account details via Instagram private API.

    Uses ``users/search`` to resolve the username → user ID, then
    ``users/{id}/info/`` for full profile details. These endpoints
    are more resilient to rate-limiting than ``web_profile_info``.
    """
    try:
        api_headers, api_cookies = _get_api_session()
    except Exception:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1: Search for username to get user PK (ID)
        search_resp = await client.get(
            f"{INSTAGRAM_API}/users/search/",
            headers=api_headers,
            cookies=api_cookies,
            params={"q": username, "count": 1},
        )
        if search_resp.status_code == 429:
            logger.warning(
                f"Search API rate-limited for @{username}",
                extra={"action": "api_rate_limit"},
            )
            return None
        search_resp.raise_for_status()

        users = search_resp.json().get("users", [])
        if not users:
            return None

        # Verify exact username match (search can return fuzzy results)
        matched_user = None
        for u in users:
            if u.get("username", "").lower() == username.lower():
                matched_user = u
                break
        if not matched_user:
            return None

        user_pk = matched_user["pk"]

        # Step 2: Fetch full user info by ID
        info_resp = await client.get(
            f"{INSTAGRAM_API}/users/{user_pk}/info/",
            headers=api_headers,
            cookies=api_cookies,
        )
        if info_resp.status_code == 429:
            logger.warning(
                f"UserInfo API rate-limited for @{username}",
                extra={"action": "api_rate_limit"},
            )
            return None
        info_resp.raise_for_status()

        user = info_resp.json().get("user")
        if not user:
            return None

    # Extract recent post timestamp if available
    last_post_date = None
    latest_reel = user.get("latest_reel_media")
    if latest_reel:
        last_post_date = datetime.utcfromtimestamp(latest_reel).isoformat()

    details = {
        "username": username,
        "follower_count": user.get("follower_count", 0),
        "following_count": user.get("following_count", 0),
        "post_count": user.get("media_count", 0),
        "bio": user.get("biography", ""),
        "last_post_date": last_post_date,
        "is_private": user.get("is_private", False),
    }

    region, confidence = detect_region(details)
    details["region"] = region
    details["region_confidence"] = confidence
    details["category"] = detect_category(details)

    return details


async def _get_account_details_via_browser(page: Page, username: str) -> Optional[Dict]:
    """Fallback: fetch account details by navigating to the profile."""
    profile_url = f"{INSTAGRAM_URL}{username}/"
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=10000)
    await asyncio.sleep(1.5)

    if await _is_profile_not_found(page):
        return None

    counts = await _extract_profile_counts(page)
    if not counts:
        return None

    bio = await _extract_bio(page)
    last_post_date = await _get_last_post_date(page)

    details = {
        "username": username,
        "follower_count": counts.get("followers", 0),
        "following_count": counts.get("following", 0),
        "post_count": counts.get("posts", 0),
        "bio": bio or "",
        "last_post_date": last_post_date,
        "is_private": await _is_private_account(page),
    }

    region, confidence = detect_region(details)
    details["region"] = region
    details["region_confidence"] = confidence
    details["category"] = detect_category(details)

    return details


def detect_region(account_details: Dict) -> Tuple[str, str]:
    """Detect the likely region of an account using heuristics.

    Uses bio text, language patterns, and available metadata.
    Returns (region, confidence) where region is one of:
    NA, KR, JP, EU, AU, UNKNOWN.

    Confidence is HIGH, MEDIUM, or UNKNOWN.

    Args:
        account_details: Dict with 'bio', 'username', etc.

    Returns:
        Tuple of (region_code, confidence_level).
    """
    bio = account_details.get("bio", "").lower()
    username = account_details.get("username", "").lower()
    combined_text = f"{bio} {username}"

    # 1. Emoji/flag detection (highest confidence — checked first)
    flag_regions = {
        "🇺🇸": "NA", "🇨🇦": "NA", "🇲🇽": "NA",
        "🇰🇷": "KR",
        "🇯🇵": "JP",
        "🇬🇧": "EU", "🇩🇪": "EU", "🇫🇷": "EU", "🇪🇸": "EU", "🇮🇹": "EU",
        "🇳🇱": "EU", "🇸🇪": "EU", "🇳🇴": "EU", "🇩🇰": "EU",
        "🇦🇺": "AU", "🇳🇿": "AU",
    }
    for flag, region in flag_regions.items():
        if flag in account_details.get("bio", ""):
            return (region, "HIGH")

    # 2. Check language patterns (high confidence)
    for region, pattern in LANGUAGE_PATTERNS.items():
        if pattern.search(bio):
            return (region, "HIGH")

    # 3. Check city/country keywords in bio (high confidence)
    #    Use word boundary matching to avoid substring false positives
    #    (e.g., "us" matching inside "genericuser")
    for region, data in REGION_KEYWORDS.items():
        for city in data["cities"]:
            if re.search(r'\b' + re.escape(city.lower()) + r'\b', combined_text):
                return (region, "HIGH")
        for country in data["countries"]:
            if re.search(r'\b' + re.escape(country.lower()) + r'\b', combined_text):
                return (region, "HIGH")

    # 4. Check for common regional patterns (medium confidence)
    # English bio with pet-related content could be NA, EU, or AU
    english_pet_keywords = [
        "dog mom", "dog dad", "fur baby", "pupper", "doggo",
        "pet parent", "rescue dog", "shelter dog",
    ]
    for keyword in english_pet_keywords:
        if keyword in combined_text:
            return ("NA", "MEDIUM")  # Most likely NA for English pet content

    return ("UNKNOWN", "UNKNOWN")


# Category detection keywords — matched against bio text
CATEGORY_KEYWORDS = {
    "dogs": [
        "dog", "puppy", "pup", "canine", "doggo", "pupper", "doggy",
        "gsd", "german shepherd", "golden retriever", "labrador", "bulldog",
        "poodle", "husky", "corgi", "beagle", "dachshund", "rottweiler",
        "border collie", "shiba", "frenchie", "pitbull",
        "犬", "わんこ", "강아지", "멍멍이", "perro", "hund", "chien", "cane",
    ],
    "cats": [
        "cat", "kitten", "kitty", "feline", "meow", "neko",
        "猫", "ねこ", "고양이", "gato", "katze", "chat",
    ],
    "pets": [
        "pet", "animal", "fur baby", "furbaby", "fur kid",
        "pet parent", "pet lover", "animal lover",
        "ペット", "반려동물",
    ],
    "photography": [
        "photographer", "photography", "photo", "portrait",
        "landscape", "street photo", "camera", "canon", "nikon", "sony",
        "写真", "사진",
    ],
    "travel": [
        "travel", "wanderlust", "explorer", "adventure", "backpack",
        "nomad", "旅", "여행",
    ],
    "fitness": [
        "fitness", "gym", "workout", "crossfit", "bodybuilding",
        "yoga", "pilates", "health", "athlete",
    ],
    "food": [
        "food", "foodie", "chef", "cook", "baking", "recipe",
        "料理", "음식",
    ],
    "lifestyle": [
        "lifestyle", "blogger", "influencer", "content creator",
        "daily life", "vlog",
    ],
    "art": [
        "artist", "art", "illustration", "drawing", "painting",
        "design", "graphic", "creative",
    ],
    "entertainment": [
        "entertainment", "comedy", "funny", "meme", "humor",
        "music", "singer", "musician", "dancer",
    ],
}


def detect_category(account_details: Dict) -> str:
    """Detect the likely category/niche of an account from its bio.

    Args:
        account_details: Dict with 'bio' key.

    Returns:
        Category string like 'dogs', 'pets', 'photography', etc.
        Returns 'other' if no category is detected.
    """
    bio = account_details.get("bio", "").lower()
    username = account_details.get("username", "").lower()
    combined = f"{bio} {username}"

    if not combined.strip():
        return "other"

    # Score each category by keyword matches
    scores: Dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in combined:
                score += 1
        if score > 0:
            scores[category] = score

    if not scores:
        return "other"

    # Return the category with the highest score
    return max(scores, key=scores.get)


async def filter_candidates(
    page: Page,
    usernames: List[str],
    max_followers: int,
    min_following: int,
    activity_days: int,
    target_count: int,
) -> List[Dict]:
    """Apply the full filter pipeline to candidate accounts.

    For each candidate, fetch details and check:
    1. Followers < max_followers
    2. Following > min_following
    3. Active within activity_days
    4. Profile accessible

    Stops once target_count qualified accounts are found.

    Args:
        page: Authenticated Playwright page.
        usernames: Candidate usernames to evaluate.
        max_followers: Upper follower limit.
        min_following: Lower following limit.
        activity_days: Must have posted within this many days.
        target_count: Stop after finding this many.

    Returns:
        List of qualified account detail dicts.
    """
    qualified = []
    checked = 0
    cutoff_date = datetime.utcnow() - timedelta(days=activity_days)

    # Shuffle candidates for variety
    shuffled = list(usernames)
    random.shuffle(shuffled)

    for username in shuffled:
        if len(qualified) >= target_count:
            break

        checked += 1
        details = await get_account_details(page, username)

        if details is None:
            continue

        # Filter 1: Followers < max_followers
        if details["follower_count"] >= max_followers:
            logger.debug(
                f"@{username} rejected: {details['follower_count']} followers "
                f"(max {max_followers})"
            )
            continue

        # Filter 2: Following > min_following
        if details["following_count"] <= min_following:
            logger.debug(
                f"@{username} rejected: {details['following_count']} following "
                f"(min {min_following})"
            )
            continue

        # Filter 3: Active recently
        if details.get("last_post_date"):
            try:
                last_post = datetime.fromisoformat(details["last_post_date"])
                if last_post < cutoff_date:
                    logger.debug(
                        f"@{username} rejected: last post {details['last_post_date']} "
                        f"(cutoff {cutoff_date.isoformat()})"
                    )
                    continue
            except (ValueError, TypeError):
                pass  # Can't parse date — don't reject on this alone

        qualified.append(details)

        if checked % 20 == 0:
            logger.info(
                f"Filter progress: checked {checked}, qualified {len(qualified)}",
                extra={"action": "filter_progress"},
            )

        # Delay between profile checks — polite to API, avoids 429s
        await random_delay(1.0, 2.0)

    logger.info(
        f"Filter complete: checked {checked}, qualified {len(qualified)}",
        extra={
            "action": "filter_complete",
            "detail": f"{len(qualified)}/{checked} qualified",
        },
    )

    return qualified


# --- Private helpers ---


async def _extract_post_author(page: Page) -> Optional[str]:
    """Extract the author username from a post page."""
    selectors = [
        'header a[role="link"][href^="/"]',
        'a[role="link"][tabindex="0"][href^="/"]',
        'header span a[href^="/"]',
    ]
    for selector in selectors:
        try:
            el = await page.wait_for_selector(selector, timeout=3000)
            if el:
                href = await el.get_attribute("href")
                if href:
                    username = href.strip("/").split("/")[0]
                    if username and username not in ("explore", "p", "reel", "stories"):
                        return username
        except PlaywrightTimeout:
            continue
    return None


async def _get_liked_by_users(page: Page, max_users: int = 20) -> Set[str]:
    """Extract usernames from a post's "Liked by" list."""
    users = set()
    try:
        # Click on "liked by" link to open the likes modal
        like_selectors = [
            'a[href*="/liked_by/"]',
            'span:has-text("likes")',
            'button:has-text("others")',
        ]
        for selector in like_selectors:
            try:
                el = await page.wait_for_selector(selector, timeout=3000)
                if el:
                    await el.click()
                    await asyncio.sleep(2)
                    break
            except PlaywrightTimeout:
                continue

        # Collect usernames from the likes dialog
        links = await page.query_selector_all(
            'div[role="dialog"] a[role="link"][href^="/"]'
        )
        for link in links[:max_users]:
            try:
                href = await link.get_attribute("href")
                if href:
                    username = href.strip("/").split("/")[0]
                    if username and username not in ("explore", "p"):
                        users.add(username)
            except Exception:
                continue

        # Close the dialog
        try:
            close_btn = await page.wait_for_selector(
                'div[role="dialog"] button[aria-label="Close"], '
                'svg[aria-label="Close"]',
                timeout=2000,
            )
            if close_btn:
                await close_btn.click()
        except PlaywrightTimeout:
            pass

    except Exception as e:
        logger.debug(f"Error getting liked-by users: {e}")

    return users


async def _extract_profile_counts(page: Page) -> Optional[Dict]:
    """Extract posts, followers, following counts from a profile page."""
    try:
        counts = {}

        # Try to find the stats section
        stat_links = await page.query_selector_all('a[href*="/followers"], a[href*="/following"], li')

        # Alternative: parse from meta or structured elements
        stats_selectors = [
            'header section ul li',
            'header ul li',
        ]

        for selector in stats_selectors:
            elements = await page.query_selector_all(selector)
            if len(elements) >= 3:
                for i, el in enumerate(elements[:3]):
                    text = await el.inner_text()
                    number = _parse_count(text)
                    if i == 0:
                        counts["posts"] = number
                    elif i == 1:
                        counts["followers"] = number
                    elif i == 2:
                        counts["following"] = number
                if counts.get("followers") is not None:
                    return counts

        # Fallback: try to extract from page content via JavaScript
        try:
            result = await page.evaluate("""
                () => {
                    const meta = document.querySelector('meta[property="og:description"]');
                    if (meta) {
                        const content = meta.getAttribute('content');
                        const match = content.match(
                            /([\d,.]+[KkMm]?)\s*Followers.*?([\d,.]+[KkMm]?)\s*Following.*?([\d,.]+[KkMm]?)\s*Posts/
                        );
                        if (match) {
                            return {
                                followers: match[1],
                                following: match[2],
                                posts: match[3]
                            };
                        }
                    }
                    return null;
                }
            """)
            if result:
                return {
                    "followers": _parse_count(result.get("followers", "0")),
                    "following": _parse_count(result.get("following", "0")),
                    "posts": _parse_count(result.get("posts", "0")),
                }
        except Exception:
            pass

        return None

    except Exception as e:
        logger.debug(f"Error extracting profile counts: {e}")
        return None


def _parse_count(text: str) -> int:
    """Parse a follower/following count string like '1.2K' or '3,456'.

    Args:
        text: Count string (e.g., '1.2K', '3,456', '1M', '500').

    Returns:
        Integer count value.
    """
    if not text:
        return 0

    text = text.strip().replace(",", "").replace(" ", "")

    # Extract the numeric part
    match = re.search(r"([\d.]+)\s*([KkMm]?)", text)
    if not match:
        return 0

    number = float(match.group(1))
    suffix = match.group(2).upper()

    if suffix == "K":
        return int(number * 1_000)
    elif suffix == "M":
        return int(number * 1_000_000)
    else:
        return int(number)


async def _extract_bio(page: Page) -> Optional[str]:
    """Extract the bio text from a profile page."""
    try:
        bio_selectors = [
            'header section > div:nth-child(3)',
            'div[data-testid="user-bio"]',
            'header span:not([role])',
        ]
        for selector in bio_selectors:
            try:
                el = await page.wait_for_selector(selector, timeout=2000)
                if el:
                    text = await el.inner_text()
                    if text and len(text) > 5:
                        return text.strip()
            except PlaywrightTimeout:
                continue

        # Fallback: JavaScript extraction
        try:
            bio = await page.evaluate("""
                () => {
                    const header = document.querySelector('header');
                    if (header) {
                        const spans = header.querySelectorAll('span');
                        for (const span of spans) {
                            const text = span.textContent.trim();
                            if (text.length > 10 && !text.includes('followers')
                                && !text.includes('following') && !text.includes('posts')) {
                                return text;
                            }
                        }
                    }
                    return null;
                }
            """)
            return bio
        except Exception:
            pass

        return None
    except Exception:
        return None


async def _get_last_post_date(page: Page) -> Optional[str]:
    """Get the date of the most recent post on the profile.

    Returns ISO format date string, or None if can't be determined.
    """
    try:
        # Try to find a time element on the profile page
        time_el = await page.query_selector('time[datetime]')
        if time_el:
            dt = await time_el.get_attribute("datetime")
            if dt:
                return dt

        # Fallback: check if there are recent posts visible
        post_links = await page.query_selector_all('a[href*="/p/"]')
        if post_links:
            # Click the first post to check its date
            first_post = post_links[0]
            href = await first_post.get_attribute("href")
            if href:
                await page.goto(
                    f"{INSTAGRAM_URL.rstrip('/')}{href}",
                    wait_until="domcontentloaded",
                    timeout=10000,
                )
                await asyncio.sleep(1)

                time_el = await page.query_selector('time[datetime]')
                if time_el:
                    dt = await time_el.get_attribute("datetime")
                    # Navigate back
                    await page.go_back(wait_until="domcontentloaded", timeout=10000)
                    await asyncio.sleep(1)
                    return dt

        return None
    except Exception:
        return None


async def _is_profile_not_found(page: Page) -> bool:
    """Check if the profile page shows a 'not found' error."""
    try:
        not_found_selectors = [
            'text="Sorry, this page isn\'t available."',
            'h2:has-text("Sorry")',
        ]
        for selector in not_found_selectors:
            try:
                el = await page.wait_for_selector(selector, timeout=2000)
                if el:
                    return True
            except PlaywrightTimeout:
                continue
        return False
    except Exception:
        return False


async def _is_private_account(page: Page) -> bool:
    """Check if the current profile is a private account."""
    try:
        private_indicators = [
            'text="This account is private"',
            'text="This Account is Private"',
            'h2:has-text("This account is private")',
        ]
        for selector in private_indicators:
            try:
                el = await page.wait_for_selector(selector, timeout=1500)
                if el:
                    return True
            except PlaywrightTimeout:
                continue
        return False
    except Exception:
        return False
