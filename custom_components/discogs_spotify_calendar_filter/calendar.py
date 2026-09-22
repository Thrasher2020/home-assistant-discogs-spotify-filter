"""Calendar platform for the Discogs/Spotify Calendar Filter integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import DiscogsSpotifyCalendarFilterCoordinator

SOURCE_TAGS = {
    "ticketmaster": "TM",
    "fatsoma": "FS",
    "skiddle": "SK",
}


class DiscogsSpotifyCalendarFilterCalendar(
    CoordinatorEntity[DiscogsSpotifyCalendarFilterCoordinator], CalendarEntity
):
    """A curated calendar of upcoming gigs whose bands are in the Discogs collection."""

    _attr_icon = "mdi:calendar-star"
    _attr_name = "Discogs/Spotify Calendar Filter"

    def __init__(
        self,
        coordinator: DiscogsSpotifyCalendarFilterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_calendar"

    def _matching_events(self) -> list[dict[str, Any]]:
        """Return the raw gig events that matched the collection."""
        return list((self.coordinator.data or {}).get("calendar_events", []))

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next matching event, if any."""
        now = dt_util.now()
        for event in self._matching_events():
            if event["start"] >= now:
                return self._to_calendar_event(event)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return matching events within the given time range."""
        return [
            self._to_calendar_event(event)
            for event in self._matching_events()
            if start_date <= event["start"] <= end_date
        ]

    def _to_calendar_event(self, event: dict[str, Any]) -> CalendarEvent:
        """Convert a raw gig event into a CalendarEvent."""
        start: datetime = event["start"]
        end = start + timedelta(hours=3)
        venue = event.get("venue") or {}
        name = event.get("name") or "Unknown event"

        location = None
        if venue:
            city = (venue.get("city") or {}).get("name")
            location = " / ".join(x for x in [venue.get("name"), city] if x)

        source = event.get("source")
        tag = SOURCE_TAGS.get(source or "")
        summary = f"[{tag}] {name}" if tag else name

        parts = list(event.get("lineup") or [])
        if event.get("url"):
            parts.append(event["url"])

        event_id = event.get("id")
        uid = (
            str(event_id)
            if event_id
            else f"{start.isoformat()}|{name}|{venue.get('name')}"
        )
        return CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            location=location,
            description=" / ".join(parts),
            uid=uid,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Discogs/Spotify Calendar Filter calendar from a config entry."""
    coordinator: DiscogsSpotifyCalendarFilterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DiscogsSpotifyCalendarFilterCalendar(coordinator, entry)])