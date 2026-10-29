# Changelog

## v2 - review fixes (all verified by tests; see tests/test_review_fixes.py and tests/integration)

Bugs / correctness
1. Bulk import no longer hangs in `processing`: one PBKDF2 hash per distinct password, chunked progress saves, soft time limit that fails the job and keeps partial results, beat task `links.fail_stuck_import_jobs`, cap on distinct passwords, CSV plaintext wiped after processing (also for stuck/failed jobs).
2. "Unique visitors (30d)" renamed to **daily uniques** (`daily_unique_visitors`); per-day numbers are exact.
3. HTML login page is rate limited per IP and per username.
4. `QuerySet.update()` / `bulk_update()` invalidate the redirect cache; admin actions "Disable/Enable selected links" added.
5. Overview `top_links` use the same time window as totals and the daily series.
6. Click delivery is exactly-once (`Click.click_id` unique + single transaction) instead of "idempotent" by claim.

Security
7. Numeric / obfuscated / IPv4-mapped / CGNAT / link-local hosts are rejected.
8. `SECURE_PROXY_SSL_HEADER` only when `NUM_PROXIES > 0`.
9. `DATABASE_URL` is required when `DJANGO_DEBUG=false` (SQLite fallback only in debug).
10. `MAX_LINKS_PER_USER` cap (default 10000) applies to API and bulk import.
11. Chart.js 4.4.1 is bundled (no third-party CDN, nothing to pin with SRI).

Maintainability / testing
* Test that fails when a new top-level route is not in `RESERVED_SLUGS`; `seed_demo` no longer uses the reserved slug `docs`.
* Dashboard pagination + search; all UI text and comments in English.
* Case-insensitive slug uniqueness (DB `lower(slug)` index + friendly validation).
* CI: `ruff format --check`, migration check, and a Postgres 16 + Redis 7 integration job.

Migrations to run on an existing database: `analytics.0002_click_click_id`, `links.0002_link_link_slug_ci_unique` (data is preserved).

## v2.1 - follow-ups

Deployment
12. Redis is split: `redis-cache` (allkeys-lru) for cache and rate limits, `redis-broker` (noeviction, AOF) for Celery, so queued click and import tasks are never evicted.
13. `migrate` runs as its own one-shot compose service; web, worker and beat wait for it (no migration race with several replicas).
14. The web container has a healthcheck that calls `/health/` (200 when the cache probe works, 503 otherwise).
Admin
15. The link admin hashes a plain password typed into the password box (it used to be stored as plain text and could never be unlocked).

Docs
* "Known limitations" now lists the login lockout trade-off, the soft `MAX_LINKS_PER_USER` cap and shared hashes in bulk imports.
