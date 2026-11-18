# FETCH

FETCH is a download manager for your Windows PC. Paste links into its window;
FETCH detects the provider, queues the downloads, shows live progress, and
stores the finished files on your computer.

- A desktop app: one `FETCH.exe`, its own window and a tray icon. No browser,
  no accounts, nothing to set up
- Queue with priorities, pause / resume / retry, and recovery after a restart
- Live progress (no polling, no fake progress)
- Provider adapters: direct HTTP(S), Rapidgator, MEGA, Pixeldrain, Google Drive
- Runs entirely on your machine and only answers to it; no cloud service, no telemetry

To give FETCH to a friend, send them `FETCH.exe`. Each copy is its own
program with its own downloads.

> **What FETCH does not do.** It never bypasses a provider's authentication,
> Premium requirement, CAPTCHA, waiting period, quota or other access control.
> When a provider asks for something FETCH cannot legitimately satisfy, the
> download stops with a clear explanation. See [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Contents

- [Quick start: one file](#quick-start-one-file)
- [Requirements](#requirements)
- [Install (folder installation)](#install-folder-installation)
- [Start and stop](#start-and-stop)
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

## Quick start: one file

1. Download `FETCH.exe` from the
   [latest release](https://github.com/tacobellerontop-sudo/f3tch/releases/latest).
2. Put it in a folder of its own, for example `C:\FETCH`. FETCH keeps
   everything next to the file: `config`, `data`, `logs` and `storage`.
3. Double-click it. FETCH opens in its own window. That is all: there is no
   account to create and nothing to sign in to.

Nothing else has to be installed; Python and the interface are inside the
file. The window uses Microsoft Edge WebView2, which Windows 10 and 11
already have. On a PC without it, FETCH opens in your browser instead.

**Closing the window** keeps FETCH running in the notification area (the tray
icon next to the clock), so downloads continue. Click the tray icon to open
the window again, or choose **Quit FETCH** from its menu to stop it. To quit
when you close the window instead, switch off **Settings > General > Keep
running when the window is closed**. Starting `FETCH.exe` while it is already running brings
its window to the front. Downloads that were running when FETCH stopped
continue the next time you start it.

**Your files.** **Files > Open folder** opens the download folder in File
Explorer, and the folder button next to a file shows it there.

Windows may show "Windows protected your PC" the first time, because the
file is not signed with a paid certificate. Choose **More info**, then **Run
anyway**. To check the download, compare it with `FETCH.exe.sha256` from the
same release: `Get-FileHash .\FETCH.exe`.

**Updates:** **Settings > Updates > Install update** downloads the new
`FETCH.exe`, verifies its checksum, backs up the database, replaces the file
and starts FETCH again.

Commands, from PowerShell in that folder: `.\FETCH.exe status`,
`.\FETCH.exe stop`, and `.\FETCH.exe run --headless` (no window or tray
icon; the dashboard is then at <http://localhost:8787>). To use another port,
change it in **Settings > Advanced**. For aria2, put `aria2c.exe` in
`tools\aria2\` next to `FETCH.exe` or install it system-wide.

The rest of this README describes the folder installation
(`FETCH-<version>.zip` with PowerShell scripts), which runs FETCH from a
Python environment and shows the dashboard in your browser. The providers,
storage layout and backup sections apply to both.

## Requirements

These are for the folder installation. `FETCH.exe` needs only Windows 10 or 11.


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

## Install (folder installation)

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

## Providers

| Provider | Works without setup | Needs | Notes |
|---|---|---|---|
| Direct HTTP(S) | Yes | Nothing | Any plain file link. Resume when the server supports it |
| Pixeldrain | Yes | Optional API key | Single-file links |
| MEGA | Yes | Nothing | Public file links with their key; decrypted and verified locally |
| Google Drive | No | Your own API key or service account | Publicly shared or explicitly shared files |
| Rapidgator | Yes | Nothing for free downloads (you wait and complete Rapidgator's CAPTCHA yourself). Optionally your own Premium account | Free download flow, or the official API with an account |
| Dropbox | Yes | Nothing | Public share links; shared folders arrive as one .zip |
| MediaFire | Yes | Nothing | Public file links; SHA-256 checked |
| WeTransfer | Yes | Nothing | wetransfer.com/downloads and we.tl links; several files arrive as one .zip |
| YouTube & media sites | Yes | Nothing (ffmpeg for above 720p) | YouTube, Vimeo, SoundCloud, TikTok, Twitch, X, Reddit and more, through yt-dlp. Quality under Settings > Downloads |

Spotify (and Apple Music, Tidal, Deezer) is not supported: its music is
protected by DRM, and FETCH does not remove DRM. A Spotify link is answered
with that explanation.

### Speed

FETCH splits every file of 2 MB or more into parts and downloads them over
several connections at once (8 by default, up to 32 under **Settings >
Downloads > Connections per file**). When one connection finishes early it
takes over half of the slowest part, so one slow connection never holds the
download back. Each part's progress is saved, so pause, a crash or a lost
connection continues where every part stopped. This needs no extra program;
aria2 remains an option. Servers that do not support ranges, and providers
that allow one connection only (MEGA, free Rapidgator), use one connection.
YouTube and other media downloads fetch their fragments in parallel too.

Credentials are entered under **Providers**, encrypted at rest, and never
shown again. Each provider card states exactly what
is supported and how it was verified. Details, setup steps and limits are in
[docs/PROVIDERS.md](docs/PROVIDERS.md).

## Using FETCH

- **Add download**: paste links, or any text that contains them (a chat
  message, a forum post, a page's HTML): FETCH picks the links out. It
  inspects each link first and shows the file name, size and provider, or
  the reason a link cannot be added. Untick the ones you don't want, then
  choose an optional subfolder and a priority.
- **Link grabbing**: copy download links anywhere on your PC and FETCH offers
  to add them (bottom right of its window). It reacts to file-host links,
  media sites and links that end in a file name such as `.zip` or `.exe`,
  not to ordinary web pages. FETCH never adds anything on its own. The
  clipboard is read only on this PC and never stored; turn it off under
  **Settings > General > Watch the clipboard for links**.
- **Downloads**: everything you added, with search, filters, sorting and bulk
  actions. Select a row for details, including the exact error of a failed job.
- **Queue**: what runs next and in which order. Reorder within a priority or
  change the priority.
- **Auto-extract**: finished .zip, .7z, .rar and .tar downloads are unpacked
  into a folder next to them, multi-part sets (`name.part1.rar`, `name.r00`,
  `name.7z.001`) once the last part is in. .7z and .rar need 7-Zip
  (`winget install 7zip.7zip`). Paths that would leave the folder are refused
  and password-protected archives are left alone. Turn it off, or have the
  archive deleted afterwards, under **Settings > Downloads**.
- **Schedule**: **Settings > Downloads > Only download at certain times**
  limits downloading to chosen hours and days (for example 01:00 to 07:00
  every night). Outside them the queue waits; running downloads go back to
  the queue and continue where they were when the time comes.
- **Files**: browse finished downloads, show them in File Explorer, save a
  copy elsewhere, or delete them.
- **Providers**, **Activity**, **Settings**: capabilities and credentials,
  the activity log, and settings with diagnostics and logs.

## Storage layout

```
storage\
  downloads\<n>\        your downloads (a number, 1 for most installations)
  shared\              only present if an older FETCH with accounts saved files there
  temporary\           partial downloads (cleaned up automatically)
data\fetch.db          history, settings, encrypted credentials
config\fetch.toml      bootstrap configuration and secrets (keep private)
logs\fetch.log         structured log (JSON lines, rotated)
```

Stored files are never executed or opened by FETCH itself. To keep downloads
on another drive set **Settings > Storage > Storage location**, stop FETCH,
move the existing `storage` folder there, and start FETCH again.

**Coming from FETCH 1.x (with accounts).** The update keeps your downloads,
history and provider credentials. The administrator's downloads become yours;
other people's files stay in their numbered folders under `storage\downloads\`
and can be moved by hand. Accounts, invitations, sessions and quotas are
removed from the database.

## Updating

With `FETCH.exe`, use **Settings > Updates** as described under
[Quick start](#quick-start-one-file), or stop FETCH and replace the file by
hand. The rest of this section is about the folder installation.

**From the dashboard.** Open **Settings > Updates**. The page
shows the installed version, whether a newer release exists on GitHub, and its
release notes. **Install update** asks for confirmation, then stops FETCH,
installs the release and starts it again; the page reconnects by itself. A dot
on the Settings entry in the sidebar tells you when an update is waiting. Nothing is ever installed without that click, and the automatic check
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
| Database (history, settings, encrypted provider credentials) | `data\fetch.db` |
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

- FETCH listens on `127.0.0.1` only and answers only to requests from this
  computer that name it as `localhost` or `127.0.0.1`. Requests carrying any
  other host name are refused, which also defeats DNS rebinding.
- State-changing requests and the live-update connection must come from
  FETCH's own page: a request sent by another website open in a browser on
  the same PC is refused (`Origin` check).
- The local control channel (`FETCH.exe stop`, bringing the window forward)
  needs a random token stored in `data\control.json`.
- Download URLs are checked against private, loopback and link-local address
  ranges at connection time and on every redirect (SSRF protection). aria2 is
  forced through a local filtering proxy that applies the same rule.
- File names and paths are sanitised; path traversal out of the storage
  folder is refused.
- Provider credentials are encrypted at rest (Fernet) and never returned by
  the API. Passwords, tokens and cookies are redacted from logs.
- aria2's RPC interface listens on loopback only, with a random secret, and is
  never exposed through FETCH.
- Settings changes and maintenance actions are recorded in the activity log.

There are no accounts: anyone who can use your Windows account can use FETCH.

## Troubleshooting

| Problem | What to do |
|---|---|
| "running scripts is disabled on this system" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then run the script again |
| `start.ps1` says FETCH did not start | Run `.\start.ps1 -Foreground` to see why. Most often the port is in use: change `port` in `config\fetch.toml` or stop the other program |
| FETCH opens in the browser instead of its own window | The Microsoft Edge WebView2 Runtime is missing. Install it from Microsoft and start FETCH again |
| The window is gone but FETCH still runs | It is in the notification area next to the clock (maybe under the ^ arrow). Click it, or start `FETCH.exe` again |
| The dashboard says "FETCH only answers to this computer" | Open it as `http://localhost:8787` (or your port). FETCH cannot be reached from other devices |
| A link is rejected as "unsafe address" | The link points to a private or local address. If that is intended (a NAS on your LAN), add the host to `ssrf_allowlist` in `config\fetch.toml` and restart |
| A Rapidgator download says "Needs you" | Rapidgator's free download asks for a CAPTCHA. Click the download, then **Complete CAPTCHA**. If the CAPTCHA only works on Rapidgator's site, choose **Open the CAPTCHA page** and complete it in the window that opens. FETCH never solves it. See [docs/PROVIDERS.md](docs/PROVIDERS.md#rapidgator) |
| Rapidgator says "Premium only" | The file is Premium-only or over the free size limit, or the stored account is not Premium |
| MEGA downloads pause with a countdown | MEGA's transfer quota for your IP address is used up. FETCH continues when MEGA allows it |
| aria2 shows as "optional, not in use" | Install aria2 or place `aria2c.exe` in `tools\aria2\`, then restart. FETCH works without it |
| Something else | **Settings > Diagnostics** shows health checks; **Settings > Logs** and `logs\fetch.log` show details |

## Known limitations

- The interface is in English only. The language setting exists so more can
  be added, but no other translation is included.
- Dropbox, MediaFire, WeTransfer and the YouTube/media provider have only
  been tested against local stand-ins, not the live sites. yt-dlp itself is
  maintained against those sites, but a site change can break a download
  until yt-dlp is updated with FETCH. Without ffmpeg, YouTube is limited to
  720p; playlists and channels are not expanded into separate downloads.
- Rapidgator, MEGA, Pixeldrain and Google Drive were implemented from the
  providers' published interfaces and are tested against local emulations of
  them. They have **not** been run against the live services by the automated
  tests. Rapidgator has not been confirmed with a real Premium account, and
  its anonymous free download has not been confirmed against the live site.
  Use **Providers > Test connection** after entering credentials.
- Folder links (MEGA, Google Drive, Pixeldrain lists) are not supported; add
  the individual file links.
- Native Google Docs, Sheets and Slides cannot be downloaded (they are not
  files); export them in Google Drive first.
- Free Rapidgator downloads need you to complete Rapidgator's CAPTCHA, and
  run one at a time without resume. If Rapidgator serves a CAPTCHA widget
  that only works on its own site, **Open the CAPTCHA page** shows
  Rapidgator's page in a FETCH window; you complete the steps there and FETCH
  picks up the download by itself. This has not yet been tried against the
  live Rapidgator site.
- Anything else that needs a browser login cannot be downloaded.
- FETCH is for the computer it runs on. It cannot be opened from a phone or
  another PC, and there are no accounts.
- `FETCH.exe` is built and tested on Windows by the automated checks: start
  from an empty folder, refusing other host names and websites, update check,
  replacing itself with a newer build, restart and stop, and opening its
  window with WebView2, bringing it forward and closing it. It is not code
  signed, so Windows SmartScreen warns about it once. It has not been tried
  on a real desktop by a person yet, where antivirus software may behave
  differently. The tray icon is not exercised by the automated checks.
- The PowerShell scripts are run on Windows Server with Windows PowerShell 5.1
  by the automated checks (install, start, update, stop, uninstall). The
  winget prompts and the sign-in startup task have not been exercised.
- Changing the storage location does not move existing files for you.

## More documentation

- [docs/PROVIDERS.md](docs/PROVIDERS.md): provider capabilities, credentials, limits, writing an adapter
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): every setting, `fetch.toml` and environment variables
- [docs/API.md](docs/API.md): REST endpoints and WebSocket events
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): architecture, running from source, tests
