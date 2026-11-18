# API reference

The dashboard talks to the backend through a JSON REST API under `/api` and a
WebSocket at `/api/ws`. The same API can be used by scripts running on the
same computer.

An interactive, always up-to-date reference generated from the code (OpenAPI)
is served by a running FETCH at **`/api/docs`**, and the schema at
`/api/openapi.json`.

## Conventions

- **Local only.** FETCH listens on `127.0.0.1` and answers only to clients on
  this computer whose `Host` header is `localhost` or a loopback address
  (anything else gets `403 local_only`). There is no sign-in and no session.
- **Origin.** Every request that changes something (`POST`, `PUT`, `PATCH`,
  `DELETE`), and the WebSocket, must either carry no `Origin` header (scripts)
  or an `Origin` equal to FETCH's own address. A request sent by another
  website gets `403 bad_origin`.
- **Errors.** Every error has the same shape and a stable `code`:

  ```json
  { "error": { "code": "invalid_state", "message": "A completed download cannot be paused." } }
  ```

  Common codes: `local_only` / `bad_origin` (403), `not_found` (404),
  `invalid_state` (409), validation problems (422).
- **Times** are UTC in ISO 8601. **Sizes** are bytes, **speeds** bytes per second.
- **Lists** return `{ "items": [...], "total": n, "page": 1, "page_size": 50 }`.

## Bootstrap

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Liveness: `{ "status": "ok", "version": "..." }` |
| `GET /api/bootstrap` | What the dashboard needs to start: `{ app_name, version, default_theme, language, theme, usage }`. `usage` holds `storage_bytes`, `storage_limit_bytes`, `max_concurrent`, `active_downloads`, `open_downloads`, `downloads_today` |
| `PATCH /api/preferences` | Body `{ theme: "dark" \| "light" \| "system" }` |

## Downloads

A download object:

```json
{
  "id": 12,
  "url": "https://example.com/file.zip", "provider": "direct", "filename": "file.zip",
  "state": "downloading", "priority": 1, "position": 12,
  "destination": "private", "folder": "", "final_path": "",
  "total_bytes": 1048576, "downloaded_bytes": 524288,
  "speed_bps": 262144, "avg_speed_bps": 250000, "eta_seconds": 2,
  "engine": "native", "attempts": 1, "max_attempts": 4,
  "error_code": "", "error_message": "", "wait_until": null, "wait_reason": "",
  "created_at": "...", "started_at": "...", "finished_at": null
}
```

States: `pending`, `queued`, `inspecting`, `waiting`, `downloading`, `paused`,
`verifying`, `completed`, `failed`, `cancelled`. `total_bytes` and
`eta_seconds` are `null` when the provider does not report a size; FETCH never
invents them.

| Method and path | Purpose |
|---|---|
| `POST /api/downloads/inspect` | Body `{ urls: [...] }`. Detects the provider of each link and fetches name and size. Nothing is queued. Returns `{ items: [{ url, provider, provider_name, supported, filename, size, resumable, restriction, message, premium_only, auth_mode }] }` |
| `POST /api/downloads` | Body `{ urls: [...], folder?, priority?: 0-3 }` (up to 100 links). Returns `{ created: [download], rejected: [{ url, provider, code, message }] }` |
| `GET /api/downloads` | Query: `page`, `page_size`, `state` (comma separated), `provider`, `q`, `sort` (`created_at`, `filename`, `size`, `state`, `provider`, `priority`, `progress`, `finished_at`, `position`, `queue`), `order` |
| `GET /api/downloads/stats` | Counters by state and current speed |
| `GET /api/downloads/{id}` | One download |
| `POST /api/downloads/{id}/pause` | Also `resume`, `cancel`, `retry`. `409 invalid_state` when the action does not apply |
| `GET /api/downloads/{id}/captcha` | The provider CAPTCHA this download is waiting on (state `waiting`, `error_code` `captcha_required`): `{ kind: "image" \| "external", prompt, has_image, page_url, accepts_download_url, attempt, expires_at }`. `404 no_captcha` otherwise |
| `GET /api/downloads/{id}/captcha/image` | The provider's CAPTCHA picture, for `kind: "image"` |
| `POST /api/downloads/{id}/captcha` | Body `{ answer }` (what you typed for a picture CAPTCHA) or `{ download_url }` (the address the provider issued after you completed its steps in your own browser). FETCH passes it on unchanged and never produces an answer itself |
| `PATCH /api/downloads/{id}` | Body `{ priority }` (0 low to 3 urgent) |
| `DELETE /api/downloads/{id}?delete_file=true\|false` | Remove from history; a running job is stopped first |
| `POST /api/downloads/bulk` | Body `{ ids, action: "pause"\|"resume"\|"cancel"\|"retry"\|"remove", delete_files? }`. Returns `{ done, skipped }` |
| `POST /api/downloads/reorder` | Body `{ ids }`: the given queued jobs take each other's positions in this order |

