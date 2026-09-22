"""Switch platform for the Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DiscogsSpotifyCalendarFilterCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Discogs/Spotify Calendar Filter switches from a config entry."""
    coordinator: DiscogsSpotifyCalendarFilterCoordinator = hass.data[DOMAIN][entry.entry_id]
    switch = DiscogsSpotifyCalendarFilterSwitch(coordinator, entry)
    coordinator.collection_only = switch
    async_add_entities([switch])


class DiscogsSpotifyCalendarFilterSwitch(
    CoordinatorEntity[DiscogsSpotifyCalendarFilterCoordinator], SwitchEntity, RestoreEntity
):
    """Toggles whether the events sensor only shows collection bands."""

    _attr_has_entity_name = True
    _attr_translation_key = "collection_only"

    def __init__(self, coordinator: DiscogsSpotifyCalendarFilterCoordinator, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_collection_only"
        self._attr_is_on = False

    @property
    def icon(self) -> str:
        """Icon reflecting the filter state."""
        return "mdi:filter-check" if self._attr_is_on else "mdi:filter-check-outline"

    async def async_added_to_hass(self) -> None:
        """Restore the previous state when available."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the filter on."""
        self._attr_is_on = True
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the filter off."""
        self._attr_is_on = False
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()