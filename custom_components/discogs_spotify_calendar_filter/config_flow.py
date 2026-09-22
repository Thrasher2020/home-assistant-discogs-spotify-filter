"""Config flow for the Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import SERVER_SOFTWARE, async_get_clientsession

from .const import (
    API_BASE_URL,
    CALENDAR_DOMAIN,
    CONF_CALENDAR_ENTITY_ID,
    CONF_DISCOGS_TOKEN,
    CONF_DISCOGS_USERNAME,
    CONF_EXCLUDE_GENRES,
    CONF_EXCLUDE_STYLES,
    CONF_KEEP_RELEASES,
    CONF_SPOTIFY_PLAYLIST_ID,
    CONF_SPOTIFY_PLAYLIST_NAME,
    CONF_SPOTIFY_PLAYER_ENTITY_ID,
    CONF_SPOTIFY_SYNC_PLAYLIST_ID,
    CONF_SPOTIFY_SYNC_PLAYLIST_NAME,
    DEFAULT_EXCLUDE_GENRES,
    DEFAULT_EXCLUDE_STYLES,
    DEFAULT_KEEP_RELEASES,
    DOMAIN,
    NAME,
    SERVICE_GET_PLAYLIST_FAVORITES,
    SKIP_CALENDAR,
    SKIP_SYNC,
    SPOTIFY_DOMAIN,
)

_TIMEOUT = aiohttp.ClientTimeout(connect=5, total=15)


async def _async_get_identity(hass, token: str) -> str | None:
    """Return the Discogs username for a personal access token, or None on failure."""
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Discogs token={token}", "User-Agent": SERVER_SOFTWARE}
    async with session.get(
        f"{API_BASE_URL}/oauth/identity", headers=headers, timeout=_TIMEOUT
    ) as response:
        response.raise_for_status()
        data = await response.json(content_type=None)
        return data.get("username")


def _spotifyplus_available(hass) -> bool:
    """Return True when SpotifyPlus is running with a usable player."""
    if not hass.data.get(SPOTIFY_DOMAIN):
        return False
    return hass.services.has_service(SPOTIFY_DOMAIN, SERVICE_GET_PLAYLIST_FAVORITES)


def _spotify_player_entity_id(hass) -> str | None:
    """Return the first SpotifyPlus media player entity id, if any."""
    for instance in hass.data.get(SPOTIFY_DOMAIN, {}).values():
        player = getattr(instance, "media_player", None)
        if player is not None and player.entity_id:
            return player.entity_id
    return None


async def _async_get_playlists(hass, player_entity_id: str) -> dict[str, str]:
    """Return the user's Spotify playlists as {id: name}."""
    response = await hass.services.async_call(
        SPOTIFY_DOMAIN,
        SERVICE_GET_PLAYLIST_FAVORITES,
        {"limit_total": 200},
        target={"entity_id": player_entity_id},
        blocking=True,
        return_response=True,
    )
    result = (response or {}).get("result") or {}
    items = result.get("items")
    if not isinstance(items, list):
        items = (result.get("playlists") or {}).get("items")
    playlists: dict[str, str] = {}
    for item in items or []:
        name = item.get("name")
        playlist_id = item.get("id")
        if name and playlist_id:
            playlists[playlist_id] = name
    return dict(sorted(playlists.items(), key=lambda pair: pair[1].lower()))


