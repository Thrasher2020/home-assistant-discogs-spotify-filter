# Changelog

## 0.3.0

**Renamed integration.** "Discogs Gigs" (`discogs_gigs`) is now **Discogs/Spotify Calendar Filter** (`discogs_spotify_calendar_filter`).

### Changed (breaking)

- Integration renamed and re-branded throughout: folder, `manifest.json`, domain, entity IDs, class names, and UI strings.
- Documentation and issue tracker moved to `https://github.com/Thrasher2020/home-assistant-discogs-spotify-filter`.
- Existing config entries are not migrated: **remove the old "Discogs Gigs" entry, restart, and re-add the integration** (the Discogs token is re-entered).

### Added

- Project scaffolding for distribution: `README.md`, `CHANGELOG.md`, `LICENSE` (Apache-2.0), `hacs.json`, `.github/workflows/hacs.yml`, `.github/workflows/hassfest.yaml`.

## 0.2.0 — last release under the "Discogs Gigs" name

- Match upcoming Gig Finder events against the Discogs collection artist list.
- Optional match against artist names extracted from selected Spotify playlists (via SpotifyPlus).
- Curated calendar of collection-matched gigs, tagged by source (`TM`, `FS`, `SK`).
- Optional secondary-calendar cross-reference: loosely matching same-day gigs are treated as booked and hidden.
- Sensors: highlighted gigs, upcoming events (with a Collection Only switch filter), collection sync status.
- Collection sync: adds the collection's albums' tracks to a Spotify playlist, skipping ones already present.
- Playlist re-align: makes a playlist an exact mirror of the collection (adds missing, removes extra, dedupes).
- Genre/style exclusion rules for the collection sync, with a keep-list override.