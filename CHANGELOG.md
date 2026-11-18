# Changelog

## [2.1.0] - 2026-11-18

### Added

- **Rapidgator CAPTCHA in a FETCH window.** When Rapidgator's free download
  asks for a CAPTCHA that only works on its own site, **Complete CAPTCHA >
  Open the CAPTCHA page** opens Rapidgator's page in a window of the FETCH
  app. You complete the free download steps and the CAPTCHA there yourself;
  when Rapidgator shows its download button, FETCH takes the download over
  and closes the window, so nothing has to be copied and pasted. Opening it
  in your browser and pasting the address still works.

### Changed

- A download waiting for a CAPTCHA now says "Needs you: click here to
  complete the CAPTCHA".

## [2.0.0] - 2026-11-17

FETCH is now a desktop app for your own PC, without accounts.

### Install

Download `FETCH.exe`, put it in a folder of its own and double-click it.
FETCH opens in its own window; there is no account to create and nothing to
sign in to. To give FETCH to a friend, send them `FETCH.exe`: each copy is
its own program with its own downloads.

Updating from 1.x with **Settings > Updates** keeps your downloads, history,
settings and provider credentials. The administrator's downloads become
yours. Other people's files stay in their folders under `storage\downloads\`.

### Added

- **Desktop window.** `FETCH.exe` shows the dashboard in its own window
  (Microsoft Edge WebView2) and runs the server inside the same program. No
  browser tab and no console window. Without WebView2 it falls back to the
  browser.
- **Tray icon.** Closing the window keeps FETCH running in the notification
  area so downloads continue; the tray menu opens the window or the download
  folder, or quits FETCH. **Settings > General > Keep running when the window
  is closed** turns this off.
- Starting `FETCH.exe` again brings the open window to the front.
- **Show in folder.** **Files > Open folder** and the folder button next to a
  file open File Explorer at that place.
- A global **speed limit** under **Settings > Downloads**.
- `FETCH.exe run --headless` starts only the server.
- **Rapidgator without an account.** Rapidgator links can be inspected and
  downloaded through Rapidgator's free download. FETCH waits the full period
  Rapidgator announces and then asks you to complete Rapidgator's CAPTCHA
  yourself (**Complete CAPTCHA** on the download). FETCH never solves or
  skips a CAPTCHA and never shortens a wait. Free downloads run one at a time.
- **Sign-in modes.** Each provider declares whether it works anonymously,
  with a stored account, or both. **Providers** shows what is available and
  configured, and lets you choose Automatic, Anonymous or Stored account.
- More precise errors: `premium_only` and `anonymous_unavailable` join the
  existing codes, and a refused free account is no longer reported as a
  CAPTCHA.

### Removed

- Accounts: sign-in, first-run setup token, users, roles, invitations,
  sessions, per-user quotas, and the `create-admin` command. A database
  migration (`0003`) drops their tables; your downloads are kept.
- Network access from other devices: the bind address, public URL, trusted
  proxies and cross-origin settings, and the Tailscale instructions. FETCH
  listens on `127.0.0.1` only.
- The `-BindAddress` option of `install.ps1` and the `--bind` / `--host`
  options of the command line.

### Security

- FETCH only answers to this computer: requests from other addresses, or that
  name FETCH by any host name other than `localhost` or `127.0.0.1` (DNS
  rebinding), are refused.
- Changes and the live connection must come from FETCH's own page; another
  website open in your browser cannot drive FETCH.

### Changed

- Database migrations `0002` (provider sign-in modes) and `0003` (remove
  accounts) are applied automatically at start; a backup is made first when
  updating.

### Not verified

- The window and the tray icon on a real Windows desktop by a person. The
  automated checks open the window with WebView2 on a Windows runner; the
  tray icon is covered only by tests with a stand-in.
- The anonymous Rapidgator flow is tested against a local emulation only. It
  has not been run against rapidgator.net.

## [1.1.0] - 2026-11-17

FETCH is now a single file.

### Install

Download `FETCH.exe`, put it in a folder of its own (for example `C:\FETCH`)
and double-click it. Nothing else has to be installed: Python and the web
interface are inside the file. FETCH creates its `config`, `data`, `logs` and
`storage` folders next to the executable, and opens the dashboard in your
browser with the setup token already filled in.

Windows may show a "Windows protected your PC" notice the first time,
because the file is not signed with a paid certificate. Choose **More info**,
then **Run anyway**. You can check the download against `FETCH.exe.sha256`.

Keep the FETCH window open while you use it. Closing it stops FETCH.

### Changed

- **Settings > Updates** now updates the executable itself: it downloads the
  new `FETCH.exe`, verifies it against the published SHA-256 checksum, backs
  up the database, swaps the file and restarts. No PowerShell is involved.
- The folder installation (`FETCH-1.1.0.zip` with `install.ps1`) is still
  published and still updates itself the same way as before.
- `FETCH.exe` also accepts commands: `FETCH.exe status`, `FETCH.exe stop`,
  `FETCH.exe create-admin`, `FETCH.exe run --no-browser`.

### Moving from 1.0.0

Stop FETCH, copy `config`, `data` and `storage` from the old folder into the
folder that holds `FETCH.exe`, and start `FETCH.exe`. Or keep the folder
installation and choose **Install update**.

### Known limitations

- `FETCH.exe` is not code signed.
- Rapidgator, MEGA, Pixeldrain and Google Drive were tested against local
  emulators only, not against the live services.

## [1.0.0] - 2026-11-16

First release.

### Install

Download `FETCH-1.0.0.zip`, unpack it, and run `.\install.ps1` in the `FETCH`
folder from Windows PowerShell. Python 3.12 or newer is required. Node.js is
not: the package contains the built web interface. The README has the full
instructions.

### What is in it

- Self-hosted download manager for several users, with a web dashboard:
  queue, priorities, pause and resume, retries, live progress.
- Providers: direct HTTP(S) links, Pixeldrain, Google Drive, MEGA and
  Rapidgator, each reporting what it can and cannot do. FETCH does not bypass
  CAPTCHAs, waiting times, quotas or Premium requirements.
- Optional aria2 engine for large files, with a built-in engine as fallback.
- Accounts with invitations, roles, per-user quotas, private and shared
  storage, and an activity log.
- Provider credentials encrypted at rest; protection against requests to
  private network addresses.
- Updates from the dashboard: **Settings > Updates** shows the installed
  version, checks GitHub for a newer release, shows its notes and installs it
  after confirmation. The package is verified against its published SHA-256
  checksum, and the database and configuration are backed up first.
- PowerShell scripts to install, start, stop, update and uninstall.

### Known limitations

- Rapidgator, MEGA, Pixeldrain and Google Drive were tested against local
  emulators only, not against the live services.
- Installing an update from the dashboard is available on Windows, when FETCH
  was started with `start.ps1`.
