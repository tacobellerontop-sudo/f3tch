# Changelog

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
