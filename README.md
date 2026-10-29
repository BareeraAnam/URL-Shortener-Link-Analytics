# 🔗 Snipr — URL Shortener & Link Analytics

Django 5 · DRF · Redis · Celery · PostgreSQL. Production-oriented URL shortener with click analytics.

## Features
| Feature | Where |
|---|---|
| Custom / auto slugs (base62, reserved-word protection, live availability check) | `apps/links/{validators,services,serializers}.py` |
| Fast redirects (Redis lookup, 0 DB queries on a hit, negative caching) | `apps/links/{middleware,cache}.py` |
| Click tracking: browser, OS, device, country, referrer | `apps/analytics/{tracking,useragent,geoip}.py` |
| Async analytics writes (Celery, retries, **exactly-once** via a unique `click_id`) | `apps/analytics/{tasks,tracking}.py` |
| QR code per link (PNG/SVG, size, download) | `apps/links/qr.py` |
| Expiry + password protection (hashed, signed unlock cookie) | `middleware.py`, `views_public.py` |
| Dashboard + analytics API | `apps/dashboard`, `apps/analytics` |
| Bulk CSV import (validated, async, chunked progress, time limits, stuck-job recovery) | `apps/links/{bulk,tasks}.py` |
| Rate limiting (django-ratelimit, fail-open) | `apps/core/{ratelimit,throttling}.py` |
| JWT + session auth, OpenAPI docs | `/api/auth/*`, `/api/docs/` |

## Quick start (local, no Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # DJANGO_DEBUG=true
python manage.py migrate
python manage.py seed_demo      # demo / demo12345!  (optional sample data)
python manage.py runserver
# in other terminals (needs Redis running):
celery -A config worker -l info
celery -A config beat -l info
```
Without Redis the app still redirects (falls back to the DB). Without a Celery worker set `CELERY_TASK_ALWAYS_EAGER=true` in `.env` (local development only) so clicks are written inline.

GeoIP (country): `MAXMIND_LICENSE_KEY=xxx python manage.py download_geoip` (free key). Without it countries show "Unknown".

## Docker (web + worker + beat + migrate + Postgres + Redis cache + Redis broker)
```bash
cp .env.example .env && docker compose up -d --build
docker compose ps
docker compose exec web python manage.py createsuperuser
docker compose down
```
`migrate` runs once as its own service before web, worker and beat start. There are two Redis instances: `redis-cache` (allkeys-lru) for cache and rate limits, and `redis-broker` (noeviction, AOF) for Celery, so queued click and import tasks are never evicted. The web container is health-checked through `/health/`.

## API (all under `/api`, JWT `Authorization: Bearer <token>` or session)
| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register/` · `/auth/token/` · `/auth/token/refresh/` · GET `/auth/me/` | accounts |
| GET/POST | `/links/` | list (`?search=&status=active\|expired\|disabled&ordering=`) / create |
| GET/PATCH/DELETE | `/links/{id}/` | slug is immutable |
| GET | `/links/{id}/qr/?type=png\|svg&size=10&border=4&download=1` | QR code |
| GET | `/links/check-slug/?slug=abc` | availability |
| POST | `/links/bulk-import/` (multipart `file`) → 202 · GET `/links/bulk-import/{job}/` | CSV import |
| GET | `/links/{id}/analytics/?days=30&include_bots=false` | per-link analytics (`daily_unique_visitors`, see below) |
| GET | `/links/{id}/clicks/` | raw click log |
| GET | `/analytics/overview/` | account-wide analytics |

CSV columns: `original_url` (alias `url`), `slug`, `title`, `expires_at` (ISO-8601), `password`, `is_active`.

## Testing & quality
```bash
pytest                       # unit tests: SQLite + in-memory cache + eager Celery (no services needed)
ruff check . && ruff format --check .

# integration tests: REAL PostgreSQL + REAL Redis (needs both running)
RUN_INTEGRATION=1 DATABASE_URL=postgres://snipr:snipr@localhost:5432/snipr REDIS_URL=redis://localhost:6379/5 \
  pytest tests/integration --ds=config.settings.integration --no-cov
```
`pytest` prints the exact number of test cases (parametrised tests count once per case, so it is higher than the number of `def test_` functions).
The integration suite checks what mocks cannot: Redis keys really appear, "Redis down" falls back to the database, the rate limiter fails open, Postgres `TruncDate`/`distinct` aggregation, the case-insensitive unique index, exactly-once click delivery and a bulk import using the real (slow) PBKDF2 hasher.
CI (`.github/workflows/ci.yml`) runs two jobs: lint + format + migration check + unit tests with a coverage gate, and the integration suite on Postgres 16 + Redis 7 service containers.

