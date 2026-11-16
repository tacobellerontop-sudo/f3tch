# FETCH

FETCH is a self-hosted download manager for one Windows PC and a small group of
people you trust. Paste links into a web dashboard; FETCH detects the provider,
queues the downloads, shows live progress, and stores the finished files on the
host computer, where each person can browse and retrieve their own.

- Multi-user, with invitations, roles, per-user quotas and private storage
- Queue with priorities, pause / resume / retry, and recovery after a restart
- Live updates over a WebSocket (no polling, no fake progress)
- Provider adapters: direct HTTP(S), Rapidgator, MEGA, Pixeldrain, Google Drive
- Runs entirely on your machine; no cloud service, no telemetry

> **What FETCH does not do.** It never bypasses a provider's authentication,
> Premium requirement, CAPTCHA, waiting period, quota or other access control.
> When a provider asks for something FETCH cannot legitimately satisfy, the
> download stops with a clear explanation. See [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Start and stop](#start-and-stop)
- [First-time setup](#first-time-setup)
- [Inviting friends](#inviting-friends)
- [Remote access with Tailscale](#remote-access-with-tailscale)
- [Providers](#providers)
- [Using FETCH](#using-fetch)
- [Storage layout](#storage-layout)
- [Updating](#updating)
- [Backup and restore](#backup-and-restore)
- [Uninstalling](#uninstalling)
- [Security overview](#security-overview)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [More documentation](#more-documentation)

## Requirements

| | |
|---|---|
| Operating system | Windows 10 or 11 (PowerShell 5.1 or newer) |
| Python | 3.12 or newer |
| Node.js | Not needed for a release package. 20 or newer only when installing from a git checkout, to build the web interface |
| aria2 | Optional. Speeds up large downloads with several connections |
| Disk | About 500 MB for the program, plus room for your downloads |

`install.ps1` checks all of these. If something is missing it offers to install
it with **winget** (Microsoft's package manager, official package sources) and
only does so after you say yes. You can also install them yourself:
[python.org](https://www.python.org/downloads/windows/),
[nodejs.org](https://nodejs.org/), [aria2](https://github.com/aria2/aria2/releases).

## Install

1. Download `FETCH-<version>.zip` from the
   [latest release](https://github.com/tacobellerontop-sudo/f3tch/releases/latest)
   and unpack it. Put the `FETCH` folder where it should live, for example `C:\FETCH`.
   Downloads are stored inside it by default (you can move them later).
2. Open PowerShell in that folder and run:

   ```powershell
   .\install.ps1
   ```

   If PowerShell refuses to run scripts, allow local scripts for your user once
   with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or run this one
   time with `powershell -ExecutionPolicy Bypass -File .\install.ps1`.

The installer creates a private Python environment in `.venv`, installs the
backend packages, builds the web interface, creates `config\fetch.toml` with
freshly generated secrets, and creates the database. Running it again is safe.

Useful options:

| Option | Effect |
|---|---|
| `-Port 9000` | Dashboard port (default 8787) |
| `-BindAddress 127.0.0.1` | Address to listen on (default: this computer only) |
| `-RegisterStartup` | Start FETCH automatically when you sign in to Windows |
| `-SkipAria2` | Do not offer to install aria2 |
| `-Yes` | Unattended: answer yes to every question |

`Get-Help .\install.ps1 -Full` shows everything.

## Start and stop

```powershell
.\start.ps1              # start in the background and open the dashboard
.\start.ps1 -Foreground  # run in this window and show the server output
.\stop.ps1               # stop cleanly; running downloads are saved
```

The dashboard is at <http://localhost:8787> unless you chose another port.

Stopping, restarting or even a power cut does not lose downloads: unfinished
jobs go back to the queue and continue from the bytes already on disk when the
server supports resuming.

**Automatic start.** `.\install.ps1 -RegisterStartup` registers a scheduled
task named "FETCH Download Manager" that starts FETCH when you sign in. It runs
with your normal user rights; there is no Windows service and no stored
password. `.\uninstall.ps1` removes it, or delete it in Task Scheduler.

## First-time setup

On the first visit the dashboard asks you to create the administrator account.
For that it needs the **setup token** that `install.ps1` and `start.ps1` print.
This stops anyone else on your network from claiming a fresh installation. The
token is useless once the administrator exists.

If you prefer the command line:

```powershell
cd backend
..\.venv\Scripts\python.exe -m fetch_app create-admin --username alice
```

## Inviting friends

1. Sign in as administrator and open **Users**.
2. Choose **Invite user**, optionally add a note, and create the link.
3. Send the link to your friend. It works once and expires (72 hours by
   default). They choose their own username and password.

New users get the default quota from **Settings > Default quotas**. Adjust
individual limits (storage, simultaneous downloads, speed, daily downloads,
largest file) under **Users > Edit quota**.

Registration without an invitation is off by default. Keep it that way unless
FETCH is only reachable on a network you fully control.

## Remote access with Tailscale

FETCH is meant to be reached over a private network, not the public internet.
[Tailscale](https://tailscale.com) gives you that with very little setup.

Recommended setup (HTTPS, nothing exposed to the internet):

1. Install Tailscale on the host PC and sign in. Each friend installs Tailscale
   too. Either add them to your tailnet or use Tailscale's *Share* feature to
   share just this machine with them.
2. Leave FETCH listening on `127.0.0.1` (the default) and run on the host PC:

   ```powershell
   tailscale serve --bg 8787
   ```

   Tailscale now serves FETCH at `https://<pc-name>.<tailnet>.ts.net` with a
   valid certificate, to members of your tailnet only.
3. In **Settings > Network** set **Public URL** to that address (invitation
   links then use it) and add `127.0.0.1` to **Trusted proxies**, so sign-in
   rate limiting sees each person's real address. Session cookies are marked
   `Secure` automatically when requests arrive over HTTPS.
4. Create invitations and send the links.

Alternative without HTTPS: set the bind address in **Settings > Network** to the
PC's Tailscale IP (`100.x.y.z`, shown by `tailscale ip -4`), restart FETCH, and
have friends open `http://100.x.y.z:8787`. Tailscale encrypts the traffic
between devices. Allow the port for the Tailscale interface in Windows Firewall
if asked.

Do **not** forward the FETCH port on your router and do not bind to `0.0.0.0`
on a network you do not control. Do not use `tailscale funnel`, which publishes
a service to the whole internet.

## Providers

| Provider | Works without setup | Needs | Notes |
|---|---|---|---|
| Direct HTTP(S) | Yes | Nothing | Any plain file link. Resume when the server supports it |
| Pixeldrain | Yes | Optional API key | Single-file links |
| MEGA | Yes | Nothing | Public file links with their key; decrypted and verified locally |
| Google Drive | No | Your own API key or service account | Publicly shared or explicitly shared files |
| Rapidgator | No | Your own account whose plan allows API downloads (in practice Premium) | Official API only |

Credentials are entered under **Providers** by an administrator, encrypted at
rest, and never shown again to anyone. Each provider card states exactly what
is supported and how it was verified. Details, setup steps and limits are in
[docs/PROVIDERS.md](docs/PROVIDERS.md).

## Using FETCH

- **Add download**: paste one or many links (one per line). FETCH inspects
  each link first and shows the file name, size and provider, or the reason a
  link cannot be added. Choose the private or shared folder, an optional
  subfolder and a priority.
- **Downloads**: everything you added, with search, filters, sorting and bulk
  actions. Select a row for details, including the exact error of a failed job.
- **Queue**: what runs next and in which order. Reorder within a priority or
  change the priority.
- **Files**: browse finished downloads, retrieve them to your own device, or
  delete them. Administrators can also browse other users' folders.
- **Providers**, **Activity**, **Users**, **Settings**: capabilities and
  credentials, the audit log, account management, and server configuration
  with diagnostics and logs.

## Storage layout

```
storage\
  downloads\<user id>\  private files of each user
  shared\               the shared folder (can be switched off)
  temporary\            partial downloads (cleaned up automatically)
data\fetch.db          users, history, settings, encrypted credentials
config\fetch.toml      bootstrap configuration and secrets (keep private)
logs\fetch.log         structured log (JSON lines, rotated)
```

A user can only ever reach files inside their own folder and the shared
folder. Stored files are served as downloads and are never executed or
rendered by the server. To keep downloads on another drive set **Settings >
Storage > Storage location**, stop FETCH, move the existing `storage` folder
there, and start FETCH again.

## Updating

**From the dashboard.** An administrator opens **Settings > Updates**. The page
shows the installed version, whether a newer release exists on GitHub, and its
release notes. **Install update** asks for confirmation, then stops FETCH,
installs the release and starts it again; the page reconnects by itself. A dot
on the Settings entry in the sidebar tells administrators when an update is
waiting. Nothing is ever installed without that click, and the automatic check
can be switched off on the same page.

**From PowerShell.** The dashboard runs the same script you can run yourself:

```powershell
.\update.ps1
```

Either way the update:

1. stops FETCH (running downloads are saved and continue afterwards),
2. backs up the database and configuration to `data\backups\<timestamp>\`,
3. downloads `FETCH-<version>.zip` from the project's GitHub releases and
   checks it against the SHA-256 checksum published with the release (a
   package that does not match is refused and nothing is changed),
4. replaces the program files only (`data`, `logs`, `storage`, `config\fetch.toml`
   and `.venv` are never touched),
5. updates the Python packages, migrates the database and starts FETCH again
   if it was running.

A git checkout is updated with `git pull --ff-only` instead of a release
package (use `-FromRelease` to force the package). If you update by copying
files yourself, run `.\update.ps1 -SkipPull`. The result of the last run is
shown in **Settings > Updates** and details are in `logs\update.log`.

Installing from the dashboard needs Windows and a server started with
`start.ps1`. The update check is an anonymous request to GitHub for the latest
release of the repository named under `[updates]` in `config\fetch.toml`; it
sends nothing about your server.

## Backup and restore

Everything that matters is in three places:

| What | Where |
|---|---|
| Database (users, history, settings, encrypted provider credentials) | `data\fetch.db` |
| Secrets, including the key that decrypts those credentials | `config\fetch.toml` |
| Downloaded files | `storage\` (or your custom storage location) |

**Backup:** run `.\stop.ps1`, copy `data\`, `config\fetch.toml` and, if you
want the files too, the storage folder. Start FETCH again.

**Restore:** install FETCH in the new place with `.\install.ps1`, stop it, copy
the three items back over the fresh ones, and run `.\start.ps1`.

The database and `fetch.toml` belong together: without the original
`secret_key`, stored provider credentials cannot be decrypted and have to be
entered again. Treat backups of `fetch.toml` as secrets.

## Uninstalling

```powershell
.\uninstall.ps1                                # remove the program, keep all data
.\uninstall.ps1 -RemoveData -RemoveDownloads   # remove everything (asks first)
```

Python, Node.js and aria2 are left installed; remove them in Windows Settings
if you no longer need them.

## Security overview

- Passwords are hashed with Argon2id. Sessions are opaque random tokens in
  `HttpOnly`, `SameSite=Lax` cookies, stored hashed, with expiry and revocation.
- State-changing requests need a CSRF token and a matching `Origin`.
- Sign-in attempts are rate limited per account and per address.
- Role checks happen on the server for every request and every WebSocket
  event; users cannot see or control each other's downloads or private files.
- Download URLs are checked against private, loopback and link-local address
  ranges at connection time and on every redirect (SSRF protection). aria2 is
  forced through a local filtering proxy that applies the same rule.
- File names and paths are sanitised; path traversal out of a user's folder is
  refused.
- Provider credentials are encrypted at rest (Fernet) and never returned by
  the API. Passwords, tokens and cookies are redacted from logs.
- aria2's RPC interface listens on loopback only, with a random secret, and is
  never exposed through FETCH.
- Cross-origin access is denied unless you list origins explicitly.
- Administrative actions are recorded in the activity log.

## Troubleshooting

| Problem | What to do |
|---|---|
| "running scripts is disabled on this system" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then run the script again |
| `start.ps1` says FETCH did not start | Run `.\start.ps1 -Foreground` to see why. Most often the port is in use: change `port` in `config\fetch.toml` or stop the other program |
| The dashboard asks for a setup token | Run `.\start.ps1`; it prints the token until the administrator exists |
| Forgot the administrator password | `cd backend`, then `..\.venv\Scripts\python.exe -m fetch_app create-admin --username rescue`, sign in and reset the other password under Users |
| A friend cannot open the dashboard | Check that both devices are connected to Tailscale and that the address matches the setup you chose above |
| "Sign in" works but every action fails with a CSRF or origin error | You are reaching FETCH through an address it does not know. Set **Public URL** in Settings > Network, or open it by the address shown at start |
| A link is rejected as "unsafe address" | The link points to a private or local address. If that is intended (a NAS on your LAN), add the host to `ssrf_allowlist` in `config\fetch.toml` and restart |
| Rapidgator says "CAPTCHA required" | The link needs an account whose plan allows API downloads. FETCH does not solve CAPTCHAs |
| MEGA downloads pause with a countdown | MEGA's transfer quota for your IP address is used up. FETCH continues when MEGA allows it |
| aria2 shows as "optional, not in use" | Install aria2 or place `aria2c.exe` in `tools\aria2\`, then restart. FETCH works without it |
| Something else | **Settings > Diagnostics** shows health checks; **Settings > Logs** and `logs\fetch.log` show details |

## Known limitations

- The interface is in English only. The language setting exists so more can
  be added, but no other translation is included.
- Rapidgator, MEGA, Pixeldrain and Google Drive were implemented from the
  providers' published interfaces and are tested against local emulations of
  them. They have **not** been run against the live services by the automated
  tests, and Rapidgator has not been confirmed with a real Premium account.
  Use **Providers > Test connection** after entering credentials.
- Folder links (MEGA, Google Drive, Pixeldrain lists) are not supported; add
  the individual file links.
- Native Google Docs, Sheets and Slides cannot be downloaded (they are not
  files); export them in Google Drive first.
- Free Rapidgator links and anything else that needs a CAPTCHA or a browser
  login cannot be downloaded.
- One server process, one SQLite database: built for a household or a group of
  friends, not for hundreds of concurrent users.
- The PowerShell scripts were written for Windows PowerShell 5.1 and
  PowerShell 7 and reviewed statically, but the development environment was
  Linux, so they have not been executed on Windows yet.
- Changing the storage location does not move existing files for you.

## More documentation

- [docs/PROVIDERS.md](docs/PROVIDERS.md): provider capabilities, credentials, limits, writing an adapter
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): every setting, `fetch.toml` and environment variables
- [docs/API.md](docs/API.md): REST endpoints and WebSocket events
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): architecture, running from source, tests
