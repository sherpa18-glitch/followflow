"""Discovery engine for finding target accounts in the pet/dog niche.

Crawls hashtags, mines engagements from top posts, fetches account
details, detects regions, and applies the full filter pipeline:

  - Followers < 2,000
  - Following > 3,000
  - Active in last 7 days
  - Pet/dog niche
  - Regions: NA, KR, JP, EU, AU (UNKNOWN included for broader reach)
  - Not already followed
  - Not on blocklist

Prioritizes confirmed-region accounts, fills remaining with unknown.
"""

import asyncio
import random
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.utils.logger import get_logger
from app.utils.rate_limiter import random_delay

logger = get_logger("discovery")

INSTAGRAM_URL = "https://www.instagram.com/"

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
    """Search pet/dog hashtags and collect usernames from recent posts.

    Args:
        page: Authenticated Playwright page.
        tags: Hashtags to crawl (defaults to NICHE_HASHTAGS).
        limit_per_tag: Max accounts to collect per hashtag.

    Returns:
        Set of discovered usernames.
    """
    tags = tags or NICHE_HASHTAGS
    usernames = set()

    # Shuffle tags so we don't always crawl in the same order
    shuffled_tags = list(tags)
    random.shuffle(shuffled_tags)

    # Only crawl a subset each run to manage time
    tags_to_crawl = shuffled_tags[:8]

    for tag in tags_to_crawl:
        try:
            tag_url = f"{INSTAGRAM_URL}explore/tags/{tag}/"
            await page.goto(tag_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Collect post links from the tag page
            post_links = await page.query_selector_all(
                'a[href*="/p/"], a[href*="/reel/"]'
            )

            collected = 0
            for link in post_links[:limit_per_tag]:
                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue

                    # Navigate to the post to get the author
                    await page.goto(
                        f"{INSTAGRAM_URL.rstrip('/')}{href}",
                        wait_until="domcontentloaded",
                        timeout=10000,
                    )
                    await asyncio.sleep(1.5)

                    # Extract the post author
                    author = await _extract_post_author(page)
                    if author:
                        usernames.add(author)
                        collected += 1

                    await random_delay(2, 4)

                except Exception as e:
                    logger.debug(f"Error processing post in #{tag}: {e}")
                    continue

            logger.info(
                f"#{tag}: collected {collected} usernames",
                extra={"action": "hashtag_crawl", "detail": f"#{tag}:{collected}"},
            )

        except Exception as e:
            logger.warning(f"Error crawling #{tag}: {e}")
            continue

    return usernames


async def mine_engagements(
    page: Page,
    limit: int = 50,
) -> Set[str]:
    """Mine usernames from "Liked by" lists on top niche posts.

    Navigates to a few niche hashtag pages, opens top posts,
    and scrapes the "liked by" user list.

    Args:
        page: Authenticated Playwright page.
        limit: Maximum usernames to collect.

    Returns:
        Set of discovered usernames.
    """
    usernames = set()

    # Use a couple of popular tags for engagement mining
    mining_tags = random.sample(NICHE_HASHTAGS[:10], min(3, len(NICHE_HASHTAGS)))

    for tag in mining_tags:
        try:
            tag_url = f"{INSTAGRAM_URL}explore/tags/{tag}/"
            await page.goto(tag_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Click on first few top posts
            post_links = await page.query_selector_all(
                'a[href*="/p/"], a[href*="/reel/"]'
            )

            for link in post_links[:3]:
                if len(usernames) >= limit:
                    break

                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue

                    await page.goto(
                        f"{INSTAGRAM_URL.rstrip('/')}{href}",
                        wait_until="domcontentloaded",
                        timeout=10000,
                    )
                    await asyncio.sleep(2)

                    # Try to open "Liked by" list
                    liked_users = await _get_liked_by_users(page, max_users=20)
                    usernames.update(liked_users)

                    await random_delay(2, 4)

                except Exception as e:
                    logger.debug(f"Error mining engagement: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Error mining #{tag}: {e}")
            continue

    return usernames


async def get_account_details(page: Page, username: str) -> Optional[Dict]:
    """Fetch follower/following counts, bio, and recent post info.

    Args:
        page: Authenticated Playwright page.
        username: The account to look up.

    Returns:
        Dict with account details, or None if profile can't be loaded.
    """
    try:
        profile_url = f"{INSTAGRAM_URL}{username}/"
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=10000)
        await asyncio.sleep(1.5)

        # Check if profile exists / is accessible
        if await _is_profile_not_found(page):
            return None

        # Extract follower/following counts
        counts = await _extract_profile_counts(page)
        if not counts:
            return None

        # Extract bio text
        bio = await _extract_bio(page)

        # Check recent post dates
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

        # Detect region
        region, confidence = detect_region(details)
        details["region"] = region
        details["region_confidence"] = confidence

        return details

    except Exception as e:
        logger.debug(f"Error fetching details for @{username}: {e}")
        return None


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

        # Small delay between profile checks
        await random_delay(2, 4)

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
