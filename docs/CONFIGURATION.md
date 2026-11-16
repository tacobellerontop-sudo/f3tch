# Configuration reference

FETCH has two layers of configuration:

1. **Bootstrap configuration** in `config/fetch.toml` (plus environment
   variables). It holds what is needed before the database can be opened:
   paths, the initial listen address, and secrets.
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
| `server.bind_address` | `FETCH_BIND` | `127.0.0.1` | Listen address, unless overridden in Settings > Network |
| `server.port` | `FETCH_PORT` | `8787` | Listen port, unless overridden in Settings > Network |
| `server.log_level` | `FETCH_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `security.secret_key` | `FETCH_SECRET_KEY` | generated | Fernet key that encrypts provider credentials |
| `security.setup_token` | `FETCH_SETUP_TOKEN` | generated | Needed once to create the first administrator in the browser |
| `security.ssrf_allowlist` | `FETCH_SSRF_ALLOWLIST` (comma separated) | `[]` | Private hosts or CIDR ranges downloads may reach |
| `security.cookie_secure` | `FETCH_COOKIE_SECURE` | `auto` | `auto`, `true` or `false` |
| `updates.repository` | `FETCH_UPDATE_REPO` | `tacobellerontop-sudo/f3tch` | GitHub repository (`owner/name`) whose releases are offered as updates. Empty disables update checks |
| `updates.api_url` | `FETCH_UPDATE_API` | `https://api.github.com` | GitHub API address used for the check |

Other variables: `FETCH_HOME` (the install folder; defaults to the folder that
contains `backend/`) and `FETCH_CONFIG` (path of the configuration file).

Relative paths are resolved against the install folder. An environment
variable wins over the file.

**`secret_key`.** Losing or changing it makes stored provider credentials
unreadable; they then have to be entered again. Nothing else depends on it.

**`ssrf_allowlist`.** By default FETCH refuses to download from loopback,
private (`10/8`, `172.16/12`, `192.168/16`), link-local, carrier-grade NAT and
other non-public addresses, including when a public URL redirects to one. Add
a host or range here only if you really want users to download from it, for
example a NAS on your LAN. Everyone who can add downloads can then reach it.

**`cookie_secure`.** With `auto` the session cookie is marked `Secure` when the
request arrived over HTTPS (directly, or according to `X-Forwarded-Proto` from
a trusted proxy). Use `true` to force it.

## Runtime settings

Zero means "no limit" for every limit below unless noted.

### General

| Setting | Default | Meaning |
|---|---|---|
| Application name | `FETCH` | Shown in the sidebar and the browser tab |
| Default theme | Dark | Dark, light or follow the device. Each person can override it |
| Language | English | Only English is included |
| Default destination | Private | Where new downloads go unless chosen otherwise |
| Check for updates automatically | On | Ask GitHub for the latest release a few times a day (Settings > Updates). Updates are only installed when an administrator confirms |

### Downloads

| Setting | Default | Meaning |
|---|---|---|
| Simultaneous downloads (server) | 3 | Across all users (1 to 32) |
| Simultaneous downloads (per user) | 2 | Upper bound per user; a user's quota can lower it |
| Automatic retries | 3 | For temporary errors (network, server busy). Permanent errors are not retried |
| Retry delay | 30 s | Wait before each automatic retry |
| Default priority | Normal | Low, normal, high or urgent (urgent is for administrators) |
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
| Total storage limit | 0 | For all users together, in GB |
| Clean up temporary files after | 72 h | Leftover partial files of removed jobs |
| Delete completed files after | 0 days | 0 keeps files until someone deletes them |
| Keep history for | 0 days | Finished history entries and activity; 0 keeps everything |
| Shared folder | On | A common area every user can read |
| Users may save to the shared folder | On | Off: only administrators can add or delete shared files |

### Network

| Setting | Default | Meaning |
|---|---|---|
| Bind address | empty | Empty uses `server.bind_address`. Must be an IP address. Restart required |
| Port | 0 | 0 uses `server.port`. Restart required |
| Public URL | empty | The address people use; invitation links are built from it and it is accepted as request origin |
| Trusted proxies | none | IPs or CIDR ranges allowed to report the client address (`X-Forwarded-For`) and scheme |
| Allowed cross-origin sites | none | Explicit origins only; `*` is rejected. Restart required |

### Security

| Setting | Default | Meaning |
|---|---|---|
| Require an invitation to register | On | Off allows anyone who can reach FETCH to create an account |
| Invitation validity | 72 h | |
| Session length | 168 h | People are signed out after this long |
| Minimum password length | 10 | 8 to 128 |
| Require mixed characters | Off | Upper and lower case letters and a digit |
| Failed sign-ins allowed / within / lock out for | 5 / 15 min / 15 min | Per account and per client address |

### Default quotas

Applied to users created afterwards. Existing users keep their limits; change
those under **Users > Edit quota**. Administrators are created without limits.

| Setting | Default |
|---|---|
| Simultaneous downloads | 2 |
| Queued (unfinished) downloads | 100 |
| Storage | 50 GB |
| Largest file | no limit |
| Downloads per 24 hours | no limit |
| Speed limit | no limit |

## Command line

Run from the `backend` folder with the virtual environment's Python:

```powershell
..\.venv\Scripts\python.exe -m fetch_app <command>
```

| Command | Purpose |
|---|---|
| `init [--bind ADDR] [--port N]` | Create the configuration (if missing), folders and database; apply migrations |
| `serve [--host ADDR] [--port N]` | Run the server in the foreground |
| `status` | Print version, address, whether it is running, and the setup token while no user exists (JSON) |
| `stop` | Ask the running server to shut down cleanly |
| `create-admin [--username NAME] [--password-stdin]` | Create an administrator account |
