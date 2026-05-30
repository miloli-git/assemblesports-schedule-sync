# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- The scheduled workflow is now **gated on initialisation**: the job only runs once `TEAM_ID`, `TEAM_NAME`, `CLUB_SLUG`, and `SEASON_KEY` are all set as GitHub Variables. Before that it is *skipped* rather than failed, so a freshly forked but un-configured repo no longer fails every scheduled run and emails the owner an hourly stream of failure notifications on game days. Setting the four Variables activates the schedule automatically — no workflow edit required. See DESIGN.md §5.11.

## [1.0.0] - 2026-03-24

Initial public release.

### Added
- GitHub Actions workflow that polls the AssembleSports API for a team's schedule on game days (hourly, Thursday-Sunday).
- Google Calendar sync: creates, updates, and deletes events as the schedule changes.
- Ladder standings tracking, with updates posted when results come in.
- Discord notifications for schedule changes (time, venue, cancellations) and ladder movements.
- Pluggable notification layer (`Notifier` base, `DiscordNotifier`, `NullNotifier`) — add a channel by subclassing and registering it in `build_notifier()`.
- Configurable for any AssembleSports league via `ASSEMBLESPORTS_BASE_URL`, `ASSEMBLESPORTS_LIVE_URL`, `TIMEZONE`, and `SEASON_KEY` (no hardcoded team or timezone defaults).
- `cleanup_duplicates.py` utility for removing duplicate calendar events.
- `README.md` setup guide, `SEASON_SETUP.md` season-config guide, and `DESIGN.md` architecture reference.

### Known Issues
- The workflow YAML (`.github/workflows/schedule-checker-enhanced.yml`) needs a manual edit: read `DEBUG_DATES` from `vars` (not `secrets`), inject `ASSEMBLESPORTS_BASE_URL` / `ASSEMBLESPORTS_LIVE_URL` / `TIMEZONE` into the run steps, and replace the hardcoded `2026` cache key with `${{ vars.SEASON_KEY }}`. See DESIGN.md §7.

[1.0.0]: https://github.com/miloli-git/assemblesports-schedule-sync/releases/tag/v1.0.0
