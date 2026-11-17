# Configuration reference

FETCH has two layers of configuration:

1. **Bootstrap configuration** in `config/fetch.toml` (plus environment
   variables). It holds what is needed before the database can be opened:
   paths, the initial port, and secrets.
2. **Runtime settings** stored in the database and edited in the dashboard
   under **Settings**. Most take effect immediately; a few need a restart and
   the dashboard says so.

## `config/fetch.toml`

Created by `install.ps1` (or `python -m fetch_app init`) with freshly generated
secrets. It is never overwritten. An annotated example is in
[`config/fetch.example.toml`](../config/fetch.example.toml).

| Key | Environment variable | Default | Meaning |
|---|---|---|---|
| `paths.data_dir` | `FETCH_DATA_DIR` | `data` | Database, pid file, backups |
| `paths.log_dir` | `FETCH_LOG_DIR` | `logs` | Log files |
| `paths.storage_root` | `FETCH_STORAGE_ROOT` | `storage` | Downloads, unless overridden in Settings > Storage |
| `paths.frontend_dist` | `FETCH_FRONTEND_DIST` | `frontend/dist` | Built web interface |
| `server.port` | `FETCH_PORT` | `8787` | Listen port, unless overridden in Settings > Advanced. FETCH always listens on `127.0.0.1` |
| `server.log_level` | `FETCH_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `security.secret_key` | `FETCH_SECRET_KEY` | generated | Fernet key that encrypts provider credentials |
| `security.ssrf_allowlist` | `FETCH_SSRF_ALLOWLIST` (comma separated) | `[]` | Private hosts or CIDR ranges downloads may reach |
| `updates.repository` | `FETCH_UPDATE_REPO` | `tacobellerontop-sudo/f3tch` | GitHub repository (`owner/name`) whose releases are offered as updates. Empty disables update checks |
| `updates.api_url` | `FETCH_UPDATE_API` | `https://api.github.com` | GitHub API address used for the check |

Other variables: `FETCH_HOME` (the install folder; defaults to the folder that
contains `backend/`), `FETCH_CONFIG` (path of the configuration file) and
`FETCH_DESKTOP` (`0` makes `FETCH.exe` open the dashboard in the browser
instead of its own window).

Older files may still contain `server.bind_address`, `security.setup_token`
or `security.cookie_secure`. They are ignored.

Relative paths are resolved against the install folder. An environment
variable wins over the file.

**`secret_key`.** Losing or changing it makes stored provider credentials
unreadable; they then have to be entered again. Nothing else depends on it.

**`ssrf_allowlist`.** By default FETCH refuses to download from loopback,
private (`10/8`, `172.16/12`, `192.168/16`), link-local, carrier-grade NAT and
other non-public addresses, including when a public URL redirects to one. Add
a host or range here only if you really want to download from it, for
example a NAS on your LAN.

## Runtime settings

Zero means "no limit" for every limit below unless noted.

### General

| Setting | Default | Meaning |
|---|---|---|
| Application name | `FETCH` | Shown in the sidebar and the window title |
| Theme | Dark | Dark, light or follow Windows |
| Language | English | Only English is included |
| Keep running when the window is closed | On | Closing the window leaves FETCH in the notification area so downloads continue. Off: closing the window quits FETCH |
| Check for updates automatically | On | Ask GitHub for the latest release a few times a day (Settings > Updates). Updates are only installed when you confirm |

### Downloads

| Setting | Default | Meaning |
|---|---|---|
| Simultaneous downloads | 3 | 1 to 32 |
| Speed limit | 0 | For all downloads together, in KB/s |
| Automatic retries | 3 | For temporary errors (network, server busy). Permanent errors are not retried |
| Retry delay | 30 s | Wait before each automatic retry |
| Default priority | Normal | Low, normal, high or urgent |
| Use aria2 for large files | On | Restart required |
| aria2 path | empty | Empty: `tools\aria2\aria2c.exe`, then `PATH`. Restart required |
| Use aria2 from | 16 MB | Smaller files use the built-in engine |
| Connections per download | 4 | 1 to 16 |

aria2 is only used for resumable files of known size from providers that allow
it; everything else, and any transfer aria2 fails, uses the built-in engine.

### Storage

| Setting | Default | Meaning |
|---|---|---|
| Storage location | empty | Empty uses `paths.storage_root`. Restart required; existing files are not moved |
| Total storage limit | 0 | In GB. New downloads wait while the limit is reached |
| Clean up temporary files after | 72 h | Leftover partial files of removed jobs |
| Delete completed files after | 0 days | 0 keeps files until you delete them |
| Keep history for | 0 days | Finished history entries and activity; 0 keeps everything |

### Advanced

| Setting | Default | Meaning |
|---|---|---|
| Port | 0 | 0 uses `server.port`. Restart required |

## Command line

Run from the `backend` folder with the virtual environment's Python:

```powershell
..\.venv\Scripts\python.exe -m fetch_app <command>
```

| Command | Purpose |
|---|---|
| `init [--port N]` | Create the configuration (if missing), folders and database; apply migrations |
| `serve [--port N]` | Run the server in the foreground (no window) |
| `run [--headless]` | What double-clicking `FETCH.exe` does: set up if needed, start the server and show the window with a tray icon. `--headless` starts only the server |
| `status` | Print version, address, port, whether it is running and how its interface is shown (JSON) |
| `stop` | Ask the running server to shut down cleanly (this also closes its window) |

`FETCH.exe` takes the same commands, for example `.\FETCH.exe status`.