## Files

Paths are relative to the download folder (`storage\downloads\<n>`);
anything that would leave it is refused. `scope=shared` reaches the shared
folder that FETCH 1.x (with accounts) could write to, while it holds files.

| Method and path | Purpose |
|---|---|
| `GET /api/files` | Query: `scope`, `path`, `q` (recursive search), `type` (`video`, `audio`, `image`, `archive`, `document`, `application`, `other`), `sort` (`name`, `size`, `modified`), `order`, `page`, `page_size`. Returns a list plus `scope` and `path` |
| `GET /api/files/usage` | `{ private: { used_bytes, limit_bytes }, shared: { used_bytes }, disk: { total, used, free } }` |
| `GET /api/files/download?path=...` | The file, always as an attachment. Supports range requests |
| `POST /api/files/reveal` | Body `{ scope?, path }`. Shows the file selected in File Explorer, or opens the folder. Never opens or runs the file itself |
| `POST /api/files/folder` | Body `{ scope, path, name }` |
| `DELETE /api/files?path=...` | Delete a file or folder |

## Providers

| Method and path | Purpose |
|---|---|
| `GET /api/providers` | Every provider with `capabilities`, `notes`, `verification`, `enabled`, `configured`, `ready`, `auth_modes` and `auth` (`modes`, `anonymous_available`, `authentication_required`, `account_configured`, `account_valid`, `preference`, `effective_mode`, `status`: `anonymous` \| `account` \| `auth_required` \| `unavailable`). Also `credential_fields`, the *names* of stored fields, and the last health result. Stored values are never returned |
| `PATCH /api/providers/{id}` | Body `{ enabled }` |
| `PUT /api/providers/{id}/auth-mode` | Body `{ mode: "auto" \| "anonymous" \| "account" }`. `422 invalid_mode` when the provider does not support it |
| `PUT /api/providers/{id}/credentials` | Body `{ values: { field: value } }`. Replaces the stored credentials |
| `DELETE /api/providers/{id}/credentials` | Remove the stored credentials |
| `POST /api/providers/{id}/test` | Returns `{ ok, message, account }` |
| `GET /api/providers/{id}/errors` | Recent provider events |

## Settings and maintenance

| Method and path | Purpose |
|---|---|
| `GET /api/settings` | `{ settings, effective, restart_required }` |
| `PUT /api/settings` | Body: any subset of sections, for example `{ "downloads": { "retry_count": 5 } }`. Returns the new state plus `changed` |
| `GET /api/activity` | Query: `page`, `page_size`, `category` (`download`, `file`, `provider`, `admin`), `level`, `q` |
| `DELETE /api/activity/{id}` | Delete one activity entry. `{ deleted: 1 }`, or `404 not_found` |
| `DELETE /api/activity` | Delete every activity entry, or only those matching `category`, `level` and `q` (the same filters as the list). Returns `{ deleted }`. Downloads and files are not touched |
| `GET /api/admin/health` | Health checks, versions, disk, queue and connection counters |
| `GET /api/admin/logs?limit=&level=&q=` | Tail of the structured log |
| `GET /api/admin/update?refresh=true\|false` | Installed and latest version, release notes, whether an update can be installed from here (`can_apply`, `apply_blocker`) and the result of the last update run. The GitHub answer is cached for six hours; `refresh=true` asks again |
| `POST /api/admin/update/apply` | Install the latest release. `FETCH.exe` downloads and verifies the new executable during this request, then restarts; a folder installation starts `update.ps1` in the background. `409 update_unavailable` when there is nothing to install or this server cannot update itself (not Windows, not started with `start.ps1` or as `FETCH.exe`, an update already running) |
| `POST /api/admin/workers/restart` | Requeue every running transfer |
| `POST /api/admin/cleanup` | Run storage maintenance now |

## WebSocket

Connect to `ws://localhost:<port>/api/ws`. A connection from another website
(foreign `Origin`) or another host name is closed with code `1008`.

Every message is `{ "type": "...", "data": { ... } }`:

| Type | Data |
|---|---|
| `hello` | `server_time` |
| `ping` | (keep-alive) |
| `download.added`, `.updated` | `download` (full object) |
| `download.progress` | `id`, `downloaded_bytes`, `total_bytes`, `speed_bps`, `avg_speed_bps`, `eta_seconds`, at most about twice a second per job |
| `download.removed` | `id` |
| `queue.changed` | |
| `storage.changed` | `scope` |
| `activity` | |
| `activity.deleted` | `ids` (one entry) or `cleared` (count) |
| `settings.changed`, `providers.changed` | |

## Example

```powershell
$base = 'http://localhost:8787/api'

Invoke-RestMethod "$base/downloads" -Method Post `
    -ContentType 'application/json' `
    -Body '{"urls":["https://example.com/file.zip"]}'

Invoke-RestMethod "$base/downloads?state=downloading"
```
