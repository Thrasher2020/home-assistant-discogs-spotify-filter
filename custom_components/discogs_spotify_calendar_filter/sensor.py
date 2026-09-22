"""Sensors for the Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
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
    """Set up Discogs/Spotify Calendar Filter sensors from a config entry."""
    coordinator: DiscogsSpotifyCalendarFilterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DiscogsSpotifyCalendarFilterSensor(coordinator, entry),
            DiscogsSpotifyCalendarFilterEventsSensor(coordinator, entry),
            DiscogsSpotifyCalendarFilterSyncStatusSensor(coordinator, entry),
        ]
    )


class DiscogsSpotifyCalendarFilterSensor(CoordinatorEntity[DiscogsSpotifyCalendarFilterCoordinator], SensorEntity):
    """A sensor listing the upcoming gigs whose bands are in the Discogs collection."""

    _attr_has_entity_name = True
    _attr_translation_key = "highlighted_gigs"
    _attr_icon = "mdi:account-music"
    _attr_native_unit_of_measurement = "gigs"

    def __init__(self, coordinator: DiscogsSpotifyCalendarFilterCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_highlighted_gigs"

    @property
    def native_value(self) -> int:
        """Return the number of highlighted gigs."""
        return len((self.coordinator.data or {}).get("matches", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the collection artist count and the matching gigs."""
        data = self.coordinator.data or {}
        return {
            "artist_count": len(data.get("artists", [])),
            "spotify_artist_count": len(data.get("spotify_artists", [])),
            "matches": data.get("matches", []),
        }


class DiscogsSpotifyCalendarFilterEventsSensor(CoordinatorEntity[DiscogsSpotifyCalendarFilterCoordinator], SensorEntity):
    """All upcoming Gig Finder events, optionally filtered to collection bands."""

    _attr_has_entity_name = True
    _attr_translation_key = "upcoming_events"
    _attr_icon = "mdi:calendar-month"
    _attr_native_unit_of_measurement = "events"

    def __init__(self, coordinator: DiscogsSpotifyCalendarFilterCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_events"

    @property
    def _collection_only(self) -> bool:
        """Return True when the Collection Only switch is on."""
        switch = getattr(self.coordinator, "collection_only", None)
        return bool(switch and switch.is_on)

    def _visible_events(self) -> list[dict[str, Any]]:
        """Return all events, or only collection matches when filtered."""
        events = (self.coordinator.data or {}).get("events", [])
        if self._collection_only:
            return [event for event in events if event.get("in_collection")]
        return events

    @property
    def native_value(self) -> int:
        """Return the number of visible events."""
        return len(self._visible_events())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the visible events and whether the filter is active."""
        return {"events": self._visible_events(), "filtered": self._collection_only}


class DiscogsSpotifyCalendarFilterSyncStatusSensor(CoordinatorEntity[DiscogsSpotifyCalendarFilterCoordinator], SensorEntity):
    """Describes the last collection-to-playlist sync or re-align."""

    _attr_has_entity_name = True
    _attr_translation_key = "collection_sync_status"
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator: DiscogsSpotifyCalendarFilterCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_collection_sync_status"

    def _summary(self, result: dict[str, Any]) -> str:
        """Render a short human summary of a sync result."""
        if result.get("skipped"):
            return f"skipped ({result.get('reason')})"
        if result.get("op") == "realign":
            summary = (
                f"{result['added']} added, {result['removed']} removed, "
                f"{result['deduped']} deduped, "
                f"{result['already']} intact, {result['errored']} errors"
            )
            if result.get("removals_skipped"):
                summary += " (removals skipped)"
            return summary
        return (
            f"{result['added']} added, {result['already']} already present, "
            f"{result['errored']} errors"
        )

    @property
    def native_value(self) -> str:
        """Return 'running' while a sync is active, else a summary of the last run."""
        if self.coordinator.sync_running:
            return "running"
        result = self.coordinator.last_sync_result
        if not result:
            return "never"
        return self._summary(result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full last-run result."""
        result = self.coordinator.last_sync_result or {}
        attrs: dict[str, Any] = {}
        for key in (
            "op",
            "playlist_id",
            "added",
            "already",
            "removed",
            "errored",
            "total",
            "deduped",
            "removals_skipped",
        ):
            if key in result:
                attrs[key] = result[key]
        if result.get("removed_tracks") is not None:
            attrs["removed_tracks"] = result["removed_tracks"]
        if result.get("last_run") is not None:
            attrs["last_run"] = result["last_run"].isoformat()
        if result.get("errors"):
            attrs["errors"] = result["errors"][-10:]
        return attrs