# Changelog

## [Unreleased]

### Added

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

### Changed

- The database gains a small migration (`0002`); it is applied automatically
  at start.

### Not verified

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