## Design decisions
* **Middleware redirect** runs before sessions/auth/CSRF -> minimal latency; reserved paths (`/api`, `/admin`...) are never captured.
* **302, not 301** so browsers don't cache the redirect and every click is counted; links stay editable.
* **Privacy**: raw IPs are never stored, only a salted per-day SHA-256. Because the salt changes daily, the dashboard reports **daily uniques** (unique visitors per day, summed): a person who returns on another day is counted again. Per-day values in the time series are exact. A retention job purges old clicks.
* **Exactly-once clicks**: every redirect carries a `click_id` (UUID). The counter increment and the `Click` insert share one transaction and `click_id` is unique, so a redelivered Celery message is dropped and rolled back instead of double counting.
* **Cache invalidation also covers `QuerySet.update()`/`bulk_update()`** (e.g. an admin action disabling a phishing link takes effect immediately); counter-only updates skip invalidation so the click worker stays cheap.
* **Bulk import safety**: each distinct password is hashed once per job, progress is checkpointed every N rows, a soft time limit marks the job failed (keeping the partial result), a beat task fails jobs stuck in `processing`/`pending`, CSV plaintext is wiped once processed, imports are capped per account (`MAX_LINKS_PER_USER`) and by distinct passwords.
* **Slugs are case-insensitive for uniqueness** (`/Promo` and `/promo` can never be two links; DB-level `lower(slug)` unique index), while redirect lookup stays exact.
* **Login brute-force protection** on the HTML login page (per IP and per username), plus register and the JWT endpoints.
* **Configuration is fail-closed**: production (`DJANGO_DEBUG=false`) requires `DJANGO_SECRET_KEY` and `DATABASE_URL`; `X-Forwarded-Proto` is only trusted when `NUM_PROXIES > 0`.
* **Cache invalidation**: save/delete signals delete the key immediately and again on commit (closes the read-race).
* **Resilience**: Redis down -> DB fallback; broker down -> redirect still works; limiter errors -> fail open.
* **Abuse protection**: only http(s) destinations, no credentials, no localhost/private/reserved IPs (including obfuscated forms like `2130706433`, `0x7f000001`, `127.1`, IPv4-mapped IPv6), own-domain/blocklist (loop and phishing guard), per-IP/user rate limits, brute-force limit on unlock.
* **Admin passwords are hashed**: a plain password typed into the link admin's password box is hashed on save (the field stores a hash, and a plain value could never be unlocked).
* **Scaling notes**: nginx/CDN in front (`NUM_PROXIES`), PgBouncer, partition `Click` by month at very large scale, optionally batch `click_count` updates via Redis `INCR` + periodic flush.

Rate limits use `django-ratelimit` fixed windows, so a burst that straddles a window boundary can briefly pass up to roughly twice the configured rate.

## Upgrading an existing database
Two small, additive migrations (`analytics.0002_click_click_id`, `links.0002_link_link_slug_ci_unique`). Your data is kept:
```bash
cp db.sqlite3 db.sqlite3.bak     # optional safety copy
python manage.py migrate
```
If two existing slugs differ only by case (e.g. `Promo` / `promo`), `migrate` stops with a message naming them; delete or rename one of each pair and run it again.

## Known limitations
* Real `QuerySet.delete()` of many links fires one cache invalidation per row (acceptable at this scale).
* Unique visitors cannot be counted across days without storing a stable identifier, which this project deliberately avoids.
* **Login lockout is a deliberate trade-off.** Login attempts are limited per IP and per username (`LOGIN_RATE_LIMIT`, default 10/m), so anyone can lock a known username out for up to a minute by sending wrong passwords. This is accepted as a mild, self-expiring cost of brute-force protection.
* **`MAX_LINKS_PER_USER` is a soft cap.** The check counts existing links and then inserts, so parallel requests can exceed it slightly. Do not treat it as a hard quota.
* **Shared password hashes in bulk imports.** Links in one import that share a password get an identical hash (including the salt), because each distinct password is hashed once per job. This only reveals which links share a password, and only to someone who already has database access.