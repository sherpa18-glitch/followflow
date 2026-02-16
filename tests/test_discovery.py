"""Tests for the discovery engine.

Tests the filter pipeline, region detection, and count parsing
logic without hitting Instagram.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.instagram.discovery import (
    detect_region,
    _parse_count,
    filter_candidates,
    discover_target_accounts,
    NICHE_HASHTAGS,
    REGION_KEYWORDS,
)


# --- Tests: Count Parsing ---

class TestParseCount:
    def test_plain_number(self):
        assert _parse_count("1234") == 1234

    def test_number_with_commas(self):
        assert _parse_count("1,234") == 1234
        assert _parse_count("12,345") == 12345

    def test_k_suffix(self):
        assert _parse_count("1.2K") == 1200
        assert _parse_count("1.2k") == 1200
        assert _parse_count("500K") == 500000

    def test_m_suffix(self):
        assert _parse_count("1.5M") == 1500000
        assert _parse_count("2M") == 2000000

    def test_with_label_text(self):
        assert _parse_count("1,234 followers") == 1234
        assert _parse_count("567 posts") == 567

    def test_empty_string(self):
        assert _parse_count("") == 0

    def test_no_number(self):
        assert _parse_count("followers") == 0


# --- Tests: Region Detection ---

class TestDetectRegion:
    def test_korean_bio(self):
        details = {"bio": "강아지 사랑해요 🐶", "username": "puppy_kr"}
        region, confidence = detect_region(details)
        assert region == "KR"
        assert confidence == "HIGH"

    def test_japanese_bio(self):
        details = {"bio": "犬が大好きです", "username": "dog_jp"}
        region, confidence = detect_region(details)
        assert region == "JP"
        assert confidence == "HIGH"

    def test_city_in_bio_na(self):
        details = {"bio": "Dog mom from Los Angeles 🐕", "username": "dogmom"}
        region, confidence = detect_region(details)
        assert region == "NA"
        assert confidence == "HIGH"

    def test_city_in_bio_eu(self):
        details = {"bio": "Pet lover in London", "username": "petlover_uk"}
        region, confidence = detect_region(details)
        assert region == "EU"
        assert confidence == "HIGH"

    def test_city_in_bio_au(self):
        details = {"bio": "Golden retriever dad, Sydney", "username": "goldenboy"}
        region, confidence = detect_region(details)
        assert region == "AU"
        assert confidence == "HIGH"

    def test_country_in_bio(self):
        details = {"bio": "I live in Canada with my pup", "username": "canuck_dog"}
        region, confidence = detect_region(details)
        assert region == "NA"
        assert confidence == "HIGH"

    def test_flag_emoji_kr(self):
        details = {"bio": "🇰🇷 반려견 일상", "username": "kr_pet"}
        region, confidence = detect_region(details)
        # Could match on Korean chars first or flag
        assert region == "KR"
        assert confidence == "HIGH"

    def test_flag_emoji_au(self):
        details = {"bio": "🇦🇺 Aussie dog lover", "username": "aussie_pup"}
        region, confidence = detect_region(details)
        assert region == "AU"
        assert confidence == "HIGH"

    def test_english_pet_keywords(self):
        details = {"bio": "Proud dog mom of 3 rescues", "username": "rescuemom"}
        region, confidence = detect_region(details)
        assert region == "NA"
        assert confidence == "MEDIUM"

    def test_unknown_region(self):
        details = {"bio": "hello world", "username": "genericuser"}
        region, confidence = detect_region(details)
        assert region == "UNKNOWN"
        assert confidence == "UNKNOWN"

    def test_empty_bio(self):
        details = {"bio": "", "username": "noinfo"}
        region, confidence = detect_region(details)
        assert region == "UNKNOWN"
        assert confidence == "UNKNOWN"


# --- Tests: Niche Hashtags ---

class TestNicheHashtags:
    def test_has_english_tags(self):
        assert "dogvideos" in NICHE_HASHTAGS
        assert "dogsofinstagram" in NICHE_HASHTAGS

    def test_has_japanese_tags(self):
        assert "犬" in NICHE_HASHTAGS

    def test_has_korean_tags(self):
        assert "강아지" in NICHE_HASHTAGS

    def test_has_european_tags(self):
        assert "perro" in NICHE_HASHTAGS  # Spanish
        assert "Hund" in NICHE_HASHTAGS   # German

    def test_minimum_tag_count(self):
        assert len(NICHE_HASHTAGS) >= 10


# --- Tests: Region Keywords ---

class TestRegionKeywords:
    def test_all_target_regions_present(self):
        assert "NA" in REGION_KEYWORDS
        assert "KR" in REGION_KEYWORDS
        assert "JP" in REGION_KEYWORDS
        assert "EU" in REGION_KEYWORDS
        assert "AU" in REGION_KEYWORDS

    def test_each_region_has_cities_and_countries(self):
        for region, data in REGION_KEYWORDS.items():
            assert "cities" in data, f"{region} missing cities"
            assert "countries" in data, f"{region} missing countries"
            assert len(data["cities"]) > 0
            assert len(data["countries"]) > 0


# --- Tests: Filter Candidates ---

class TestFilterCandidates:
    @pytest.mark.asyncio
    @patch("app.instagram.discovery.get_account_details")
    @patch("app.instagram.discovery.random_delay", new_callable=AsyncMock)
    async def test_filters_high_followers(self, mock_delay, mock_details):
        """Should reject accounts with followers >= max_followers."""
        mock_details.return_value = {
            "username": "bigaccount",
            "follower_count": 5000,  # Too many
            "following_count": 4000,
            "last_post_date": "2026-02-14T00:00:00",
            "region": "NA",
            "region_confidence": "HIGH",
        }
        mock_delay.return_value = 0

        page = AsyncMock()
        result = await filter_candidates(
            page, ["bigaccount"], max_followers=2000,
            min_following=3000, activity_days=7, target_count=10,
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("app.instagram.discovery.get_account_details")
    @patch("app.instagram.discovery.random_delay", new_callable=AsyncMock)
    async def test_filters_low_following(self, mock_delay, mock_details):
        """Should reject accounts with following <= min_following."""
        mock_details.return_value = {
            "username": "lowfollowing",
            "follower_count": 500,
            "following_count": 1000,  # Too few
            "last_post_date": "2026-02-14T00:00:00",
            "region": "NA",
            "region_confidence": "HIGH",
        }
        mock_delay.return_value = 0

        page = AsyncMock()
        result = await filter_candidates(
            page, ["lowfollowing"], max_followers=2000,
            min_following=3000, activity_days=7, target_count=10,
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("app.instagram.discovery.get_account_details")
    @patch("app.instagram.discovery.random_delay", new_callable=AsyncMock)
    async def test_accepts_qualifying_account(self, mock_delay, mock_details):
        """Should accept accounts meeting all criteria."""
        mock_details.return_value = {
            "username": "perfect_target",
            "follower_count": 1200,   # < 2000 ✓
            "following_count": 4500,  # > 3000 ✓
            "last_post_date": "2026-02-14T00:00:00",  # Recent ✓
            "region": "KR",
            "region_confidence": "HIGH",
        }
        mock_delay.return_value = 0

        page = AsyncMock()
        result = await filter_candidates(
            page, ["perfect_target"], max_followers=2000,
            min_following=3000, activity_days=7, target_count=10,
        )
        assert len(result) == 1
        assert result[0]["username"] == "perfect_target"

    @pytest.mark.asyncio
    @patch("app.instagram.discovery.get_account_details")
    @patch("app.instagram.discovery.random_delay", new_callable=AsyncMock)
    async def test_stops_at_target_count(self, mock_delay, mock_details):
        """Should stop checking once target_count is reached."""
        mock_details.return_value = {
            "username": "good",
            "follower_count": 800,
            "following_count": 4000,
            "last_post_date": "2026-02-14T00:00:00",
            "region": "NA",
            "region_confidence": "HIGH",
        }
        mock_delay.return_value = 0

        page = AsyncMock()
        usernames = [f"user_{i}" for i in range(20)]
        result = await filter_candidates(
            page, usernames, max_followers=2000,
            min_following=3000, activity_days=7, target_count=5,
        )
        assert len(result) == 5

    @pytest.mark.asyncio
    @patch("app.instagram.discovery.get_account_details")
    @patch("app.instagram.discovery.random_delay", new_callable=AsyncMock)
    async def test_handles_none_details(self, mock_delay, mock_details):
        """Should skip accounts where details can't be fetched."""
        mock_details.return_value = None
        mock_delay.return_value = 0

        page = AsyncMock()
        result = await filter_candidates(
            page, ["missing_account"], max_followers=2000,
            min_following=3000, activity_days=7, target_count=10,
        )
        assert len(result) == 0