def _normalize_selection(value: Any, playlists: dict[str, str]) -> dict[str, str]:
    """Return the selected playlists as {id: name}, whatever shape the UI sent."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, set, tuple, str)):
        values = value if isinstance(value, str) else list(value)
        if isinstance(values, str):
            values = [values] if values else []
        return {item: playlists.get(item, item) for item in values}
    return {}


def _calendar_options(hass) -> dict[str, str]:
    """Return selectable secondary calendars as {entity_id: name}."""
    options = {SKIP_CALENDAR: "None - Discogs only (no calendar cross-reference)"}
    for state in hass.states.async_all(CALENDAR_DOMAIN):
        entity_id = state.entity_id
        if entity_id.startswith(f"{CALENDAR_DOMAIN}.{DOMAIN}"):
            continue
        options[entity_id] = state.attributes.get("friendly_name") or entity_id
    return options


class DiscogsSpotifyCalendarFilterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Discogs/Spotify Calendar Filter."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> DiscogsSpotifyCalendarFilterOptionsFlow:
        """Return the options flow for this handler."""
        return DiscogsSpotifyCalendarFilterOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the flow."""
        self._token: str | None = None
        self._username: str | None = None
        self._player_entity_id: str | None = None
        self._playlists: dict[str, str] = {}
        self._selected_playlists: dict[str, str] = {}
        self._calendar_entity_id: str | None = None

    async def _async_create_entry(self) -> ConfigFlowResult:
        """Create the config entry with the selected playlists and calendar."""
        data: dict[str, Any] = {
            CONF_DISCOGS_TOKEN: self._token,
            CONF_DISCOGS_USERNAME: self._username,
        }
        if self._selected_playlists:
            data.update(
                {
                    CONF_SPOTIFY_PLAYLIST_ID: list(self._selected_playlists),
                    CONF_SPOTIFY_PLAYLIST_NAME: dict(self._selected_playlists),
                    CONF_SPOTIFY_PLAYER_ENTITY_ID: self._player_entity_id,
                }
            )
        if self._calendar_entity_id:
            data[CONF_CALENDAR_ENTITY_ID] = self._calendar_entity_id
        return self.async_create_entry(title=NAME, data=data)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the Discogs token step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_DISCOGS_TOKEN]
            try:
                username = await _async_get_identity(self.hass, token)
            except aiohttp.ClientResponseError as err:
                if err.status == 401:
                    errors["base"] = "invalid_token"
                else:
                    errors["base"] = "cannot_connect"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            else:
                if username is None:
                    errors["base"] = "invalid_token"
                else:
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()
                    self._token = token
                    self._username = username
                    if _spotifyplus_available(self.hass):
                        self._player_entity_id = _spotify_player_entity_id(self.hass)
                        if self._player_entity_id:
                            try:
                                self._playlists = await _async_get_playlists(
                                    self.hass, self._player_entity_id
                                )
                            except HomeAssistantError:
                                self._playlists = {}
                            if self._playlists:
                                return await self.async_step_spotify()
                    return await self.async_step_calendar()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DISCOGS_TOKEN): str}),
            errors=errors,
        )

    async def async_step_spotify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the optional multi-playlist selection step."""
        if user_input is not None:
            self._selected_playlists = _normalize_selection(
                user_input.get(CONF_SPOTIFY_PLAYLIST_ID), self._playlists
            )
            return await self.async_step_calendar()

        return self.async_show_form(
            step_id="spotify",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SPOTIFY_PLAYLIST_ID, default=[]
                    ): cv.multi_select(self._playlists)
                }
            ),
        )

    async def async_step_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the optional secondary calendar selection step."""
        if user_input is not None:
            chosen = user_input[CONF_CALENDAR_ENTITY_ID]
            if chosen != SKIP_CALENDAR:
                self._calendar_entity_id = chosen
            return await self._async_create_entry()

        return self.async_show_form(
            step_id="calendar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CALENDAR_ENTITY_ID, default=SKIP_CALENDAR): vol.In(
                        _calendar_options(self.hass)
                    )
                }
            ),
        )


