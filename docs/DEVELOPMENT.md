# Development

## Layout

```
backend/
  fetch_app/
    app.py              application factory, local-only guard, security headers, SPA serving
    __main__.py         command line (init, serve, run, status, stop)
    desktop.py          the desktop shell: window (pywebview), tray icon (pystray), server thread
    config.py           bootstrap configuration (fetch.toml, environment)
    settings_service.py runtime settings (validated, stored in the database)
    models.py           SQLAlchemy models        schemas.py  Pydantic schemas
    api/                routers: profile, downloads, files, providers, admin, ws; deps.py has the local-only checks
    engine/
      manager.py        the job manager: queue, scheduling, state machine, recovery
      transfer.py       native HTTP transfer with resume; hands large files to aria2
      aria2.py          aria2 process and RPC client (loopback only)
      guardproxy.py     filtering CONNECT proxy that keeps aria2 inside the SSRF rules
      http.py           HTTP client with guarded DNS resolution and manual redirects
      limiter.py        bandwidth limiter (the global speed limit)
    providers/          adapters (base, direct, pixeldrain, mega, gdrive, rapidgator) and registry
    security/           crypto (Fernet), URL guard
    storage.py          path safety, storage spaces, usage accounting
    events.py           in-process event bus for the WebSocket
    activity.py         activity log      usage.py    storage and download counters
    maintenance.py      periodic cleanup  logging_setup.py  JSON logs with redaction
  alembic/              database migrations
  tests/                pytest suite (local file server and provider emulators included)
frontend/
  src/
    lib/                API client, app bootstrap, real-time, query hooks, formatting, types
    components/ui/      design system (buttons, fields, dialogs, menus, toasts)
    components/         layout shell and download widgets
    pages/              one file per page
    test/               Vitest suite against an in-memory API
scripts/FetchCommon.ps1 helpers shared by the PowerShell scripts in the repository root
```

## How a download flows

1. `POST /api/downloads` validates each URL (scheme, SSRF rules), picks the
   adapter and stores a job in state `queued`.
2. `DownloadManager` (one asyncio task) wakes up, picks jobs by priority and
   position while respecting the concurrency limit and the storage limit, and
   starts a worker task per job.
3. The worker asks the adapter to `resolve` the link, then `Transfer` streams
   the file into `storage/temporary/<job id>/`, resuming a partial file when
   the identity of the remote file is unchanged. Large resumable files go to
   aria2 when it is available; any aria2 failure falls back to the native path.
4. Progress is published on the event bus (throttled) and persisted every few
   seconds. The storage limit is enforced while bytes arrive.
5. After an optional integrity check the file is moved into the download folder
   under a unique, sanitised name, and a `FileRecord` is written.
6. Errors become a `ProviderError` with a stable code. Temporary ones are
   retried after a delay, announced waits are waited out, everything else
   fails with the message shown in the dashboard.

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
2 GiB stream, the local-only guard (foreign hosts, DNS rebinding, foreign
origins over REST and WebSocket), migration of a database from FETCH 1.x,
and aria2 transfers through the guard proxy (those are skipped when no `aria2c`
is installed). Rapidgator, MEGA, Pixeldrain and Google Drive are tested against
emulators in `tests/emulators.py`; no test contacts an external service or
needs an account.

The frontend suite renders the real application with `fetch` replaced by an
in-memory API (`src/test/harness.tsx`) and covers the overview, submitting
and controlling downloads, the file browser, providers and settings.

`tests/test_desktop.py` covers the desktop shell with stand-ins for pywebview
and pystray: closing to the tray, quitting, the window closing when the server
stops, and falling back to the browser when no usable renderer exists.

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
  `StorageManager.resolve`, which refuses anything outside the storage space.
- FETCH has no accounts. Every request is checked by `LocalOnlyMiddleware`
  (loopback client, local `Host`) and, for changes, by `check_origin` in
  `api/deps.py`. Do not add a way to reach FETCH from another device without
  bringing back authentication first.
- No feature flags for unfinished work, and no controls that do nothing.

## Continuous integration and releases

`.github/workflows/ci.yml` runs on every pull request and on `main`: the
backend tests, the frontend type check, tests and build, a Windows job that builds
`FETCH.exe` and runs its self-test (headless, then once with its window), and a Windows job
that installs the freshly built package with `install.ps1`, starts it, checks
the dashboard, runs `update.ps1 -SkipPull`, and then stops and uninstalls it.

### The single-file executable

`scripts/build_exe.py` builds `FETCH.exe` with PyInstaller (one file, a
windowed program with the FETCH icon) from `backend/fetch_exe.py`, bundling
the built web interface, the Alembic migrations, pywebview (with pythonnet
for the WebView2 renderer) and pystray. Inside the executable `fetch_app.runtime.is_frozen()` is
true: the install folder is the folder of the executable, the web interface
and migrations are read from the bundle, and starting it without arguments
means `run`: set up if needed, start the server in a thread, and show the
dashboard in a WebView2 window with a tray icon (`fetch_app/desktop.py`).
Without the WebView2 runtime it opens the browser instead and keeps the tray
icon. Because the program is windowed, `attach_console` reconnects output to
the terminal for commands such as `status`.

```bash
pip install -r backend/requirements.txt -r backend/requirements-build.txt
(cd frontend && npm ci && npm run build)
python scripts/build_exe.py --out dist
python scripts/exe_selftest.py dist/FETCH.exe   # dist/FETCH on Linux and macOS
python scripts/exe_selftest.py dist/FETCH.exe --desktop   # Windows: the window
```

`scripts/exe_selftest.py` drives the built file end to end, including a real
self-update against a local stand-in for the GitHub releases API. CI runs it
on Windows. An update of the executable is done by the server itself
(`UpdateService.apply_executable`): download `FETCH.exe` and
`FETCH.exe.sha256` from the release, compare, rename the running file to
`FETCH.exe.old`, put the new one in place, start it with
`run --after-update <old version>` (plus `--headless` when the old one had no
window) and exit. The new process waits for the
old one, backs up the database, migrates and serves.

To publish a release:

1. Set the new version in `backend/fetch_app/__init__.py` and
   `frontend/package.json`, and add a `## [x.y.z]` section to `CHANGELOG.md`.
2. Merge that change into `main`.

`.github/workflows/release.yml` runs on every change to `main`. When the
version has no release yet it runs CI, builds `FETCH-x.y.z.zip` with
`scripts/package_release.py` (tracked program files plus the built interface
and a `frontend/dist/.prebuilt` marker), publishes it together with
`FETCH.exe` as release `vx.y.z`, each with its `.sha256` file, and the
changelog section as release notes, and finally
installs the package on Windows and lets `update.ps1` download and verify the
published release. Pushing a tag `vx.y.z` or starting the workflow by hand
(Actions > Release > Run workflow) does the same.

Installed servers find the release through `GET /repos/<repo>/releases/latest`
(`fetch_app/updates.py`), so a release marked as a draft or pre-release is not
offered as an update.
