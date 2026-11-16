# API reference

The dashboard talks to the backend through a JSON REST API under `/api` and a
WebSocket at `/api/ws`. The same API can be used by scripts.

An interactive, always up-to-date reference generated from the code (OpenAPI)
is served by a running FETCH at **`/api/docs`**, and the schema at
`/api/openapi.json`.

## Conventions

- **Authentication.** Sign in with `POST /api/auth/login`. The server sets an
  `HttpOnly` session cookie (`fetch_session`). Send it with every request.
- **CSRF.** Every request that changes something (`POST`, `PUT`, `PATCH`,
  `DELETE`) must carry the header `X-CSRF-Token` with the `csrf_token` returned
  by login, setup, register and `GET /api/auth/me`. Browsers must also send a
  same-origin `Origin`.
- **Errors.** Every error has the same shape and a stable `code`:

  ```json
  { "error": { "code": "invalid_state", "message": "A completed download cannot be paused." } }
  ```

  Common codes: `not_authenticated` (401), `forbidden` (403), `not_found`
  (404), `csrf_failed` / `bad_origin` (403), `rate_limited` (429),
  `invalid_state` (409), validation problems (422).
- **Isolation.** A regular user only ever sees their own downloads and files.
  Requests for another user's objects answer `404`, exactly as if they did not
  exist. Endpoints marked *admin* answer `403` for regular users.
- **Times** are UTC in ISO 8601. **Sizes** are bytes, **speeds** bytes per second.
- **Lists** return `{ "items": [...], "total": n, "page": 1, "page_size": 50 }`.

## Bootstrap and authentication

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Liveness: `{ "status": "ok", "version": "..." }`. No authentication |
| `GET /api/bootstrap` | Public facts the sign-in page needs: app name, whether setup or invitations are required, password policy |
| `POST /api/setup` | First run only. Body `{ setup_token, username, password }`. Creates the administrator and signs in |
| `POST /api/auth/login` | Body `{ username, password }`. Returns the session payload |
| `POST /api/auth/logout` | Ends the current session |
| `GET /api/auth/me` | Session payload: `{ user, csrf_token, quota: { limits, usage }, session_expires_at }` |
| `PATCH /api/auth/me` | Body `{ display_name?, theme? }` |
| `POST /api/auth/password` | Body `{ current_password, new_password }`. Signs out the account's other sessions |
| `GET /api/auth/sessions` | The caller's active sessions |
| `DELETE /api/auth/sessions/{id}` | Revoke one of the caller's other sessions |
| `GET /api/auth/invitation/{token}` | Whether an invitation is valid |
| `POST /api/auth/register` | Body `{ token, username, password, display_name? }`. The token is required unless open registration is enabled |

Sign-in, setup, registration and password changes are rate limited; a locked
out client receives `429` with `Retry-After`.

## Downloads

A download object:

