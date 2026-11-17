# Providers

A provider adapter turns a link into a download. Every adapter declares what
it can do, and the dashboard shows exactly that under **Providers**, together
with how the integration was verified.

## Ground rules

FETCH only downloads what the provider permits for the link and the account in
use. It does not and will not:

- bypass logins, Premium requirements or paywalls,
- solve, skip or outsource CAPTCHAs,
- skip or shorten waiting periods,
- evade IP, account or transfer quotas (no proxies, no account rotation),
- forge tokens or use private or undocumented interfaces to get around limits.

When a provider demands one of these, the download ends with a specific status
(`captcha_required`, `auth_required`, `quota_exceeded`, ...) and a message that
says what would be needed. Waiting periods that the provider announces are
simply waited out, visibly, with a countdown.

## Capability overview

| | Direct | Pixeldrain | MEGA | Google Drive | Rapidgator |
|---|---|---|---|---|---|
| Works without credentials | yes | yes | yes | no | no |
| Accepts credentials | no | API key (optional) | no | API key or service account | account e-mail and password |
| Inspect before download | yes | yes | yes | yes | yes |
| Resume | if the server supports it | yes | yes | yes | yes |
| Integrity check | size | size | MAC (cryptographic) | MD5 | size, MD5 when given |
| Folder links | no | no | no | no | no |
| Verified against the live service | local servers only | no | no | no | no |

"Verified" is meant literally. Direct HTTP is exercised end to end by the test
suite against real local HTTP servers (including resume, redirects, failures
and multi-gigabyte streams). The other four were implemented from the
providers' published interfaces and are tested against local emulations of
those interfaces, because the build environment had no access to the services
and no accounts. After entering credentials, use **Test connection**, then try
one small file.

## Direct HTTP(S)

Any `http://` or `https://` link that leads straight to a file.

- The file name comes from `Content-Disposition`, else from the URL.
- Resume needs range support and an `ETag` or `Last-Modified` header; FETCH
  uses `If-Range`, so a file that changed on the server restarts cleanly
  instead of being corrupted.
- Redirects are followed manually (at most 10) and every hop is checked
  against the SSRF rules.
- Links that require cookies, a login form or a CAPTCHA in a browser fail with
  `auth_required`. FETCH does not accept pasted cookies.

## Pixeldrain

Links: `https://pixeldrain.com/u/<id>` (also `/api/file/<id>`).

- Works anonymously through Pixeldrain's public API.
- **Optional API key** (pixeldrain.com > Account > API keys): downloads then
  count against your own account's transfer allowance.
- Album (`/l/`) and filesystem (`/d/`) links are reported as unsupported.
- When Pixeldrain rate-limits anonymous downloads or demands a CAPTCHA for a
  popular file, FETCH reports that and stops.

## MEGA

Links: `https://mega.nz/file/<id>#<key>` and the older `#!<id>!<key>` form.

- Public single-file links only. The link must include its decryption key.
- Files are decrypted locally (AES-128-CTR) while downloading and verified
  with MEGA's own MAC afterwards; a corrupted transfer is detected and retried.
- MEGA enforces a transfer quota per IP address for anonymous downloads. When
  it is exhausted FETCH shows the waiting time MEGA announces and continues
  afterwards.
- Folder links and signing in to a MEGA account are not supported.
- aria2 is not used for MEGA (the stream has to be decrypted in order).

## Google Drive

Links: `https://drive.google.com/file/d/<id>/...`,
`https://drive.google.com/open?id=<id>` and `https://drive.google.com/uc?id=<id>`.

Google Drive is integrated through the official **Drive API v3**, which needs
credentials from your own Google Cloud project. Without them Drive links are
rejected with an explanation; FETCH does not scrape the Drive website or its
"download anyway" confirmation pages.

**Option A: API key** (enough for files shared as "Anyone with the link")

1. Open <https://console.cloud.google.com/>, create a project.
2. *APIs & Services > Library*: enable **Google Drive API**.
3. *APIs & Services > Credentials > Create credentials > API key*. Restrict the
   key to the Drive API.
4. In FETCH: **Providers > Google Drive > Add credentials**, paste the key.

**Option B: service account** (for files that are not public)

