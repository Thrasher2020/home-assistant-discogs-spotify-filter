# Discogs/Spotify Calendar Filter for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Surf the gigs around you that actually matter to *you*. **Discogs/Spotify Calendar Filter** watches the live music listings held by [Gig Finder](https://github.com/Thrasher2020/home-assistant-gigfinder), then:

- highlights every upcoming gig whose band is in **your Discogs collection** or **your Spotify playlists**;
- exposes them as a curated **calendar** and **sensors**;
- optionally cross-references a **secondary calendar** (e.g. your iCloud/Google calendar) so gigs that clash with its events — or are by a band already booked in it — are hidden;
- can **sync your Discogs collection** to a Spotify playlist, and re-align that playlist to be an exact mirror of the collection.

## Prerequisites

| Requirement | Why | Link |
|---|---|---|
| **Gig Finder** integration | Provides the upstream live-music listings (Ticketmaster / Fatsoma / Skiddle) | [home-assistant-gigfinder](https://github.com/Thrasher2020/home-assistant-gigfinder) |
| **Discogs personal access token** | Reads your collection to build the artist list | discogs.com → Settings → Developers |
| **SpotifyPlus** *(optional)* | Only needed for matching against Spotify playlists and collection sync | `spotifyplus` player integration |
| **Secondary calendar** *(optional)* | Only needed to hide gigs that clash with it or repeat a band you are already seeing | any HA calendar entity |

## Installation

### Via HACS (recommended)

1. Open HACS in your Home Assistant sidebar.
2. Go to **Integrations → ⋮ → Custom repositories**.
3. Add `https://github.com/Thrasher2020/home-assistant-discogs-spotify-filter` with category **Integration**.
4. Click **Explore & download repositories**, search for **Discogs/Spotify Calendar Filter**, and download it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/discogs_spotify_calendar_filter` folder into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Discogs/Spotify Calendar Filter**.
3. Enter your **Discogs personal access token**.
4. *(Optional)* pick any of your **Spotify playlists** whose artists should also match.
5. *(Optional)* pick a **secondary calendar** to cross-reference.

Then open **Options** to tune everything:

| Option | Description |
|---|---|
| Spotify playlists | Multi-select of playlists whose artists should match upcoming gigs |
| Secondary calendar | Calendar to cross-reference; gigs overlapping its events or by an attended band are hidden |
| Collection sync playlist | Target playlist for `sync_collection` / `realign_playlist` |
| Exclude Discogs genres | Comma-separated genres to skip when syncing the collection |
| Exclude Discogs styles | Comma-separated styles to skip when syncing the collection |
| Keep these releases | Comma-separated Discogs titles kept even when their genre/style matches an exclusion |

## Entities

| Entity | Type | Description |
|---|---|---|
| `sensor.highlighted_gigs` | Sensor | Number of upcoming gigs matching your collection or playlists (with a `matches` attribute) |
| `sensor.upcoming_events` | Sensor | Number of upcoming events, optionally filtered to collection bands only |
| `sensor.collection_sync_status` | Sensor | Summary of the last collection sync / playlist re-align |
| `switch.collection_only` | Switch | Toggles whether `upcoming_events` only shows collection bands |
| `button.sync_collection` | Button | Adds your Discogs collection's albums to the configured Spotify playlist |
| `button.realign_playlist` | Button | Makes the target playlist an exact mirror of the collection |
| `calendar.<home>` | Calendar | Curated calendar of collection-matched gigs, tagged by source (`TM`/`FS`/`SK`) |

## Services

- `discogs_spotify_calendar_filter.sync_collection` — add the collection albums to a Spotify playlist (defaults to the configured sync playlist; optional `playlist_id`).
- `discogs_spotify_calendar_filter.realign_playlist` — make a playlist an exact mirror of the collection (optional `playlist_id`).

## Example automation — gig by an artist you own

```yaml
automation:
  - alias: "Collection artist gig on"
    trigger:
      - platform: state
        entity_id: sensor.highlighted_gigs
    action:
      - service: notify.mobile_app_phone
        data:
          title: "Gig by a band from your collection!"
          message: "{{ state_attr('sensor.highlighted_gigs', 'matches') | list }}"
```

## Troubleshooting

- **No matches** — make sure Gig Finder is set up and producing events, and that your Discogs collection is public to the token's account.
- **Spotify features missing** — SpotifyPlus must be configured with a player; check the Services list for `spotifyplus`.
- **`in_calendar` hidden gigs** — a gig is hidden when its time window overlaps an event on your secondary calendar, or when it is by a band you are already seeing there (all-day reference events are ignored).
- Enable debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.discogs_spotify_calendar_filter: debug
```

## License

Apache-2.0. See [LICENSE](LICENSE).