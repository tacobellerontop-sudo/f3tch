# Development

## Layout

```
backend/
  fetch_app/
    app.py              application factory, security headers, SPA serving
    __main__.py         command line (init, serve, status, stop, create-admin)
    config.py           bootstrap configuration (fetch.toml, environment)
    settings_service.py runtime settings (validated, stored in the database)
    models.py           SQLAlchemy models        schemas.py  Pydantic schemas
    api/                routers: auth, downloads, files, providers, admin, ws
    engine/
      manager.py        the job manager: queue, scheduling, state machine, recovery
      transfer.py       native HTTP transfer with resume; hands large files to aria2
      aria2.py          aria2 process and RPC client (loopback only)
      guardproxy.py     filtering CONNECT proxy that keeps aria2 inside the SSRF rules
      http.py           HTTP client with guarded DNS resolution and manual redirects
      limiter.py        per-user bandwidth limiter
    providers/          adapters (base, direct, pixeldrain, mega, gdrive, rapidgator) and registry
    security/           passwords (Argon2id), crypto (Fernet), rate limiting, URL guard
    storage.py          path safety, per-user spaces, usage accounting
    events.py           in-process event bus with per-connection authorization
    activity.py         audit log         quotas.py   quota checks
    maintenance.py      periodic cleanup  logging_setup.py  JSON logs with redaction
  alembic/              database migrations
  tests/                pytest suite (local file server and provider emulators included)
frontend/
  src/
    lib/                API client, auth, real-time, query hooks, formatting, types
    components/ui/      design system (buttons, fields, dialogs, menus, toasts)
    components/         layout shell and download widgets
    pages/              one file per page
    test/               Vitest suite against an in-memory API
scripts/FetchCommon.ps1 helpers shared by the PowerShell scripts in the repository root
```

## How a download flows

1. `POST /api/downloads` validates each URL (scheme, SSRF rules), picks the
   adapter, checks the user's quotas and stores a job in state `queued`.
2. `DownloadManager` (one asyncio task) wakes up, picks jobs by priority and
   position while respecting the global and per-user concurrency limits, and
   starts a worker task per job.
3. The worker asks the adapter to `resolve` the link, then `Transfer` streams
   the file into `storage/temporary/<job id>/`, resuming a partial file when
   the identity of the remote file is unchanged. Large resumable files go to
   aria2 when it is available; any aria2 failure falls back to the native path.
4. Progress is published on the event bus (throttled) and persisted every few
   seconds. Size and storage quotas are enforced while bytes arrive.
5. After an optional integrity check the file is moved into the user's folder
   under a unique, sanitised name, and a `FileRecord` is written.
6. Errors become a `ProviderError` with a stable code. Temporary ones are
   retried after a delay, announced waits are waited out, everything else
   fails with the message the user sees.

On startup every job that was mid-flight is put back in the queue; on shutdown
running jobs are stopped with their partial data kept.

## Running from source

Backend (Python 3.12+):

```bash
cd backend
python -m venv ../.venv
../.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # Windows: ..\.venv\Scripts\pip
../.venv/bin/python -m fetch_app init
../.venv/bin/python -m fetch_app serve
```

Frontend (Node 20+), with hot reload and the API proxied to the backend:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to http://127.0.0.1:8787
```

Set `FETCH_BACKEND` to proxy to another address. For a production-style run,
`npm run build` and open the backend's own address; it serves `frontend/dist`.

## Tests and checks

```bash
cd backend && python -m pytest tests -q          # about two minutes
cd frontend && npm run typecheck && npm test && npm run build
```

The backend suite starts the real application against a temporary folder and
real local HTTP servers: downloads are actual transfers, including resume
after interruption, restart recovery, redirects into private addresses, a
2 GiB stream, quota enforcement, cross-user isolation over REST and WebSocket,
and aria2 transfers through the guard proxy (those are skipped when no `aria2c`
is installed). Rapidgator, MEGA, Pixeldrain and Google Drive are tested against
emulators in `tests/emulators.py`; no test contacts an external service or
needs an account.

The frontend suite renders the real application with `fetch` replaced by an
in-memory API (`src/test/harness.tsx`) and covers sign-in and setup, the
overview, submitting and controlling downloads, the file browser, and the
administration pages.

## Database migrations

The schema is managed by Alembic and upgraded automatically by `init`,
`status` and at server start.

```bash
cd backend
../.venv/bin/alembic revision --autogenerate -m "describe the change"
```

Review the generated file in `alembic/versions/` before committing. SQLite
needs batch mode for most `ALTER` operations; `env.py` enables it.

## Conventions

- API errors go through `ApiError(status, code, message)`; messages are shown
  to users, so write them for users.
- Never log secrets. `logging_setup.py` redacts common fields, but do not rely
  on it: keep credentials out of log calls.
- Outbound requests always use `ctx.http` (guarded). Never call `aiohttp` or
  `urllib` directly for user-supplied URLs.
- Every file path that comes from a request goes through
  `StorageManager.resolve`, which refuses anything outside the caller's space.
- Authorization lives in the routers (`CurrentUser`, `AdminUser`, `_own`) and
  in the event bus audience rules; the frontend only hides what the server
  would refuse anyway.
- No feature flags for unfinished work, and no controls that do nothing.
