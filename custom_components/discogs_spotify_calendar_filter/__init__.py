"""The Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_REALIGN_PLAYLIST,
    SERVICE_SYNC_COLLECTION,
)
from .coordinator import DiscogsSpotifyCalendarFilterCoordinator

PLATFORMS = ["sensor", "switch", "calendar", "button"]

_OPTIONAL_PLAYLIST_SCHEMA = vol.Schema(
    {vol.Optional("playlist_id"): vol.Any(cv.string, None)}
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Discogs/Spotify Calendar Filter from a config entry."""
    coordinator = DiscogsSpotifyCalendarFilterCoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def handle_sync_collection(call: ServiceCall) -> None:
        """Start a collection-to-playlist sync in the background."""
        hass.async_create_task(
            coordinator.async_sync_collection(call.data.get("playlist_id"))
        )

    async def handle_realign_playlist(call: ServiceCall) -> None:
        """Start a playlist re-align in the background."""
        hass.async_create_task(
            coordinator.async_realign_playlist(call.data.get("playlist_id"))
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_COLLECTION,
        handle_sync_collection,
        schema=_OPTIONAL_PLAYLIST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REALIGN_PLAYLIST,
        handle_realign_playlist,
        schema=_OPTIONAL_PLAYLIST_SCHEMA,
    )
    entry.async_on_unload(
        lambda: hass.services.async_remove(DOMAIN, SERVICE_SYNC_COLLECTION)
    )
    entry.async_on_unload(
        lambda: hass.services.async_remove(DOMAIN, SERVICE_REALIGN_PLAYLIST)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _startup_refresh() -> None:
        """Run the first refresh once Home Assistant has fully started."""
        await coordinator.async_refresh()

    if hass.state == CoreState.running:
        entry.async_create_background_task(
            hass, _startup_refresh(), "discogs_spotify_calendar_filter_startup"
        )
    else:
        listener_fired = False

        async def _on_start(event: Event) -> None:
            nonlocal listener_fired
            listener_fired = True
            await _startup_refresh()

        unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, _on_start
        )

        @callback
        def _remove_listener() -> None:
            if not listener_fired:
                unsub()

        entry.async_on_unload(_remove_listener)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok