"""Constants for the Discogs/Spotify Calendar Filter integration."""

from datetime import timedelta

DOMAIN = "discogs_spotify_calendar_filter"
NAME = "Discogs/Spotify Calendar Filter"

CONF_DISCOGS_TOKEN = "discogs_token"
CONF_DISCOGS_USERNAME = "discogs_username"

CONF_SPOTIFY_PLAYLIST_ID = "spotify_playlist_id"
CONF_SPOTIFY_PLAYLIST_NAME = "spotify_playlist_name"
CONF_SPOTIFY_PLAYER_ENTITY_ID = "spotify_player_entity_id"

CONF_CALENDAR_ENTITY_ID = "calendar_entity_id"

CONF_SPOTIFY_SYNC_PLAYLIST_ID = "spotify_sync_playlist_id"
CONF_SPOTIFY_SYNC_PLAYLIST_NAME = "spotify_sync_playlist_name"

CONF_EXCLUDE_GENRES = "exclude_genres"
CONF_EXCLUDE_STYLES = "exclude_styles"
CONF_KEEP_RELEASES = "keep_releases"

DEFAULT_EXCLUDE_GENRES = "Classical"
DEFAULT_EXCLUDE_STYLES = "Spoken Word"
DEFAULT_KEEP_RELEASES = "Symphonique, Legacy Of The Dark Lands"

SKIP_CALENDAR = "__skip_calendar__"
SKIP_SYNC = "__skip_sync__"

CALENDAR_DOMAIN = "calendar"
SERVICE_GET_EVENTS = "get_events"
MAX_CALENDAR_LOOKAHEAD_DAYS = 730

SPOTIFY_DOMAIN = "spotifyplus"
SERVICE_GET_PLAYLIST_FAVORITES = "get_playlist_favorites"
SERVICE_GET_PLAYLIST_ITEMS = "get_playlist_items"
SERVICE_SYNC_COLLECTION = "sync_collection"
SERVICE_REALIGN_PLAYLIST = "realign_playlist"

SKIP_SPOTIFY = "__skip__"
SPOTIFY_MAX_TRACKS = 1000
SPOTIFY_SCAN_LIMIT = 9999  # max spotifyplus accepts for get_playlist_items
SPOTIFY_SEARCH_LIMIT = 5
SPOTIFY_SEARCH_ATTEMPTS = 3
SPOTIFY_SEARCH_RETRY_DELAY_SECONDS = 2
SPOTIFY_ADD_BATCH = 100
SPOTIFY_SERVICE_TIMEOUT_SECONDS = 60

API_BASE_URL = "https://api.discogs.com"

COLLECTION_FOLDER_ID = 0
COLLECTION_PER_PAGE = 100

SCAN_INTERVAL = timedelta(hours=6)

HTTP_TIMEOUT_SECONDS = 30
PAGE_DELAY_SECONDS = 0.25