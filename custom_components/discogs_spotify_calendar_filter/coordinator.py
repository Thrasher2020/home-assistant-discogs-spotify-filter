"""Coordinator for the Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import logging
import re
from typing import Any

import aiohttp

from custom_components.ticketmaster.const import DOMAIN as TICKETMASTER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import SERVER_SOFTWARE, async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE_URL,
    CALENDAR_DOMAIN,
    COLLECTION_FOLDER_ID,
    COLLECTION_PER_PAGE,
    CONF_CALENDAR_ENTITY_ID,
    CONF_DISCOGS_TOKEN,
    CONF_DISCOGS_USERNAME,
    CONF_EXCLUDE_GENRES,
    CONF_EXCLUDE_STYLES,
    CONF_KEEP_RELEASES,
    CONF_SPOTIFY_PLAYLIST_ID,
    CONF_SPOTIFY_PLAYER_ENTITY_ID,
    CONF_SPOTIFY_SYNC_PLAYLIST_ID,
    DEFAULT_EXCLUDE_GENRES,
    DEFAULT_EXCLUDE_STYLES,
    DEFAULT_KEEP_RELEASES,
    DOMAIN,
    MAX_CALENDAR_LOOKAHEAD_DAYS,
    PAGE_DELAY_SECONDS,
    SCAN_INTERVAL,
    SERVICE_GET_EVENTS,
    SERVICE_GET_PLAYLIST_ITEMS,
    SKIP_CALENDAR,
    SKIP_SYNC,
    SPOTIFY_ADD_BATCH,
    SPOTIFY_DOMAIN,
    SPOTIFY_MAX_TRACKS,
    SPOTIFY_SCAN_LIMIT,
    SPOTIFY_SEARCH_ATTEMPTS,
    SPOTIFY_SEARCH_LIMIT,
    SPOTIFY_SEARCH_RETRY_DELAY_SECONDS,
    SPOTIFY_SERVICE_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_DISAMBIG_RE = re.compile(r"^(.*?)\s*\(\d+\)\s*$")
_SURNAME_THE_RE = re.compile(r"^(.+?),\s+the$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_MIN_NAME_LENGTH = 3
_FUZZY_RATIO = 0.62
_BOOKED_HORIZON_DAYS = 90

_WORD_RE = re.compile(r"[a-z0-9]+")
_PT_WORD_RE = re.compile(r"^(part|parts|pt)$")
_ROMAN_NUMERALS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
}
_TITLE_GROUP_SPLIT_RE = re.compile(r"[(\[{]")


def _title_words(name: str) -> list[str]:
    """Tokens of an album title: part/pt folded, roman -> digits, possessive
    's merged back ('Angel's Egg' -> ['angels', 'egg'])."""
    if not name:
        return []
    out: list[str] = []
    for raw in _WORD_RE.findall(name.lower()):
        if _PT_WORD_RE.match(raw):
            token = "pt"
        else:
            token = _ROMAN_NUMERALS.get(raw)
            if token is None:
                token = raw
        if token == "s" and out and out[-1] not in ("pt", "s"):
            out[-1] += "s"
        else:
            out.append(token)
    return out


def _core_title_words(name: str) -> list[str]:
    """Title tokens before any parenthetical: 'Flying Teapot (Radio Gnome
    Invisible Part 1)' -> ['flying', 'teapot']."""
    head = _TITLE_GROUP_SPLIT_RE.split(name, maxsplit=1)[0]
    return _title_words(head)


def _title_matches(title: str, album_title: str) -> bool:
    """Tolerant album-title match: identical words, identical core title
    (Deluxe Edition / subtitle parentheticals ignored), or token containment."""
    a = _title_words(title)
    b = _title_words(album_title)
    if a and a == b:
        return True
    ca = _core_title_words(title)
    cb = _core_title_words(album_title)
    if ca and cb and ca == cb:
        return True
    for short, long in ((a, b), (ca, cb)):
        if not short:
            continue
        long_set = set(long)
        short_set = set(short)
        hit = sum(1 for t in short_set if t in long_set)
        if hit >= max(1, min(len(short_set), 2)) and hit / len(short_set) >= 0.5:
            return True
    return False