1. In the same project: *Credentials > Create credentials > Service account*.
2. Open the service account, *Keys > Add key > JSON*, and download the file.
3. Paste the whole JSON into the "Service account key" field in FETCH.
4. Share the files, folders or shared drives you want with the service
   account's e-mail address (`...@...iam.gserviceaccount.com`) in Google Drive.

Limits: native Google Docs, Sheets and Slides are not downloadable files;
folder links are not supported; Google's own download quota ("too many users
have viewed or downloaded this file") is reported as `quota_exceeded`.

## Rapidgator

Links: `https://rapidgator.net/file/<id>/...` (also `rg.to`).

FETCH uses Rapidgator's official API with **your own account**.

1. **Providers > Rapidgator > Add credentials**: account e-mail and password
   (and a current two-factor code if the account uses one).
2. **Test connection** shows whether the login works and what Rapidgator
   reports about the account (Premium status, traffic left).

What to expect:

- Rapidgator only hands out API download links to accounts whose plan allows
  it. In practice that means an active Premium subscription.
- Without an account, or with a free account, Rapidgator's own website flow
  requires a waiting period and an interactive CAPTCHA. FETCH does not
  automate that flow. Such links fail with `captcha_required` and the message
  says so.
- Daily traffic and storage quotas reported by Rapidgator are shown as
  `quota_exceeded`; announced delays are waited out.
- The session token is reused and renewed when it expires; the password is
  stored encrypted and never logged.
- **This adapter has not been confirmed against the live service or a real
  Premium account.** It follows the published API and passes tests against an
  emulation of it. If Rapidgator's API behaves differently, the download fails
  with the provider's own message, which is recorded under
  **Providers > Rapidgator > Events**.

## Status codes

| Code | Meaning | Retried automatically |
|---|---|---|
| `unsupported_link` | The link type is not supported (folder, malformed, ...) | no |
| `not_found` | The file does not exist or was removed | no |
| `auth_required` | A login, API key or permission is needed | no |
| `auth_failed` | The stored credentials were rejected | no |
| `captcha_required` | The provider demands a CAPTCHA | no |
| `quota_exceeded` | A provider or FETCH quota is exhausted | no |
| `wait_required` | The provider announced a waiting period | continues after the wait |
| `rate_limited` | Too many requests | yes |
| `provider_unavailable` | Temporary provider or server error | yes |
| `network_error` | Connection problem or interrupted transfer | yes |
| `unsafe_url` | The link (or a redirect) points to a private address | no |
| `provider_disabled` | An administrator disabled this provider | no |
| `verification_failed` | The finished file failed its integrity check | yes |
| `unexpected_response` | The provider answered in a way FETCH does not understand | no |

## Writing an adapter

Adapters live in `backend/fetch_app/providers/` and subclass
`ProviderAdapter` from `base.py`:

```python
class ExampleProvider(ProviderAdapter):
    id = "example"
    name = "Example"
    description = "Public example.com file links."
    priority = 20                      # lower runs first; Direct is the fallback
    capabilities = Capabilities(checksum=True)
    credential_fields = (CredentialField("api_key", "API key"),)
    notes = ("Single-file links only.",)
    verification = "State honestly how this was tested."

    def can_handle(self, url: str) -> bool: ...
    async def inspect(self, url, ctx) -> LinkInfo: ...        # metadata, no download
    async def resolve(self, url, ctx) -> ResolvedDownload: ... # final URL, headers, verifier
    async def test(self, ctx) -> HealthReport: ...             # "Test connection"
```

- `ctx.http` is the SSRF-guarded HTTP client; always use it.
- `ctx.credentials` holds the decrypted credentials for this provider, if any.
- Raise `ProviderError(code, message, retryable=..., wait_seconds=...)` with a
  message that is safe to show to users.
- `resolve` returns a `ResolvedDownload`; the engine takes care of transfer,
  resume, progress, rate limits and quotas. Override `download` only for
  protocols that are not a single HTTP stream.
- Register the class in `registry.py` (`builtin_providers`) and add tests with a
  local emulation in `backend/tests/`. Do not make tests depend on live
  services or real accounts.
- Report capabilities truthfully. An adapter that cannot do something should
  say so rather than pretend.
