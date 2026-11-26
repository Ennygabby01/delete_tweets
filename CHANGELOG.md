# Changelog

## [2.0.2] - 2026-11-26

### Fixed

- Smaller runner batches no longer trigger a full cooldown while X still reports
  available requests. Real rate-limit cooldowns and restart review remain enforced.

## [2.0.1] - 2026-11-26

First published 2.x release, including the 2.0 rewrite described below.

### Added

- Saved newest-first/oldest-first post order in Filters, applied to lists and frozen
  into manual and scheduled run previews. Existing runs keep their original order.
- Developer-tool deletion runner with a fixed reviewed post cap, batches paced by
  X's reset headers, progress, pause/resume and stop controls.
- Permanent-deletion warning and expandable list of included posts at confirmation.
- GitHub CI for locked checks, mocked provider/SQLite tests and dependency audits
  on Python 3.12 and 3.15, with pinned actions and read-only permissions.

### Changed

- Schema v3 persists batch counts and cooldowns. Existing schema-v1/v2 databases
  receive a private pre-upgrade backup. Restarts pause runners for explicit review.
- Scheduled runners can continue after a known rate-limit cooldown; billing,
  permission and uncertain results still pause. No credit-balance API polling added.
- Next-run sidebar uses the shared gray surface, with an allowance meter and a
  matching mobile drawer. Preserved refined half-pixel borders and input focus styles.
- Viewport-height internal workspace scrolling on larger desktop/tablet layouts,
  with modal scroll restoration and distinct Settings section icons.

### Fixed

- Mobile X-read switch alignment, filter labels and explanatory tooltip placement.

## 2.0.0 - 2026-11-25

Prepared locally and included in the 2.0.1 release; no separate 2.0.0 tag.

A rewrite of the local app, replacing the original credential form and in-memory
deletion tracking with X sign-in, SQLite and durable runs.

### Added

- X OAuth 2.0 sign-in and reconnect in the current tab.
- Local SQLite storage for posts, filters, settings, sessions and run history.
- Optional post-read permission, off by default and enforced by the backend.
- Manual account sync and older-page loading for users without an archive.
- Run previews, explicit confirmation, pause/cancel and recovery of uncertain results.
- Date-grouped Activity history with chevron accordions and server-side pagination.
- Clear run and post statuses, plus striped review and deletion progress indicators.
- A mobile run drawer with a draggable header and circular selection button.
- Glass pill navigation, solid active icons, pagination chevrons and app logo/favicon.
- Make commands for setup, the combined frontend/API server, development and checks.
- MIT license with copyright retained by GBT3K.
- Installation guide covering requirements, Ubuntu, macOS, Windows through WSL,
  HTTPS hosting, private server access, systemd, aaPanel/cPanel and backup recovery.
- Public HTTPS origin configuration, secure session cookies, explicit trusted proxy
  IPs and a pinned owner account for personal server installations.

### Changed

- Reorganized the backend and frontend into focused modules; application routes use `/v1`.
- Replaced the old Tweepy v1.1 deletion path with X API v2 and OAuth user tokens.
- Archive-based deletion no longer needs a post-read preflight. Reads remain optional;
  write requests can still require X API credits.
- Preserved existing local archives and filters during migration. Root legacy
  credentials are no longer used for browser sign-in.
- Reworked Activity, Settings, review and mobile controls with fewer borders and
  plain product copy.

### Fixed

- Missing HTML-entity decoding in displayed post text.
- Session activity missing scroll/touch use, and stale unauthorized responses
  affecting a newer session.
- Temporary token-renewal failures revoking local sessions. Uncertain renewal now
  requires reconnect without silently reusing a possibly rotated refresh token.
- Generic X denial messages that obscured app-access, billing, IP and usage failures.
- Copied virtual-environment paths and startup failure when the preferred port is busy.
- Deployment configuration coupling the public callback URL to the backend port.
  Hosted deployments retain a fixed upstream port; local startup still finds a free port.

### Security and migration

- Added server-side owner sessions, CSRF/origin/host checks, bounded requests and
  uploads, encrypted OAuth credentials and clean public errors.
- Added durable sending claims and idempotent previews. Interrupted deletion results
  stay uncertain until checked; restarts do not blindly repeat requests.
- Private configuration, archives, databases and backups remain outside Git.
- Python 3.12 or newer is required. Back up the data directory and matching `.env` before upgrading.

### X API costs

X now uses API credits for requests. This app does not include those credits, and
X Premium is separate. Using a local archive avoids post-read requests; it does
not remove deletion charges. Check [X pricing](https://docs.x.com/x-api/getting-started/pricing)
for current costs. Rate limits and credit balance are separate controls.

## [1.0.0] - 2026-02-27

Originally tagged `v1.0` in this repository.

- Local FastAPI app for reviewing an X archive and deleting selected posts.
- Domain and keyword filtering, search, page selection and bulk selection.
- Browser archive upload and responsive navigation.
- Tweepy/OAuth 1.0a credentials, persisted locally with filters in JSON files.
- In-memory tracking of deleted IDs and paced deletion requests.

[2.0.2]: https://github.com/Ennygabby01/delete_tweets/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/Ennygabby01/delete_tweets/compare/v1.0...v2.0.1
[1.0.0]: https://github.com/Ennygabby01/delete_tweets/tree/v1.0
