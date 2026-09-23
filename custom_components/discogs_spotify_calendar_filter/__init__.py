"""The Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

import asyncio

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_REALIGN_PLAYLIST,
    SERVICE_SYNC_COLLECTION,
    STARTUP_REFRESH_DELAY,
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

    async def _delayed_startup_refresh() -> None:
        """Run the first refresh after a delay so startup is never blocked."""
        await asyncio.sleep(STARTUP_REFRESH_DELAY.total_seconds())
        await coordinator.async_refresh()

    startup_task = hass.async_create_task(_delayed_startup_refresh())
    entry.async_on_unload(startup_task.cancel)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok