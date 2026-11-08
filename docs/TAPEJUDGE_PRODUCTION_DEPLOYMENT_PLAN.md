# TapeJudge — Production Deployment Plan

Research date **2026-11-07**. Companion to `TAPEJUDGE_RELEASE_READINESS.md`.
This plan assumes the release verdict: **a public release of the full surface is
now permitted** — authentication and object-level authorisation are implemented
(Mission 6) — conditional on the controls below.

It describes a single-instance deployment that matches the application's actual
architecture (Flask + SQLite + read-only X Layer access). It does not introduce
infrastructure the app does not need.

---

## 1. Topology

```
        ┌────────────────────────────────────────────┐
Client ─┤ Reverse proxy (TLS termination, HTTP→HTTPS) │
        │  - forwards to the app on 127.0.0.1:8080    │
        │  - sets X-Forwarded-For (for rate limiting) │
        └───────────────┬────────────────────────────┘
                        │
             ┌──────────▼──────────┐
             │  TapeJudge (Flask)  │   one process, one worker
             │  gunicorn -w 1      │   (SQLite is single-writer)
             └──────┬────────┬─────┘
                    │        │
        ┌───────────▼──┐  ┌──▼───────────────────────┐
        │ tapejudge.db │  │ X Layer RPC (eth_call)    │
        │ (SQLite,WAL) │  │ https://rpc.xlayer.tech   │
        └──────────────┘  └───────────────────────────┘
```

**One worker.** SQLite is a single-writer store; the app opens one connection per
process. Running multiple gunicorn workers against one SQLite file is *safe* for
reads but creates write contention; the honest configuration is one worker until
a shared store is adopted (§6).

## 2. Environment

| Variable | Value in production | Why |
|---|---|---|
| `TAPEJUDGE_DB` | `/var/lib/tapejudge/tapejudge.db` | A file, so data survives a restart |
| `TAPEJUDGE_SECRET` | a long random value (32+ bytes) | Flask session/flash signing; the app refuses a fixed default |
| `TAPEJUDGE_HOST` | `127.0.0.1` | Bind to loopback; the proxy is the only ingress |
| `TAPEJUDGE_PORT` | `8080` | Behind the proxy |
| `TAPEJUDGE_SKIP_CHAIN` | unset | Live chain reads are wanted in production |

Do **not** set `TAPEJUDGE_INSECURE_COOKIES=1` or `TAPEJUDGE_ALLOW_DEV_SECRET=1`
in production.

Generate the secret once and store it in the platform's secret manager:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 3. Reverse proxy (sketch)

```nginx
server {
    listen 443 ssl http2;
    server_name tapejudge.example;
    ssl_certificate     /etc/ssl/tapejudge/fullchain.pem;
    ssl_certificate_key /etc/ssl/tapejudge/privkey.pem;

    client_max_body_size 300k;   # the app caps bodies at 256 KiB

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
server { listen 80; server_name tapejudge.example; return 301 https://$host$request_uri; }
```

The app sets its own security headers (`X-Frame-Options`, CSP, `nosniff`,
`Referrer-Policy`), so the proxy does not need to duplicate them — but it must
not strip them.

## 4. Running it

```bash
pip install -e .            # or: pip install flask
TAPEJUDGE_DB=/var/lib/tapejudge/tapejudge.db \
TAPEJUDGE_SECRET=<from secret manager> \
TAPEJUDGE_HOST=127.0.0.1 TAPEJUDGE_PORT=8080 \
gunicorn -w 1 -b 127.0.0.1:8080 "tapejudge.web.app:app"
```

A systemd unit (sketch):

```ini
[Unit]
Description=TapeJudge
After=network-online.target

[Service]
User=tapejudge
WorkingDirectory=/opt/tapejudge
EnvironmentFile=/etc/tapejudge/env
ExecStart=/opt/tapejudge/.venv/bin/gunicorn -w 1 -b 127.0.0.1:8080 tapejudge.web.app:app
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

## 5. Data, backup, durability

Two interchangeable stores back the app (selected by configuration):

- **SQLite** (default) — one file; durable on a single host with a persistent
  disk. Back it up as below.
- **Postgres** (`TAPEJUDGE_DATABASE_URL`) — the durable option for a host with
  no persistent filesystem (a serverless platform) or for more than one
  instance. The driver ships with the package (`psycopg`); `pip install -e
  '.[server]'` is still accepted as an alias. Migrations run on first use; the
  schema is created if absent. On a serverless host also set
  `TAPEJUDGE_DB_SCHEMA` so the schema is stable across instances.

The store of *inputs* is the record of the competition, so it must be durable.

- **Backup** the SQLite file (and its `-wal`/`-shm` companions) on a schedule.
  A safe online backup: `sqlite3 tapejudge.db ".backup /backups/tapejudge-$(date +%F).db"`.
- **WAL mode** is enabled by the app; keep the `-wal` file with the DB.
- **Integrity check** after any restore: `sqlite3 tapejudge.db "PRAGMA integrity_check;"`.
- The database is the store of *inputs*; losing it loses the competition, so a
  backup is not optional for a real event.

## 6. Scaling beyond one instance (only if needed)

The application has no per-user server session; every handler is a function of
its request. To run more than one instance:

1. **Set `TAPEJUDGE_DATABASE_URL` to a Postgres DSN.** The shared store is
   already implemented (`tapejudge/store_pg.py`, behind the same `Store`
   interface) and exercised by the same behavioural contract as SQLite
   (`tests/test_store_contract.py`, run with `TAPEJUDGE_TEST_DATABASE_URL` set).
   `make_store()` selects it automatically from the DSN.
2. Replace the in-process `RateLimiter` with a shared one (e.g. a Redis counter
   or a proxy-level limiter), because the current limiter's state is per process.
3. Keep the read-only X Layer access as-is (it is stateless).

Until then, **one instance** is the correct deployment.

## 7. Observability

- **Access log**: keep it; it is the record of who hit the write routes.
- **5xx alert**: the app returns a clean 500 page and logs the traceback; alert on
  any 5xx.
- **Health check**: `GET /api/health` → `{"ok": true, "chain_reachable": …}`. The
  `chain_reachable` field is honest: `false` means the X Layer RPC did not answer
  and the app has degraded to "unknown", not that the app is down.

## 8. Hardening checklist before going live

- [ ] `TAPEJUDGE_SECRET` set to a strong random value.
- [ ] TLS terminated by the proxy; HTTP redirects to HTTPS.
- [ ] The app binds to `127.0.0.1` only.
- [ ] `TAPEJUDGE_INSECURE_COOKIES` and `TAPEJUDGE_ALLOW_DEV_SECRET` are **unset**.
- [ ] The SQLite file lives on a persistent volume with a backup job.
- [ ] The rate limiter is enabled (it is, unless `TAPEJUDGE_DISABLE_RATELIMIT=1`).
- [ ] A 5xx alert exists.
- [x] **Authentication and object-level authorisation** are implemented
      (Mission 6): every write requires a session + CSRF token, judges are bound
      to a membership, organiser actions are object-scoped. For a multi-instance
      deploy, add a shared store and a shared rate limiter (see §2).

## 9. Rollback

The app is stateless apart from the database. A rollback is: deploy the previous
code and point it at the same (or a restored) database. Schema changes so far are
additive (`SCHEMA_VERSION = 1`); a forward migration must stay compatible with
the previous version, or a rollback needs the matching DB snapshot.
