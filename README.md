# ScoutWired Submission & Voting Portal

Self-hosted FastAPI portal: proposals, moderation, polls, Discord OAuth + guest access codes, Power Automate/Discord notifications, audit log, Docker deployment.

## Quick start (local)

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # edit SECRET_KEY etc.
.venv/bin/python -m alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Docker

```sh
cp .env.example .env   # fill secrets
docker compose up --build -d
curl http://127.0.0.1:8000/health
docker compose logs -f portal
```

Migrations run automatically at container startup (`scripts/migrate.py`).
Data persists in the `portal-data` volume (`/srv/app/data`).

## Bootstrap first admin

Option A (recommended): set `BOOTSTRAP_TOKEN` + have the admin log in via Discord once, then:

```sh
BOOTSTRAP_TOKEN=... python scripts/bootstrap_admin.py --discord-id <DISCORD_USER_ID> --token ...
```

Option B: set `BOOTSTRAP_DISCORD_ID` env to the admin's Discord user id — that user is treated as admin until role config grants admin to a role. Clear the token/flag afterwards; the script prints a reminder. Further admins come from the **Admin → Roles** page (no rebuild needed).

## Discord setup

1. https://discord.com/developers/applications → New Application → OAuth2: add redirect `https://YOUR-DOMAIN/login/discord/callback`, scope `identify`.
2. Bot tab → New bot, copy token → `DISCORD_BOT_TOKEN`; enable **Server Members Intent**; invite bot with `bot` scope (no special perms needed beyond read members/roles).
3. Set `DISCORD_GUILD_ID`, `DISCORD_CLIENT_ID/SECRET`, `DISCORD_REDIRECT_URI`.
4. In Admin → Roles, paste numeric role IDs per category, Save (guild existence is verified live; permission checks fail closed when the API is unreachable). Test button checks bot connectivity (never shows secrets).

## Guest accounts

Admin → Guests → Generate. Username (`Guest-XXXXXX`) + 16-char code shown once with copy buttons. Revoke bumps a session epoch (kills existing sessions promptly + blocks login); Regenerate rotates the code; expiry dates enforced server-side. Login at `/login/guest` has rate limiting (5/5min → 10min lockout) + per-account lockout (10 fails → 15min).

## Power Automate

Create a Flow with **When a HTTP request is received** trigger; paste its URL into `POWER_AUTOMATE_URL` and a signing secret into `POWER_AUTOMATE_SECRET` (sent as `X-SW-Signature` HMAC-SHA256, event id in `X-SW-Event-Id`). Events: submission_needs_review/approved/rejected/changes_requested/published, poll_opened/closed, guest_created/revoked. Payloads include real ids/titles/statuses/admin URLs — never secrets or codes. Delivery log + bounded retries (3) + dedup keys under Admin → Integrations; failures never change moderation/voting outcomes.

## Discord notifications

Set `DISCORD_WEBHOOK_MODERATION/PUBLIC/SECURITY` webhook URLs (Admin → Integrations shows configured/not only). Separate destinations per event class; no codes/notes/secrets included.

## Backup / restore / upgrade

```sh
./scripts/backup.sh ./data/portal.db            # sqlite .backup (safe online)
./scripts/restore.sh ./backups/<f>.db ./data/portal.db
.venv/bin/python -m alembic upgrade head        # upgrades; PostgreSQL path: point DATABASE_URL at postgres, run same command
```

PostgreSQL migration path: set `DATABASE_URL=postgresql+psycopg2://...`, install `psycopg2-binary`, run `alembic upgrade head`. SQLite `check_same_thread` handling is automatic.

## Reverse proxy / Cloudflare Tunnel (no inbound ports)

Bind is localhost-only by default (`127.0.0.1:8000`). Put Caddy/Nginx in front with TLS, or `cloudflared tunnel --url http://127.0.0.1:8000`. Set `APP_URL` to the canonical public URL and `SECURE_COOKIES=true`.

## Tests

```sh
.venv/bin/python -m pytest tests/ -q   # 21 tests, mocked Discord/Power Automate
```

Mocked tests prove app logic, not live connectivity — verify Discord/Power Automate with the Admin → Roles test button + Integrations retry after configuring real credentials.

## Project structure

`app/main.py` entry · `config.py` env · `database.py` · `models.py` (users, guests, sessions, roles, submissions, revisions, decisions, polls, options, votes, audit, webhook events, deliveries, settings, oauth states) · `security.py` · `auth_sessions/discord/guest.py` · `permissions.py` · `services_polls.py` · `integrations.py` · `routes_*.py` · `deps.py` · `templates/` · `static/` · `migrations/` · `tests/` · `scripts/` · `Dockerfile` `compose.yaml`.

## Limitations (need real credentials to verify live)

Live Discord OAuth/guild checks, bot delivery, and Power Automate flows are implemented but only exercised with mocks here — configure real credentials and use the in-app test/retry buttons before relying on them.
