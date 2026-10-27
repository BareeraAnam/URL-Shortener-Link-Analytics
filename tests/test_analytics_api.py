from datetime import timedelta

import pytest
from django.utils import timezone

from apps.analytics.models import Click


def add_clicks(
    link, n, *, days_ago=0, browser="Chrome", os="Windows", country="Pakistan", ref="direct", bot=False, hash_prefix="h"
):
    when = timezone.now() - timedelta(days=days_ago)
    Click.objects.bulk_create(
        Click(
            link=link,
            clicked_at=when,
            browser=browser,
            os=os,
            device_type="desktop",
            country_code=country[:2].upper(),
            country_name=country,
            referrer_domain=ref,
            is_bot=bot,
            ip_hash=f"{hash_prefix}{i}",
        )
        for i in range(n)
    )


@pytest.mark.django_db
class TestLinkAnalytics:
    def test_totals_series_and_breakdowns(self, api, make_link):
        link = make_link()
        add_clicks(link, 3, browser="Chrome", country="Pakistan", ref="twitter.com")
        add_clicks(link, 2, days_ago=2, browser="Firefox", os="Linux", country="Germany", ref="direct", hash_prefix="z")
        r = api.get(f"/api/links/{link.id}/analytics/?days=7")
        assert r.status_code == 200
        d = r.data
        assert d["total_clicks"] == 5 and d["daily_unique_visitors"] == 5
        assert len(d["timeseries"]) == 7
        assert d["timeseries"][-1]["clicks"] == 3 and d["timeseries"][-3]["clicks"] == 2
        assert sum(x["clicks"] for x in d["timeseries"]) == 5
        assert d["browsers"][0] == {"label": "Chrome", "clicks": 3}
        assert {x["label"] for x in d["operating_systems"]} == {"Windows", "Linux"}
        assert d["countries"][0]["label"] == "Pakistan"
        assert d["referrers"][0] == {"label": "twitter.com", "clicks": 3}
        assert d["link"]["slug"] == link.slug

    def test_daily_uniques_deduplicate_within_a_day(self, api, make_link):
        link = make_link()
        add_clicks(link, 1)
        add_clicks(link, 1)  # same ip_hash "h0"
        assert api.get(f"/api/links/{link.id}/analytics/").data["daily_unique_visitors"] == 1

    def test_bots_excluded_by_default(self, api, make_link):
        link = make_link()
        add_clicks(link, 2)
        add_clicks(link, 5, bot=True, hash_prefix="bot")
        assert api.get(f"/api/links/{link.id}/analytics/").data["total_clicks"] == 2
        assert api.get(f"/api/links/{link.id}/analytics/?include_bots=true").data["total_clicks"] == 7

    def test_days_window_excludes_old_clicks(self, api, make_link):
        link = make_link()
        add_clicks(link, 2, days_ago=40)
        assert api.get(f"/api/links/{link.id}/analytics/?days=30").data["total_clicks"] == 0
        assert api.get(f"/api/links/{link.id}/analytics/?days=60").data["total_clicks"] == 2

    @pytest.mark.parametrize("days", ["0", "400", "abc"])
    def test_invalid_days(self, api, make_link, days):
        link = make_link()
        assert api.get(f"/api/links/{link.id}/analytics/?days={days}").status_code == 400

    def test_foreign_link_404(self, api, make_link, other_user):
        assert api.get(f"/api/links/{make_link(owner=other_user).id}/analytics/").status_code == 404

    def test_requires_auth(self, anon_api, make_link):
        assert anon_api.get(f"/api/links/{make_link().id}/analytics/").status_code == 401

    def test_click_log_paginated_and_scoped(self, api, make_link, other_user):
        link = make_link()
        add_clicks(link, 25)
        r = api.get(f"/api/links/{link.id}/clicks/")
        assert r.data["count"] == 25 and len(r.data["results"]) == 20
        assert "ip_hash" not in r.data["results"][0]
        assert api.get(f"/api/links/{make_link(owner=other_user).id}/clicks/").status_code == 404

    def test_overview(self, api, make_link, other_user):
        a, b = make_link(), make_link()
        add_clicks(a, 4)
        add_clicks(b, 1, hash_prefix="q")
        add_clicks(make_link(owner=other_user), 50, hash_prefix="o")
        make_link(is_active=False)
        r = api.get("/api/analytics/overview/?days=30")
        assert r.status_code == 200
        assert r.data["total_clicks"] == 5
        assert r.data["top_links"][0]["slug"] == a.slug and r.data["top_links"][0]["clicks"] == 4
        assert r.data["links"] == {"total": 3, "active": 2}

    def test_results_cached_when_enabled(self, api, make_link, settings):
        settings.ANALYTICS_CACHE_TTL = 60
        link = make_link()
        add_clicks(link, 1)
        assert api.get(f"/api/links/{link.id}/analytics/").data["total_clicks"] == 1
        add_clicks(link, 1, hash_prefix="n")
        assert api.get(f"/api/links/{link.id}/analytics/").data["total_clicks"] == 1  # served from cache