class DiscogsSpotifyCalendarFilterOptionsFlow(OptionsFlow):
    """Handle an options flow for Discogs/Spotify Calendar Filter."""

    VERSION = 1

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = entry
        self._player_entity_id: str | None = None
        self._playlists: dict[str, str] = {}

    def _current_playlists(self) -> list[str]:
        """Return the playlists currently configured on the entry."""
        value = self._entry.data.get(CONF_SPOTIFY_PLAYLIST_ID)
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [item for item in value if item]
        return []

    async def _load_playlists(self) -> None:
        """Load fresh playlists when possible, else fall back to stored names."""
        self._player_entity_id = self._entry.data.get(CONF_SPOTIFY_PLAYER_ENTITY_ID)
        if self._player_entity_id and _spotifyplus_available(self.hass):
            try:
                self._playlists = await _async_get_playlists(
                    self.hass, self._player_entity_id
                )
                return
            except HomeAssistantError:
                pass
        stored = self._entry.data.get(CONF_SPOTIFY_PLAYLIST_NAME)
        if isinstance(stored, dict):
            self._playlists = dict(stored)
        elif isinstance(stored, str):
            current = self._current_playlists()
            self._playlists = {playlist_id: stored for playlist_id in current}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the Spotify playlists and the secondary calendar."""
        if user_input is None:
            await self._load_playlists()
            current_calendar = self._entry.data.get(CONF_CALENDAR_ENTITY_ID)
            current_playlists = [
                playlist_id
                for playlist_id in self._current_playlists()
                if playlist_id in self._playlists
            ]
            sync_options = {SKIP_SYNC: "None - do not sync the collection to a playlist"}
            sync_options.update(self._playlists or {})
            current_sync = self._entry.data.get(CONF_SPOTIFY_SYNC_PLAYLIST_ID)
            current_genres = self._entry.data.get(CONF_EXCLUDE_GENRES)
            current_styles = self._entry.data.get(CONF_EXCLUDE_STYLES)
            current_keep = self._entry.data.get(CONF_KEEP_RELEASES)
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(
                    {
                        vol.Optional(
                            CONF_SPOTIFY_PLAYLIST_ID, default=current_playlists
                        ): cv.multi_select(self._playlists or {}),
                        vol.Required(
                            CONF_CALENDAR_ENTITY_ID, default=current_calendar or SKIP_CALENDAR
                        ): vol.In(_calendar_options(self.hass)),
                        vol.Required(
                            CONF_SPOTIFY_SYNC_PLAYLIST_ID, default=current_sync or SKIP_SYNC
                        ): vol.In(sync_options),
                        vol.Optional(
                            CONF_EXCLUDE_GENRES,
                            default=(
                                current_genres
                                if current_genres is not None
                                else DEFAULT_EXCLUDE_GENRES
                            ),
                        ): cv.string,
                        vol.Optional(
                            CONF_EXCLUDE_STYLES,
                            default=(
                                current_styles
                                if current_styles is not None
                                else DEFAULT_EXCLUDE_STYLES
                            ),
                        ): cv.string,
                        vol.Optional(
                            CONF_KEEP_RELEASES,
                            default=(
                                current_keep
                                if current_keep is not None
                                else DEFAULT_KEEP_RELEASES
                            ),
                        ): cv.string,
                    }
                ),
            )

        data: dict[str, Any] = dict(self._entry.data)
        selected = _normalize_selection(
            user_input.get(CONF_SPOTIFY_PLAYLIST_ID), self._playlists
        )
        if selected:
            data[CONF_SPOTIFY_PLAYLIST_ID] = list(selected)
            data[CONF_SPOTIFY_PLAYLIST_NAME] = dict(selected)
            if not data.get(CONF_SPOTIFY_PLAYER_ENTITY_ID):
                data[CONF_SPOTIFY_PLAYER_ENTITY_ID] = self._player_entity_id
        else:
            data.pop(CONF_SPOTIFY_PLAYLIST_ID, None)
            data.pop(CONF_SPOTIFY_PLAYLIST_NAME, None)
            data.pop(CONF_SPOTIFY_PLAYER_ENTITY_ID, None)

        chosen_sync = user_input[CONF_SPOTIFY_SYNC_PLAYLIST_ID]
        if chosen_sync == SKIP_SYNC:
            data.pop(CONF_SPOTIFY_SYNC_PLAYLIST_ID, None)
            data.pop(CONF_SPOTIFY_SYNC_PLAYLIST_NAME, None)
        else:
            data[CONF_SPOTIFY_SYNC_PLAYLIST_ID] = chosen_sync
            data[CONF_SPOTIFY_SYNC_PLAYLIST_NAME] = self._playlists.get(
                chosen_sync, chosen_sync
            )

        chosen = user_input[CONF_CALENDAR_ENTITY_ID]
        if chosen == SKIP_CALENDAR:
            data.pop(CONF_CALENDAR_ENTITY_ID, None)
        else:
            data[CONF_CALENDAR_ENTITY_ID] = chosen

        data[CONF_EXCLUDE_GENRES] = str(
            user_input.get(CONF_EXCLUDE_GENRES) or ""
        ).strip()
        data[CONF_EXCLUDE_STYLES] = str(
            user_input.get(CONF_EXCLUDE_STYLES) or ""
        ).strip()
        data[CONF_KEEP_RELEASES] = str(
            user_input.get(CONF_KEEP_RELEASES) or ""
        ).strip()

        self.hass.config_entries.async_update_entry(self._entry, data=data)
        return self.async_create_entry(title="", data=None)