```json
{
  "id": 12, "user_id": 2, "owner": "bob",
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
| `POST /api/downloads/inspect` | Body `{ urls: [...] }`. Detects the provider of each link and fetches name and size. Nothing is queued. Returns `{ items: [{ url, provider, provider_name, supported, filename, size, resumable, restriction, message }] }` |
| `POST /api/downloads` | Body `{ urls: [...], destination?: "private" \| "shared", folder?, priority?: 0-3 }` (up to 100 links). Returns `{ created: [download], rejected: [{ url, provider, code, message }] }` |
| `GET /api/downloads` | Query: `page`, `page_size`, `state` (comma separated), `provider`, `q`, `sort` (`created_at`, `filename`, `size`, `state`, `provider`, `priority`, `progress`, `finished_at`, `position`, `queue`), `order`, and for administrators `owner` (`me` or a user id; empty for everyone) |
| `GET /api/downloads/stats?scope=me\|all` | Counters by state and current speed (`all` is admin only) |
| `GET /api/downloads/{id}` | One download |
| `POST /api/downloads/{id}/pause` | Also `resume`, `cancel`, `retry`. `409 invalid_state` when the action does not apply |
| `PATCH /api/downloads/{id}` | Body `{ priority }`. Priority 3 (urgent) is admin only |
| `DELETE /api/downloads/{id}?delete_file=true\|false` | Remove from history; a running job is stopped first |
| `POST /api/downloads/bulk` | Body `{ ids, action: "pause"\|"resume"\|"cancel"\|"retry"\|"remove", delete_files? }`. Returns `{ done, skipped }` |
| `POST /api/downloads/reorder` | Body `{ ids }`: the given queued jobs take each other's positions in this order |

## Files

`scope` is `private` (default) or `shared`. Administrators may add
`owner_id` to address another user's private folder. Paths are relative to
that folder; anything that would leave it is refused.

| Method and path | Purpose |
|---|---|
| `GET /api/files` | Query: `scope`, `path`, `q` (recursive search), `type` (`video`, `audio`, `image`, `archive`, `document`, `application`, `other`), `sort` (`name`, `size`, `modified`), `order`, `page`, `page_size`. Returns a list plus `scope`, `path`, `owner_id`, `can_write` |
| `GET /api/files/usage` | Storage used and the caller's limit; administrators also get disk and per-user figures |
| `GET /api/files/download?path=...` | The file, always as an attachment. Supports range requests |
| `POST /api/files/folder` | Body `{ scope, path, name }` |
| `DELETE /api/files?path=...` | Delete a file or folder |

## Providers

| Method and path | Purpose |
|---|---|
| `GET /api/providers` | Every provider with `capabilities`, `notes`, `verification`, `enabled`, `configured`, `ready`. Administrators also get `credential_fields`, the *names* of stored fields, and the last health result. Stored values are never returned |
| `PATCH /api/providers/{id}` | *admin*. Body `{ enabled }` |
| `PUT /api/providers/{id}/credentials` | *admin*. Body `{ values: { field: value } }`. Replaces the stored credentials |
| `DELETE /api/providers/{id}/credentials` | *admin* |
| `POST /api/providers/{id}/test` | *admin*. Returns `{ ok, message, account }` |
| `GET /api/providers/{id}/errors` | *admin*. Recent provider events |

## Administration

All *admin* only, except `GET /api/activity`, which returns the caller's own
activity for regular users.

| Method and path | Purpose |
|---|---|
| `GET /api/users` | Users with quota, storage use and counters |
| `PATCH /api/users/{id}` | Body `{ display_name?, role?, is_active? }`. The last active administrator cannot be demoted or disabled |
| `DELETE /api/users/{id}?delete_files=true\|false` | Delete a user, their sessions and history |
| `POST /api/users/{id}/reset-password` | Body `{ new_password? }`. Without one a temporary password is generated and returned once |
| `GET /api/users/{id}/quota`, `PUT /api/users/{id}/quota` | Body `{ max_concurrent, max_storage_bytes, max_file_bytes, daily_downloads, bandwidth_bps, max_queued }` (0 = no limit) |
| `GET /api/invitations` | All invitations with their status |
| `POST /api/invitations` | Body `{ role?, note?, expires_hours? }`. The response contains `token`, `path` and `url` exactly once |
| `DELETE /api/invitations/{id}` | Revoke |
| `GET /api/admin/sessions`, `DELETE /api/admin/sessions/{id}` | Everyone's sessions; revoke one |
| `GET /api/settings` | `{ settings, effective, restart_required }` |
| `PUT /api/settings` | Body: any subset of sections, for example `{ "downloads": { "retry_count": 5 } }`. Returns the new state plus `changed` |
| `GET /api/activity` | Query: `page`, `page_size`, `category` (`auth`, `download`, `file`, `provider`, `admin`), `level`, `scope` (`me`, `all`), `q` |
| `GET /api/admin/health` | Health checks, versions, disk, queue and connection counters |
| `GET /api/admin/logs?limit=&level=&q=` | Tail of the structured log |
| `POST /api/admin/workers/restart` | Requeue every running transfer |
| `POST /api/admin/cleanup` | Run storage maintenance now |

## WebSocket

Connect to `ws(s)://<host>/api/ws` with the session cookie. The server closes
with code `4401` when the session is missing, expired or revoked.

Every message is `{ "type": "...", "data": { ... } }`:

| Type | Data | Sent to |
|---|---|---|
| `hello` | `user_id`, `server_time` | the connection |
| `ping` | | the connection (keep-alive) |
| `download.added`, `.started`, `.paused`, `.completed`, `.failed`, `.cancelled`, `.updated` | `download` (full object) | owner and administrators |
| `download.progress` | `id`, `downloaded_bytes`, `total_bytes`, `speed_bps`, `avg_speed_bps`, `eta_seconds` | owner and administrators, at most about twice a second per job |
| `download.removed` | `id` | owner and administrators |
| `queue.changed` | | everyone (carries no details) |
| `storage.changed` | `scope` | owner, or everyone for the shared folder |
| `activity` | | the affected user and administrators |
| `quota.changed` | | the affected user |
| `settings.changed`, `providers.changed` | | everyone |
| `users.changed` | | administrators |

Events are filtered on the server per connection, so a client never receives
another user's download data.

## Example

```powershell
$base = 'http://localhost:8787/api'
$login = Invoke-RestMethod "$base/auth/login" -Method Post -SessionVariable s `
    -ContentType 'application/json' -Body '{"username":"alice","password":"..."}'
$headers = @{ 'X-CSRF-Token' = $login.csrf_token }

Invoke-RestMethod "$base/downloads" -Method Post -WebSession $s -Headers $headers `
    -ContentType 'application/json' `
    -Body '{"urls":["https://example.com/file.zip"]}'

Invoke-RestMethod "$base/downloads?state=downloading" -WebSession $s
```
