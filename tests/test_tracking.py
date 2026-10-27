from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.analytics import geoip
from apps.analytics.models import Click
from apps.analytics.tasks import purge_old_clicks, record_click
from apps.analytics.tracking import parse_referrer, store_click, visitor_hash
from apps.analytics.useragent import parse_user_agent
from tests.conftest import CHROME_WIN, GOOGLEBOT, IPHONE


def now_iso():
    return timezone.now().isoformat()


class TestUserAgent:
    def test_desktop_chrome(self):
        ua = parse_user_agent(CHROME_WIN)
        assert (ua.browser, ua.os, ua.device_type, ua.is_bot) == ("Chrome", "Windows", "desktop", False)

    def test_mobile_safari(self):
        ua = parse_user_agent(IPHONE)
        assert ua.os == "iOS" and ua.device_type == "mobile"

    def test_bot(self):
        ua = parse_user_agent(GOOGLEBOT)
        assert ua.is_bot and ua.device_type == "bot"

    def test_empty(self):
        assert parse_user_agent("").browser == "Unknown"


class TestReferrer:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", "direct"),
            ("https://www.Google.com/search?q=x", "google.com"),
            ("https://t.co/abc", "t.co"),
            ("garbage", "direct"),
        ],
    )
    def test_parse(self, raw, expected):
        assert parse_referrer(raw) == expected


class TestGeoIP:
    def test_unknown_without_database(self):
        geoip._reader.cache_clear()
        assert geoip.lookup_country("8.8.8.8") == ("", "Unknown")

    def test_lookup_with_reader(self):
        reader = MagicMock()
        reader.country.return_value.country.iso_code = "PK"
        reader.country.return_value.country.name = "Pakistan"
        with patch.object(geoip, "_reader", return_value=reader):
            assert geoip.lookup_country("1.2.3.4") == ("PK", "Pakistan")

    def test_address_not_found_and_bad_ip(self):
        reader = MagicMock()
        reader.country.side_effect = geoip.geoip2.errors.AddressNotFoundError("nope")
        with patch.object(geoip, "_reader", return_value=reader):
            assert geoip.lookup_country("10.0.0.1") == ("", "Unknown")
            reader.country.side_effect = ValueError
            assert geoip.lookup_country("not-an-ip") == ("", "Unknown")


@pytest.mark.django_db
class TestStoreClick:
    def test_full_click_record(self, make_link):
        link = make_link()
        with patch("apps.analytics.tracking.lookup_country", return_value=("DE", "Germany")):
            click = store_click(link.id, "8.8.8.8", IPHONE, "https://news.ycombinator.com/item?id=1", now_iso())
        assert (click.country_code, click.country_name, click.device_type, click.os) == (
            "DE",
            "Germany",
            "mobile",
            "iOS",
        )
        assert click.referrer_domain == "news.ycombinator.com"
        link.refresh_from_db()
        assert link.click_count == 1

    def test_raw_ip_is_never_stored(self, make_link):
        link = make_link()
        click = store_click(link.id, "203.0.113.77", CHROME_WIN, "", now_iso())
        assert "203.0.113.77" not in " ".join(str(v) for v in vars(click).values())
        assert len(click.ip_hash) == 64

    def test_visitor_hash_stable_per_day_but_differs_across_days_and_ips(self):
        d1 = timezone.now()
        assert visitor_hash("1.1.1.1", "ua", d1) == visitor_hash("1.1.1.1", "ua", d1)
        assert visitor_hash("1.1.1.1", "ua", d1) != visitor_hash("1.1.1.2", "ua", d1)
        assert visitor_hash("1.1.1.1", "ua", d1) != visitor_hash("1.1.1.1", "ua", d1 + timedelta(days=1))

    def test_bot_clicks_stored_but_not_counted(self, make_link):
        link = make_link()
        click = store_click(link.id, "66.249.66.1", GOOGLEBOT, "", now_iso())
        assert click.is_bot
        link.refresh_from_db()
        assert link.click_count == 0

    def test_deleted_link_is_ignored(self):
        assert store_click(999999, "1.1.1.1", CHROME_WIN, "", now_iso()) is None
        assert Click.objects.count() == 0

    def test_country_hint_header_fallback(self, make_link):
        link = make_link()
        assert store_click(link.id, "1.1.1.1", CHROME_WIN, "", now_iso(), "pk").country_code == "PK"
        assert store_click(link.id, "1.1.1.1", CHROME_WIN, "", now_iso(), "XX").country_code == ""

    def test_celery_task_wrapper(self, make_link):
        link = make_link()
        record_click.delay(link.id, "1.1.1.1", CHROME_WIN, "", now_iso(), "")
        assert Click.objects.filter(link=link).count() == 1

    def test_purge_old_clicks_respects_retention(self, make_link, settings):
        settings.CLICK_RETENTION_DAYS = 30
        link = make_link()
        old = store_click(link.id, "1.1.1.1", CHROME_WIN, "", (timezone.now() - timedelta(days=60)).isoformat())
        new = store_click(link.id, "1.1.1.1", CHROME_WIN, "", now_iso())
        assert purge_old_clicks(batch_size=1) == 1
        assert list(Click.objects.values_list("pk", flat=True)) == [new.pk]
        assert not Click.objects.filter(pk=old.pk).exists()
