"""Sensor platform for Allarise Alarm integration."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

_MINUTES_UNTIL_REFRESH = timedelta(minutes=1)

from .const import (
    DASHBOARD_SENSORS,
    DIAGNOSTIC_PER_ALARM_SENSORS,
    DOMAIN,
    PER_ALARM_SENSORS,
)
from .coordinator import AllariseCoordinator
from .normalize import MAX_STATE_LENGTH, truncate_state

_FIRE_TIME_DASHBOARD_KEYS = frozenset({
    "active_alarm_fire_time",
    "active_alarm_snooze_fire_time",
    "quick_alarm_fire_time",
})
_FIRE_TIME_PER_ALARM_KEYS = frozenset({
    "fire_time",
    "snooze_fire_time",
})

# The slept-through count is a running measurement (resets at local midnight),
# so it carries state_class measurement — that is what makes it graphable in
# Home Assistant's history and usable in a statistics card.
_MEASUREMENT_DASHBOARD_KEYS = frozenset({
    "slept_through_today",
})

# The count sensor's state comes from this topic; its detail list, from the
# companion topic. The list is NOT its own entity — it rides along as the
# count's "alarms" attribute (see extra_state_attributes). A list of rows
# crosses Home Assistant's 255-character state cap the same way the radio
# favourites do, so a second state sensor would silently truncate.
_SLEPT_THROUGH_COUNT_KEY = "slept_through_today"
_SLEPT_THROUGH_DETAILS_KEY = "slept_through_today_details"


# Payloads the app sends when there is no time to report. A timestamp sensor
# must be None (→ "unknown") in that case, not the literal string.
_NO_VALUE = frozenset({"none", "unknown", "unavailable", ""})


# States that carry no information. Restoring one of these would mark the key
# as "seen" and park the sensor on the coordinator's literal default ("Unknown"),
# which is exactly what the availability split exists to prevent.
_UNRESTORABLE_STATES = frozenset({"", "unknown", "unavailable"})


def _restorable_state(state: Any) -> str | None:
    """Return a restored HA state worth seeding the coordinator with, or None."""
    if state is None:
        return None
    value = state.state
    if not isinstance(value, str) or value.strip().lower() in _UNRESTORABLE_STATES:
        return None
    return value


def _length_attributes(raw: str | None) -> dict[str, str] | None:
    """Expose the untruncated text when a state had to be shortened.

    Home Assistant will not accept a state longer than 255 characters — it
    raises, logs a traceback and parks the entity on "unknown". Two of our
    values pass that routinely: an after-alarm note built up over several
    `append_notes` calls, and the JSON list of favourited radio stations, which
    crosses 255 characters at roughly six favourites. The state is truncated so
    the entity keeps working; the whole thing lives here so a template that
    needs the real text can still reach it via
    `state_attr('sensor.…', 'full_value')`.
    """
    if not isinstance(raw, str) or len(raw) <= MAX_STATE_LENGTH:
        return None
    return {"full_value": raw}


def _slept_through_count(raw: str | None) -> int | None:
    """Parse the slept-through count payload into an int, or None if unusable.

    The app publishes a plain integer as a string ("0", "3"). A measurement
    sensor wants a number, and returning the raw string would make Home
    Assistant reject a stray non-numeric value with a traceback. Anything that
    is not a clean integer degrades to None ("unknown") rather than raising —
    an older app that never published this topic is the case that matters, and
    the sensor's availability keeps it off the screen entirely until a real
    value arrives.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _slept_through_alarms(raw: str | None) -> list[dict[str, Any]] | None:
    """Parse the slept-through details payload into a list of rows, or None.

    The app publishes a JSON array like
    ``[{"name": "Work", "index": 3, "at": "2026-08-05T06:00:00Z"}]`` (capped at
    ten rows on the app side). Fail soft on absence and on damage: an older app
    publishes nothing here, and a malformed or wrong-shaped payload must yield
    no attribute rather than an exception. Only a JSON list of objects is
    accepted; anything else is treated as "no detail available".
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return [row for row in parsed if isinstance(row, dict)]


def _as_timestamp(raw: str | None) -> "datetime | None":
    """Parse a fire-time payload into an aware datetime, or None when absent.

    The app publishes these topics as ISO 8601 (with offset) and sends "none"
    when there is nothing scheduled.
    """
    if raw is None or str(raw).strip().lower() in _NO_VALUE:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    if parsed is None:
        return None
    # A timestamp sensor's value must be timezone-aware. The app always sends an
    # offset, but a naive value would otherwise raise inside HA.
    if parsed.tzinfo is None:
        parsed = dt_util.as_utc(parsed)
    return parsed


def _minutes_until(iso_string: str) -> int | None:
    """Return whole minutes from now until an ISO timestamp, or None if unparseable."""
    parsed = _as_timestamp(iso_string)
    if parsed is None:
        return None
    return round((parsed - dt_util.utcnow()).total_seconds() / 60)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[AllariseCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Allarise sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    # All dashboard sensors (including quick alarm) go on the dashboard device
    for key, name_suffix, icon, _ in DASHBOARD_SENSORS:
        entities.append(
            AllariseDashboardSensor(coordinator, key, name_suffix, icon)
        )

    async_add_entities(entities)

    # Register factory for dynamic per-alarm sensor creation
    def _sensor_factory(coord: AllariseCoordinator, alarm_index: int) -> list:
        return [
            AllarisePerAlarmSensor(coord, alarm_index, key, name_suffix, icon)
            for key, name_suffix, icon, _ in PER_ALARM_SENSORS
        ]

    coordinator.register_alarm_entity_factory(_sensor_factory, async_add_entities)

    # Register factory for dynamic command sensor creation
    def _command_sensor_factory(coord: AllariseCoordinator, command_name: str) -> list:
        return [AllariseCommandSensor(coord, command_name)]

    coordinator.register_command_entity_factory(_command_sensor_factory, async_add_entities)


class AllariseDashboardSensor(
    CoordinatorEntity[AllariseCoordinator], RestoreEntity, SensorEntity
):
    """A dashboard sensor for Allarise."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AllariseCoordinator,
        key: str,
        name_suffix: str,
        icon: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name_suffix
        self._attr_icon = icon
        self._attr_unique_id = f"allarise_{coordinator.device_name}_{key}"
        # Fire times are exposed as timestamp-class sensors so Home Assistant's
        # Time trigger can target them directly:
        #
        #   triggers:
        #     - trigger: time
        #       at:
        #         entity_id: sensor.<...>_fire_time
        #         offset: "-00:05:00"
        #
        # That replaces the old every-minute time_pattern + template-condition
        # pattern with a single scheduled callback, and it re-arms itself when
        # the alarm moves. Without a device class the Time trigger will not
        # accept the entity, which is why users were having to wrap it in a
        # template-sensor helper by hand.
        if key in _FIRE_TIME_DASHBOARD_KEYS:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        # A running daily count — measurement so Home Assistant graphs it and a
        # statistics card can total it. No device_class: there is no unit that
        # fits, and "alarms" is left off deliberately, matching the other count
        # sensors on this device (snooze_count, enabled_alarm_count), none of
        # which carry a unit.
        if key in _MEASUREMENT_DASHBOARD_KEYS:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"allarise_{self.coordinator.device_name}_dashboard")},
            name=f"Allarise {self.coordinator.device_name} - Dashboard",
            manufacturer="Allarise",
            model="iOS Alarm Clock",
        )

    @property
    def available(self) -> bool:
        """Return True once this sensor has ever had a value.

        Deliberately NOT gated on app_online. A state sensor describes what the
        alarm clock is configured to do, and that does not stop being true
        because the phone suspended itself — under the Dynamic persistence mode
        the app is offline most of the day, and gating on liveness turned the
        whole integration unavailable between alarms. Liveness has its own
        entity, binary_sensor.<device>_app_online, which is what an automation
        should trigger on; action entities stay gated.

        A sensor that has never received a message stays unavailable, so a fresh
        install never shows the coordinator's literal "Unknown" default.
        """
        return self.coordinator.has_dashboard_data(self._key)

    @property
    def native_value(self) -> str | int | datetime | None:
        """Return the sensor value."""
        raw = self.coordinator.get_dashboard_state(self._key)
        if self._key in _FIRE_TIME_DASHBOARD_KEYS:
            return _as_timestamp(raw)
        # The slept-through count is a measurement sensor, so its state must be
        # a number — a non-numeric payload degrades to "unknown" rather than
        # raising inside Home Assistant's recorder.
        if self._key == _SLEPT_THROUGH_COUNT_KEY:
            return _slept_through_count(raw)
        return truncate_state(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose per-sensor attributes.

        Fire-time sensors get minutes_until; the slept-through count carries an
        "alarms" list drawn from its companion details topic; long text values
        keep their untruncated form in full_value.
        """
        raw = self.coordinator.get_dashboard_state(self._key)
        if self._key == _SLEPT_THROUGH_COUNT_KEY:
            # Fail soft: an older app never publishes the details topic, and a
            # malformed payload must yield no attribute rather than an error.
            # The details key has no registered default, so get_dashboard_state
            # returns "Unknown" until the topic is seen — which parses to None
            # and simply omits the attribute.
            details_raw = self.coordinator.get_dashboard_state(
                _SLEPT_THROUGH_DETAILS_KEY
            )
            alarms = _slept_through_alarms(details_raw)
            if alarms is None:
                return None
            return {"alarms": alarms}
        if self._key not in _FIRE_TIME_DASHBOARD_KEYS:
            return _length_attributes(raw)
        minutes = _minutes_until(raw)
        if minutes is None:
            return None
        return {"minutes_until": minutes}

    async def async_added_to_hass(self) -> None:
        """Restore the last value, then tick fire-time sensors every minute."""
        await super().async_added_to_hass()
        # Retained MQTT is the primary source and arrives during coordinator
        # setup, before the platforms are forwarded — the coordinator ignores
        # this seed when it already holds a value for the key, so a retained
        # message always wins whichever order the two land in.
        restored = _restorable_state(await self.async_get_last_state())
        if restored is not None:
            self.coordinator.restore_dashboard_state(self._key, restored)
        if self._key in _FIRE_TIME_DASHBOARD_KEYS:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._tick_minutes_until, _MINUTES_UNTIL_REFRESH
                )
            )

    @callback
    def _tick_minutes_until(self, _now: datetime) -> None:
        """Re-publish state so the minutes_until attribute counts down.

        Must be a @callback: an undecorated function is treated as blocking
        and run in an executor thread, and async_write_ha_state from off the
        event loop raises every minute on modern Home Assistant.
        """
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class AllarisePerAlarmSensor(
    CoordinatorEntity[AllariseCoordinator], RestoreEntity, SensorEntity
):
    """A per-alarm sensor — each alarm index gets its own HA device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AllariseCoordinator,
        alarm_index: int,
        key: str,
        name_suffix: str,
        icon: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._alarm_index = alarm_index
        self._key = key
        self._attr_name = name_suffix
        self._attr_icon = icon
        self._attr_unique_id = (
            f"allarise_{coordinator.device_name}_alarm_{alarm_index}_{key}"
        )
        # See the dashboard sensor above — timestamp class is what lets the Time
        # trigger target this entity with an offset.
        if key in _FIRE_TIME_PER_ALARM_KEYS:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        # Alarm ID describes the wiring, not the alarm — Home Assistant files it
        # under Diagnostic so it is findable without crowding the main list.
        if key in DIAGNOSTIC_PER_ALARM_SENSORS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — each alarm index is its own device."""
        alarm_name = self.coordinator.get_per_alarm_state(self._alarm_index, "name")
        if alarm_name in ("Unknown", ""):
            display_name = f"Allarise {self.coordinator.device_name} - Alarm {self._alarm_index}"
        else:
            display_name = f"Allarise {self.coordinator.device_name} - {alarm_name}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"allarise_{self.coordinator.device_name}_alarm_{self._alarm_index}")},
            name=display_name,
            manufacturer="Allarise",
            model="iOS Alarm Clock",
        )

    @property
    def available(self) -> bool:
        """Return True if the alarm still exists and this key has had a value.

        The alarm-side twin of the dashboard sensor's rule: known rather than
        live, so an alarm's name, fire time and days keep reading true while the
        phone is suspended. A deleted alarm is never known, and a key that has
        never arrived stays unavailable rather than reporting a default.
        """
        return (
            self.coordinator.is_alarm_known(self._alarm_index)
            and self.coordinator.has_per_alarm_data(self._alarm_index, self._key)
        )

    @property
    def native_value(self) -> str | datetime | None:
        """Return the sensor value."""
        raw = self.coordinator.get_per_alarm_state(self._alarm_index, self._key)
        if self._key in _FIRE_TIME_PER_ALARM_KEYS:
            return _as_timestamp(raw)
        # `notes` is the one that actually hits the 255-character wall; before
        # this, an alarm whose Notes page had a paragraph on it reported
        # "unknown" and logged a traceback on every republish.
        return truncate_state(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose minutes_until for fire-time sensors, full_value for long ones."""
        raw = self.coordinator.get_per_alarm_state(self._alarm_index, self._key)
        if self._key not in _FIRE_TIME_PER_ALARM_KEYS:
            return _length_attributes(raw)
        minutes = _minutes_until(raw)
        if minutes is None:
            return None
        return {"minutes_until": minutes}

    async def async_added_to_hass(self) -> None:
        """Restore the last value, then tick fire-time sensors every minute."""
        await super().async_added_to_hass()
        # See the dashboard sensor's copy: retained MQTT wins, this only fills a
        # gap. "alarm_id" is answered from the topic, so it is never restored.
        if self._key != "alarm_id":
            restored = _restorable_state(await self.async_get_last_state())
            if restored is not None:
                self.coordinator.restore_per_alarm_state(
                    self._alarm_index, self._key, restored
                )
        if self._key in _FIRE_TIME_PER_ALARM_KEYS:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._tick_minutes_until, _MINUTES_UNTIL_REFRESH
                )
            )

    @callback
    def _tick_minutes_until(self, _now: datetime) -> None:
        """Re-publish state so the minutes_until attribute counts down.

        See the dashboard sensor's copy — the tick must be a @callback or it
        runs off the event loop and raises once a minute.
        """
        if self.coordinator.is_alarm_removed(self._alarm_index):
            return
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.is_alarm_removed(self._alarm_index):
            return
        self.async_write_ha_state()


class AllariseCommandSensor(CoordinatorEntity[AllariseCoordinator], SensorEntity):
    """A sensor for an arm-widget command — grouped under the Dashboard device.

    One entity is dynamically created per command name the first time the app
    publishes {prefix}/{device}/command/{name}/status = fired|idle.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AllariseCoordinator,
        command_name: str,
    ) -> None:
        """Initialize the command sensor."""
        super().__init__(coordinator)
        self._command_name = command_name
        # Display name is the raw command name (e.g. "lr_shutdown" → "lr_shutdown")
        self._attr_name = command_name.replace("_", " ").title()
        self._attr_icon = "mdi:console"
        self._attr_unique_id = (
            f"allarise_{coordinator.device_name}_command_{command_name}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — command sensors live on the Dashboard device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"allarise_{self.coordinator.device_name}_dashboard")},
            name=f"Allarise {self.coordinator.device_name} - Dashboard",
            manufacturer="Allarise",
            model="iOS Alarm Clock",
        )

    @property
    def available(self) -> bool:
        """Return True if the app is online."""
        return self.coordinator.app_online

    @property
    def native_value(self) -> str:
        """Return 'fired' or 'idle'."""
        return self.coordinator.get_command_state(self._command_name)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