def _normalize(name: str) -> str:
    """Canonical form shared by the artist list and gig candidates.

    Strips the Discogs '(N)' disambiguation suffix ('Death Angel (2)' -> 'Death
    Angel'), reorders surname-first artists ('Fall, The' -> 'The Fall'), then
    drops whitespace/punctuation. No loose 'the'-dropping: 'The Cure' matches
    'The Cure' (and 'Cure, The'), but no longer matches an unrelated 'Cure'.
    """
    name = (name or "").strip().lower()
    if match := _DISAMBIG_RE.match(name):
        name = match.group(1).strip()
    if match := _SURNAME_THE_RE.match(name):
        name = f"the {match.group(1)}"
    return _NON_ALNUM_RE.sub("", name)


def _query_artist(name: str) -> str:
    """Artist name for a Spotify query: Discogs '(N)' disambiguation removed."""
    name = (name or "").strip()
    if match := _DISAMBIG_RE.match(name):
        name = match.group(1).strip()
    return name


def _usable(name: str) -> bool:
    """Ignore canonical names too short to be trustworthy (avoid trash matches
    like a 2-letter token matching an unrelated band)."""
    return len(_normalize(name)) >= _MIN_NAME_LENGTH


def _fuzzy_matches(name: str, other: str) -> bool:
    """Loosely tolerant artist-name match (e.g. an album artist vs a candidate).

    True when the normalized names are equal, when either is a substring of the
    other (gets 'Xentrix' ~ 'Xentrix - Live in London'), or when the shorter is
    similar enough to the longer (SequenceMatcher ratio).
    """
    a = _normalize(name)
    b = _normalize(other)
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= _MIN_NAME_LENGTH and (a in b or b in a):
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return SequenceMatcher(None, shorter, longer).ratio() >= _FUZZY_RATIO


def _event_dt(value: Any) -> datetime | None:
    """Parse a calendar event start/end value into a local datetime.

    Returns None for date-only (all-day) values and anything unparseable, so
    all-day reference entries never block a gig: they carry no time window and
    are not gig commitments.
    """
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date")
    if not isinstance(value, str) or not value:
        return None
    if parsed := dt_util.parse_datetime(value):
        return dt_util.as_local(parsed)
    return None


def _event_key(event: dict[str, Any]) -> tuple[str, Any]:
    """Identify an event across the raw and enriched representations."""
    start = event.get("start")
    start = start.isoformat() if isinstance(start, datetime) else str(start)
    return (start, event.get("name"))


def _sync_result_summary(result: dict[str, Any]) -> str:
    """Render a sync/re-align result summary for logging."""
    if result.get("skipped"):
        return f"skipped ({result.get('reason')})"
    parts: list[str] = []
    for key in ("added", "already", "removed", "removed_tracks", "deduped", "errored"):
        if key in result:
            parts.append(f"{key}={result[key]}")
    return ", ".join(parts) or "no results"


class DiscogsSpotifyCalendarFilterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch the Discogs collection's artists and cross-reference upcoming gigs."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self._entry = entry
        self._session = async_get_clientsession(hass)
        self._timeout = aiohttp.ClientTimeout(connect=5, total=30)
        self._sync_lock = asyncio.Lock()
        self.last_sync_result: dict[str, Any] | None = None
        self.sync_running = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Discogs token={self._entry.data[CONF_DISCOGS_TOKEN]}",
            "User-Agent": SERVER_SOFTWARE,
        }

    async def _async_discogs_get(self, url: str) -> dict[str, Any]:
        """GET a Discogs endpoint and return the decoded JSON object."""
        async with self._session.get(
            url, headers=self._headers(), timeout=self._timeout
        ) as response:
            if response.status == 401:
                raise UpdateFailed("Discogs authentication failed - check token")
            if response.status == 429:
                raise UpdateFailed("Discogs rate limit reached - try again later")
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _async_fetch_artists(self, username: str) -> list[str]:
        """Fetch every artist name across the user's collection."""
        artists: set[str] = set()
        page = 1
        while True:
            data = await self._async_discogs_get(
                f"{API_BASE_URL}/users/{username}/collection/"
                f"folders/{COLLECTION_FOLDER_ID}/releases"
                f"?per_page={COLLECTION_PER_PAGE}&page={page}"
            )
            for release in data.get("releases", []):
                for artist in release.get("basic_information", {}).get("artists", []):
                    if name := artist.get("name"):
                        artists.add(name)
            pages = int(data.get("pagination", {}).get("pages") or 1)
            if page >= pages:
                break
            page += 1
            await asyncio.sleep(PAGE_DELAY_SECONDS)
        return sorted(artists)

    def _playlist_ids(self) -> list[str]:
        """Return the configured Spotify playlist ids, in any legacy shape."""
        value = self._entry.data.get(CONF_SPOTIFY_PLAYLIST_ID)
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [item for item in value if item]
        return []

    async def _async_fetch_spotify_artists(self) -> list[str]:
        """Fetch artist names from the configured Spotify playlists via spotifyplus."""
        player_entity_id = self._entry.data.get(CONF_SPOTIFY_PLAYER_ENTITY_ID)
        playlist_ids = self._playlist_ids()
        if not playlist_ids or not player_entity_id:
            return []
        if not self.hass.services.has_service(SPOTIFY_DOMAIN, SERVICE_GET_PLAYLIST_ITEMS):
            _LOGGER.debug(
                "SpotifyPlus unavailable - skipping Spotify playlist artist match"
            )
            return []

        artists: set[str] = set()
        for playlist_id in playlist_ids:
            try:
                response = await asyncio.wait_for(
                    self.hass.services.async_call(
                        SPOTIFY_DOMAIN,
                        SERVICE_GET_PLAYLIST_ITEMS,
                        {"playlist_id": playlist_id, "limit_total": SPOTIFY_MAX_TRACKS},
                        target={"entity_id": player_entity_id},
                        blocking=True,
                        return_response=True,
                    ),
                    timeout=SPOTIFY_SERVICE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("Spotify playlist fetch timed out for %s", playlist_id)
                continue
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Spotify playlist fetch failed for %s: %s", playlist_id, err
                )
                continue

            result = (response or {}).get("result") or {}
            for item in result.get("items") or []:
                track = item.get("track") or item.get("item") or {}
                for artist in track.get("artists") or []:
                    if name := artist.get("name"):
                        artists.add(name)
        return sorted(artists)

    def _secondary_calendar_entity_id(self) -> str | None:
        """Return the configured secondary calendar entity id, if any."""
        entity_id = self._entry.data.get(CONF_CALENDAR_ENTITY_ID)
        if not entity_id or entity_id == SKIP_CALENDAR:
            return None
        return entity_id

    async def _async_fetch_secondary_calendar(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Return {date, summary} entries from the secondary calendar in range."""
        entity_id = self._secondary_calendar_entity_id()
        if not entity_id:
            return []
        if not self.hass.services.has_service(CALENDAR_DOMAIN, SERVICE_GET_EVENTS):
            _LOGGER.debug(
                "Calendar service unavailable - skipping secondary calendar match"
            )
            return []

        try:
            response = await asyncio.wait_for(
                self.hass.services.async_call(
                    CALENDAR_DOMAIN,
                    SERVICE_GET_EVENTS,
                    {
                        "start_date_time": start.isoformat(),
                        "end_date_time": end.isoformat(),
                    },
                    target={"entity_id": entity_id},
                    blocking=True,
                    return_response=True,
                ),
                timeout=SPOTIFY_SERVICE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as err:
            _LOGGER.warning("Secondary calendar fetch timed out: %s", entity_id)
            return []
        except HomeAssistantError as err:
            _LOGGER.warning("Secondary calendar fetch failed: %s", err)
            return []

        result = (response or {}).get(entity_id) or {}
        entries: list[dict[str, Any]] = []
        for event in result.get("events") or []:
            summary = event.get("summary")
            if not summary:
                continue
            start_dt = _event_dt(event.get("start"))
            if start_dt is None:
                continue
            end_dt = _event_dt(event.get("end")) or start_dt + timedelta(hours=1)
            entries.append({"summary": summary, "start": start_dt, "end": end_dt})
        return entries

    def _gig_blocked(
        self, gig_start: datetime, entries: list[dict[str, Any]]
    ) -> bool:
        """Return True when a gig overlaps a timed reference-calendar event.

        Both the gig (taken to be 3 hours long, matching the calendar entity)
        and the reference events are treated as blocks on the calendar. A gig is
        hidden when its time window overlaps one, not merely when its band name
        turns up anywhere on the reference calendar.
        """
        start = dt_util.as_local(gig_start)
        end = start + timedelta(hours=3)
        for entry in entries:
            ref_start = entry.get("start")
            ref_end = entry.get("end")
            if not isinstance(ref_start, datetime) or not isinstance(
                ref_end, datetime
            ):
                continue
            if start < ref_end and end > ref_start:
                return True
        return False

    def _band_attended(
        self, candidates: list[str], entries: list[dict[str, Any]]
    ) -> bool:
        """Return True when any of the gig's bands is already being attended.

        A band counts as attended when any candidate name loosely matches a
        timed reference-calendar event. You only go to a band once, so every
        other listing for it is hidden. All-day reference events never match:
        they are not gig commitments.
        """
        for candidate in candidates:
            for entry in entries:
                if _fuzzy_matches(candidate, entry["summary"]):
                    return True
        return False

    def _get_gig_events(self) -> list[dict[str, Any]]:
        """Collect every upcoming event currently held by Gig Finder."""
        events: list[dict[str, Any]] = []
        for coordinator in (self.hass.data.get(TICKETMASTER_DOMAIN) or {}).values():
            if coordinator.data:
                events.extend(coordinator.data)
        return events

    def _calendar_window(
        self, events: list[dict[str, Any]]
    ) -> tuple[datetime, datetime] | None:
        """Return the [now, last upcoming gig] window for calendar lookup."""
        if not self._secondary_calendar_entity_id():
            return None
        now = dt_util.now()
        starts = [
            dt_util.as_local(event["start"])
            for event in events
            if isinstance(event.get("start"), datetime)
        ]
        future = [start for start in starts if start >= now]
        end = max(future) if future else now + timedelta(days=90)
        end = min(end, now + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS))
        return (now, end)

    def _collection_sync_playlist_id(self) -> str | None:
        """Return the configured collection sync target playlist id, if any."""
        playlist_id = self._entry.data.get(CONF_SPOTIFY_SYNC_PLAYLIST_ID)
        if not playlist_id or playlist_id == SKIP_SYNC:
            return None
        return playlist_id

    def _exclusion_set(self, key: str, default: str) -> set[str]:
        """Return the lower-cased exclusion values configured for a key."""
        raw = self._entry.data.get(key)
        if raw is None:
            raw = default
        return {item.strip().lower() for item in str(raw).split(",") if item.strip()}

    async def _async_fetch_collection_releases(
        self, username: str
    ) -> list[dict[str, Any]]:
        """Fetch collection releases, skipping excluded genres and styles."""
        excluded_genres = self._exclusion_set(CONF_EXCLUDE_GENRES, DEFAULT_EXCLUDE_GENRES)
        excluded_styles = self._exclusion_set(CONF_EXCLUDE_STYLES, DEFAULT_EXCLUDE_STYLES)
        keep_releases = self._exclusion_set(CONF_KEEP_RELEASES, DEFAULT_KEEP_RELEASES)
        releases: list[dict[str, Any]] = []
        excluded_releases: list[str] = []
        page = 1
        while True:
            data = await self._async_discogs_get(
                f"{API_BASE_URL}/users/{username}/collection/"
                f"folders/{COLLECTION_FOLDER_ID}/releases"
                f"?per_page={COLLECTION_PER_PAGE}&page={page}"
            )
            for release in data.get("releases", []):
                basic = release.get("basic_information") or {}
                title = (basic.get("title") or "").strip()
                genres = [
                    (genre or "").strip()
                    for genre in (basic.get("genres") or [])
                    if genre
                ]
                styles = [
                    (style or "").strip()
                    for style in (basic.get("styles") or [])
                    if style
                ]
                if (
                    any(genre.lower() in excluded_genres for genre in genres)
                    or any(style.lower() in excluded_styles for style in styles)
                ) and title.lower() not in keep_releases:
                    artist_label = ", ".join(
                        (artist.get("name") or "").strip()
                        for artist in (basic.get("artists") or [])
                        if isinstance(artist, dict) and artist.get("name")
                    )
                    excluded_releases.append(
                        f"{artist_label} - {title}" if artist_label else title
                    )
                    continue
                artists = [
                    (artist.get("name") or "").strip()
                    for artist in (basic.get("artists") or [])
                    if isinstance(artist, dict) and artist.get("name")
                ]
                formats = [
                    (fmt.get("name") or "").strip()
                    for fmt in (basic.get("formats") or [])
                    if isinstance(fmt, dict) and fmt.get("name")
                ]
                releases.append(
                    {
                        "title": basic.get("title"),
                        "artists": artists,
                        "year": basic.get("year"),
                        "formats": formats,
                    }
                )
            pages = int(data.get("pagination", {}).get("pages") or 1)
            if page >= pages:
                break
            page += 1
            await asyncio.sleep(PAGE_DELAY_SECONDS)
        if excluded_releases:
            _LOGGER.warning(
                "Discogs collection sync excluded %d release(s): %s",
                len(excluded_releases),
                "; ".join(excluded_releases),
            )
        return releases

    async def _async_spotify_call(self, service: str, data: dict[str, Any]) -> Any:
        """Call a spotifyplus service on the configured player and return its result.

        Raises HomeAssistantError on missing player, missing service or failure.
        """
        player_entity_id = self._entry.data.get(CONF_SPOTIFY_PLAYER_ENTITY_ID)
        if not player_entity_id:
            raise HomeAssistantError("No Spotify player is configured")
        if not self.hass.services.has_service(SPOTIFY_DOMAIN, service):
            raise HomeAssistantError(
                f"SpotifyPlus service {SPOTIFY_DOMAIN}.{service} is unavailable"
            )
        try:
            response = await asyncio.wait_for(
                self.hass.services.async_call(
                    SPOTIFY_DOMAIN,
                    service,
                    data,
                    target={"entity_id": player_entity_id},
                    blocking=True,
                    return_response=True,
                ),
                timeout=SPOTIFY_SERVICE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as err:
            raise HomeAssistantError(f"Spotify {service} timed out") from err
        except HomeAssistantError as err:
            raise HomeAssistantError(f"Spotify {service} failed: {err}") from err
        return (response or {}).get("result")

    async def _async_resolve_spotify_album(
        self, release: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Search Spotify for the release's album; return {id, uri, name} or None.

        Searches on the artist plus the core title (subtitle parentheticals
        dropped), then requires a tolerant title match and a loose artist match,
        preferring the result whose release date contains the Discogs year.
        """
        title = release.get("title")
        artists = release.get("artists") or []
        if not title or not artists:
            return None
        core_title = " ".join(_core_title_words(title)[:5])
        criteria = f"{_query_artist(artists[0])} {core_title or title}"
        result: Any = None
        for attempt in range(SPOTIFY_SEARCH_ATTEMPTS):
            try:
                result = await self._async_spotify_call(
                    "search_albums",
                    {"criteria": criteria, "limit": SPOTIFY_SEARCH_LIMIT},
                )
                break
            except HomeAssistantError:
                if attempt == SPOTIFY_SEARCH_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(
                    SPOTIFY_SEARCH_RETRY_DELAY_SECONDS * (attempt + 1)
                )
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            albums = result.get("albums") if isinstance(result, dict) else None
            items = albums.get("items") if isinstance(albums, dict) else None
        if not items:
            return None

        year = release.get("year")
        best: dict[str, Any] | None = None
        best_score = 0.0
        for album in items:
            if not isinstance(album, dict):
                continue
            album_title = album.get("name")
            album_artists = [
                artist.get("name", "")
                for artist in (album.get("artists") or [])
                if isinstance(artist, dict) and artist.get("name")
            ]
            if not album_title or not album_artists:
                continue
            if not _title_matches(title, album_title):
                continue
            if not any(
                _fuzzy_matches(candidate, album_artist)
                for candidate in artists
                for album_artist in album_artists
            ):
                continue
            score = 0.5
            if year and str(year) in (album.get("release_date") or ""):
                score += 0.5
            if score > best_score:
                best_score = score
                best = {
                    "id": album.get("id"),
                    "uri": album.get("uri"),
                    "name": album_title,
                }
        return best

    async def _async_album_track_uris(self, album_id: str) -> list[str]:
        """Return all track URIs for a Spotify album."""
        result = await self._async_spotify_call(
            "get_album_tracks",
            {"album_id": album_id, "limit_total": SPOTIFY_MAX_TRACKS},
        )
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            tracks = result.get("tracks") if isinstance(result, dict) else None
            items = tracks.get("items") if isinstance(tracks, dict) else None
        uris: list[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            uri = item.get("uri")
            if not uri and item.get("id"):
                uri = f"spotify:track:{item['id']}"
            if uri:
                uris.append(uri)
        return uris

    async def _async_playlist_tracks(self, playlist_id: str) -> list[dict[str, Any]]:
        """Return the playlist's tracks as {album_id, album_name, uri}.

        Scans the full playlist so the "already present" check sees every track,
        not just the first page - otherwise re-runs re-add what was invisible.
        """
        result = await self._async_spotify_call(
            SERVICE_GET_PLAYLIST_ITEMS,
            {"playlist_id": playlist_id, "limit_total": SPOTIFY_SCAN_LIMIT},
        )
        items = result.get("items") if isinstance(result, dict) else None
        tracks: list[dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            track = item.get("track") or item.get("item") or {}
            if not isinstance(track, dict):
                continue
            album_raw = track.get("album")
            album = album_raw if isinstance(album_raw, dict) else {}
            uri = track.get("uri") or item.get("uri")
            if not uri and track.get("id"):
                uri = f"spotify:track:{track['id']}"
            tracks.append(
                {
                    "album_id": album.get("id"),
                    "album_name": album.get("name"),
                    "uri": uri,
                }
            )
        return tracks

    async def async_sync_collection(
        self, playlist_id: str | None = None
    ) -> dict[str, Any]:
        """Add the collection's albums' tracks to the target playlist.

        Albums already present in the playlist (matched by Spotify album id) are
        skipped. Returns a result summary; never raises.
        """
        if self._sync_lock.locked():
            return {"op": "sync", "skipped": True, "reason": "already_running"}
        async with self._sync_lock:
            result: dict[str, Any] = {
                "op": "sync",
                "playlist_id": playlist_id,
                "added": 0,
                "already": 0,
                "errored": 0,
                "total": 0,
                "errors": [],
            }
            self.sync_running = True
            self.async_update_listeners()
            try:
                playlist_id = playlist_id or self._collection_sync_playlist_id()
                if not playlist_id:
                    raise HomeAssistantError(
                        "No collection sync playlist is configured"
                    )
                result["playlist_id"] = playlist_id
                username = self._entry.data.get(CONF_DISCOGS_USERNAME)
                if not username:
                    raise HomeAssistantError("Discogs username is missing")

                releases = await self._async_fetch_collection_releases(username)
                result["total"] = len(releases)
                existing_albums = {
                    track["album_id"]
                    for track in await self._async_playlist_tracks(playlist_id)
                    if track["album_id"]
                }

                for release in releases:
                    try:
                        album = await self._async_resolve_spotify_album(release)
                    except HomeAssistantError as err:
                        self._sync_error(
                            result, release.get("title"), f"search failed: {err}"
                        )
                        continue
                    if album is None or not album.get("id"):
                        self._sync_error(result, release.get("title"), "not found on Spotify")
                        continue
                    if album["id"] in existing_albums:
                        result["already"] += 1
                        continue
                    try:
                        uris = await self._async_album_track_uris(album["id"])
                        if not uris:
                            raise HomeAssistantError("album has no available tracks")
                        for start in range(0, len(uris), SPOTIFY_ADD_BATCH):
                            await self._async_spotify_call(
                                "playlist_items_add",
                                {
                                    "playlist_id": playlist_id,
                                    "uris": ",".join(uris[start : start + SPOTIFY_ADD_BATCH]),
                                },
                            )
                    except HomeAssistantError as err:
                        self._sync_error(result, album.get("name"), f"add failed: {err}")
                        continue
                    existing_albums.add(album["id"])
                    result["added"] += 1
            except HomeAssistantError as err:
                self._sync_error(result, "sync", str(err))
            except Exception as err:  # noqa: BLE001 - report and continue
                _LOGGER.exception("Unexpected error during collection sync")
                self._sync_error(result, "sync", f"unexpected error: {err}")
            finally:
                self.sync_running = False
                result["last_run"] = dt_util.now()
                self.last_sync_result = result
                self.async_update_listeners()
                _LOGGER.info(
                    "Collection sync complete: %s", _sync_result_summary(result)
                )
            return result

    async def async_realign_playlist(
        self, playlist_id: str | None = None
    ) -> dict[str, Any]:
        """Make the target playlist an exact mirror of the collection.

        Removes every track whose album is not in the collection, then adds the
        tracks of any collection album that is missing. Returns a summary; never raises.
        """
        if self._sync_lock.locked():
            return {"op": "realign", "skipped": True, "reason": "already_running"}
        async with self._sync_lock:
            result: dict[str, Any] = {
                "op": "realign",
                "playlist_id": playlist_id,
                "added": 0,
                "already": 0,
                "removed": 0,
                "removed_tracks": 0,
                "deduped": 0,
                "removals_skipped": False,
                "errored": 0,
                "total": 0,
                "errors": [],
            }
            self.sync_running = True
            self.async_update_listeners()
            try:
                playlist_id = playlist_id or self._collection_sync_playlist_id()
                if not playlist_id:
                    raise HomeAssistantError(
                        "No collection sync playlist is configured"
                    )
                result["playlist_id"] = playlist_id
                username = self._entry.data.get(CONF_DISCOGS_USERNAME)
                if not username:
                    raise HomeAssistantError("Discogs username is missing")

                releases = await self._async_fetch_collection_releases(username)
                result["total"] = len(releases)

                collection_albums: dict[str, str] = {}
                search_failed = 0
                for release in releases:
                    try:
                        album = await self._async_resolve_spotify_album(release)
                    except HomeAssistantError as err:
                        search_failed += 1
                        self._sync_error(
                            result, release.get("title"), f"search failed: {err}"
                        )
                        continue
                    if album is None or not album.get("id"):
                        self._sync_error(result, release.get("title"), "not found on Spotify")
                        continue
                    collection_albums.setdefault(album["id"], album["name"])

                tracks = await self._async_playlist_tracks(playlist_id)

                extra_albums: set[str] = set()
                remove_uris: list[str] = []
                if search_failed:
                    result["removals_skipped"] = True
                    _LOGGER.warning(
                        "Re-align: skipping playlist removals because %s collection "
                        "release(s) could not be resolved, so the collection view is "
                        "incomplete",
                        search_failed,
                    )
                else:
                    for track in tracks:
                        album_id = track["album_id"]
                        if album_id and album_id not in collection_albums:
                            extra_albums.add(album_id)
                            if track["uri"]:
                                remove_uris.append(track["uri"])
                        elif not album_id and track["uri"]:
                            extra_albums.add("__unknown__")
                            remove_uris.append(track["uri"])

                kept_uris: set[str] = set()
                remove_uris_set = set(remove_uris)
                dup_count = 0
                for track in tracks:
                    uri = track.get("uri")
                    if not uri or uri in remove_uris_set:
                        continue
                    if uri in kept_uris:
                        remove_uris.append(uri)
                        dup_count += 1
                    else:
                        kept_uris.add(uri)
                result["deduped"] = dup_count

                for start in range(0, len(remove_uris), SPOTIFY_ADD_BATCH):
                    await self._async_spotify_call(
                        "playlist_items_remove",
                        {
                            "playlist_id": playlist_id,
                            "uris": ",".join(remove_uris[start : start + SPOTIFY_ADD_BATCH]),
                        },
                    )
                result["removed"] = len(extra_albums)
                result["removed_tracks"] = len(remove_uris)

                existing_albums = {
                    track["album_id"] for track in tracks if track["album_id"]
                }
                for album_id, album_name in collection_albums.items():
                    if album_id in existing_albums:
                        result["already"] += 1
                        continue
                    try:
                        uris = await self._async_album_track_uris(album_id)
                        if not uris:
                            raise HomeAssistantError("album has no available tracks")
                        for start in range(0, len(uris), SPOTIFY_ADD_BATCH):
                            await self._async_spotify_call(
                                "playlist_items_add",
                                {
                                    "playlist_id": playlist_id,
                                    "uris": ",".join(uris[start : start + SPOTIFY_ADD_BATCH]),
                                },
                            )
                    except HomeAssistantError as err:
                        self._sync_error(result, album_name, f"add failed: {err}")
                        continue
                    existing_albums.add(album_id)
                    result["added"] += 1
            except HomeAssistantError as err:
                self._sync_error(result, "realign", str(err))
            except Exception as err:  # noqa: BLE001 - report and continue
                _LOGGER.exception("Unexpected error during playlist re-align")
                self._sync_error(result, "realign", f"unexpected error: {err}")
            finally:
                self.sync_running = False
                result["last_run"] = dt_util.now()
                self.last_sync_result = result
                self.async_update_listeners()
                _LOGGER.info(
                    "Playlist re-align complete: %s", _sync_result_summary(result)
                )
            return result

    @staticmethod
    def _sync_error(result: dict[str, Any], item: Any, reason: str) -> None:
        """Record one failure in a sync result summary."""
        result["errored"] += 1
        result["errors"].append(f"{item}: {reason}")

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the collection artist list and compute matched gigs."""
        username = self._entry.data.get(CONF_DISCOGS_USERNAME)
        if not username:
            raise UpdateFailed("Discogs username is missing from the config entry")

        artists = await self._async_fetch_artists(username)
        spotify_artists = await self._async_fetch_spotify_artists()

        variant_to_artists: dict[str, set[str]] = {}
        for artist in set(artists) | set(spotify_artists):
            if not _usable(artist):
                continue
            variant_to_artists.setdefault(_normalize(artist), set()).add(artist)

        raw_events = self._get_gig_events()
        window = self._calendar_window(raw_events)
        calendar_entries = (
            await self._async_fetch_secondary_calendar(*window) if window else []
        )

        events: list[dict[str, Any]] = []
        matched_events: list[dict[str, Any]] = []
        for event in raw_events:
            candidates = [
                candidate
                for candidate in (event.get("lineup") or [])
                if isinstance(candidate, str)
            ]
            if not candidates and event.get("name"):
                candidates = [event["name"]]

            matched: set[str] = set()
            for candidate in candidates:
                if not _usable(candidate):
                    continue
                matched.update(variant_to_artists.get(_normalize(candidate), set()))

            venue = event.get("venue") or {}
            start = event.get("start")
            calendar_date = None
            if (
                calendar_entries
                and isinstance(start, datetime)
                and start < dt_util.now() + timedelta(days=_BOOKED_HORIZON_DAYS)
                and (
                    self._gig_blocked(start, calendar_entries)
                    or self._band_attended(candidates, calendar_entries)
                )
            ):
                calendar_date = dt_util.as_local(start).date()
            events.append(
                {
                    "name": event.get("name"),
                    "bands": sorted(matched),
                    "in_collection": bool(matched),
                    "in_calendar": calendar_date is not None,
                    "calendar_date": calendar_date.isoformat() if calendar_date else None,
                    "venue": venue.get("name"),
                    "city": (venue.get("city") or {}).get("name"),
                    "start": start.isoformat() if isinstance(start, datetime) else start,
                    "url": event.get("url"),
                    "source": event.get("source"),
                }
            )
            if matched:
                matched_events.append(event)

        events.sort(key=lambda match: match.get("start") or "")
        matches = [event for event in events if event["in_collection"]]

        in_calendar_events = []
        if calendar_entries:
            in_calendar_events = [event for event in events if event["in_calendar"]]
            dropped = {_event_key(event) for event in in_calendar_events}
            events = [event for event in events if not event["in_calendar"]]
            matches = [event for event in matches if not event["in_calendar"]]
            matched_events = [
                event for event in matched_events if _event_key(event) not in dropped
            ]

        return {
            "artists": artists,
            "spotify_artists": spotify_artists,
            "matches": matches,
            "events": events,
            "calendar_events": matched_events,
            "in_calendar_events": in_calendar_events,
        }