"""Buttons for the Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DiscogsSpotifyCalendarFilterCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Discogs/Spotify Calendar Filter buttons from a config entry."""
    coordinator: DiscogsSpotifyCalendarFilterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DiscogsSpotifyCalendarFilterSyncButton(
                coordinator, entry, "sync_collection", "mdi:playlist-plus"
            ),
            DiscogsSpotifyCalendarFilterSyncButton(
                coordinator, entry, "realign_playlist", "mdi:playlist-check"
            ),
        ]
    )


class DiscogsSpotifyCalendarFilterSyncButton(CoordinatorEntity[DiscogsSpotifyCalendarFilterCoordinator], ButtonEntity):
    """A button that runs a collection sync or a playlist re-align."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DiscogsSpotifyCalendarFilterCoordinator,
        entry: ConfigEntry,
        kind: str,
        icon: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._kind = kind
        self._attr_unique_id = f"{entry.entry_id}_{kind}"
        self._attr_translation_key = kind
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Run the associated sync job in the background."""
        if self._kind == "sync_collection":
            self.hass.async_create_task(self.coordinator.async_sync_collection())
        else:
            self.hass.async_create_task(self.coordinator.async_realign_playlist())