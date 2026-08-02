"""Select platform for Allarise Alarm — sleep sound, radio station and app persistence mode."""
from __future__ import annotations

import json
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AllariseCoordinator
from .normalize import clean_options, clean_text

_LOGGER = logging.getLogger(__name__)

# Fallback list used until the iOS app publishes its full sleep sound inventory
# (bundled + custom) on the retained sensor/sleep_sounds_available topic. After the
# first publish arrives, `options` is driven entirely by the coordinator's cache,
# so custom sleep sounds imported in the app show up automatically and disappear
# when removed.
_FALLBACK_SLEEP_SOUND_OPTIONS = [
    "Brown_Noise",
    "Fan",
    "Light_Rain",
    "Mountain_Stream",
    "Rainforest_Birds",
    "Sea_Waves",
    "Summer_Rain",
    "white_noise",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Allarise select entities."""
    coordinator: AllariseCoordinator = entry.runtime_data
    async_add_entities([
        SleepSoundSelectEntity(coordinator),
        RadioStationSelectEntity(coordinator),
        AppPersistenceModeSelectEntity(coordinator),
    ])


class SleepSoundSelectEntity(SelectEntity):
    """Dropdown to start/change/stop the sleep sound playing on the iOS device.

    Named "Audio: …" along with the Radio Station select and the six transport
    buttons because all eight drive the SAME audio channel on the phone —
    starting a station stops a sleep sound and the other way round. Nothing on
    the device page said so, and two separate-looking groups of controls that
    silently interrupt each other is exactly the confusion the prefix removes.
    Display only: unique_id and existing entity_ids are unchanged.
    """

    _attr_has_entity_name = True
    _attr_name = "Audio: Sleep Sound"
    _attr_icon = "mdi:music-note-bluetooth"

    def __init__(self, coordinator: AllariseCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"allarise_{coordinator.device_name}_sleep_sound_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"allarise_{coordinator.device_name}_dashboard")},
        }

    @property
    def available(self) -> bool:
        return self._coordinator.app_online

    @property
    def options(self) -> list[str]:
        advertised = self._coordinator.get_available_sleep_sounds()
        source = advertised or _FALLBACK_SLEEP_SOUND_OPTIONS
        return ["none", *clean_options(source)]

    @property
    def current_option(self) -> str:
        val = clean_text(self._coordinator.get_dashboard_state("sleep_sound"))
        return val if val in self.options else "none"

    async def async_select_option(self, option: str) -> None:
        if option == "none":
            await self._coordinator.async_publish_command("sleep_sound_stop")
        else:
            payload = json.dumps({"sound": option})
            await self._coordinator.async_publish_command("sleep_sound_start", payload)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


# The three App Persistence modes, exactly as the app publishes them on
# sensor/app_persistence_mode. Lower case on purpose: the sibling
# sensor.<device>_app_persistence already speaks "on"/"off", so an automation
# reads the same vocabulary from both entities instead of one of them shouting.
_APP_PERSISTENCE_MODES = ["on", "off", "dynamic"]


class AppPersistenceModeSelectEntity(SelectEntity):
    """Dropdown for the app's three-way App Persistence *setting*.

    App Persistence is what keeps the iOS app resident in the background:

    * **on** — always resident. A radio alarm streams its actual station and
      MQTT keeps answering while the phone is in a pocket. Costs battery.
    * **dynamic** — resident only while it is worth it (charging, or close to an
      alarm), suspended the rest of the time.
    * **off** — never resident. Alarms still ring on time through AlarmKit, but
      a radio alarm plays its own tone instead of the station and nothing on
      that phone answers MQTT until it is opened.

    **This is the control; the sensor is the status.** ``sensor.<device>_app_persistence``
    answers "is the app resident *right now*", which under **dynamic** flips back
    and forth by itself all day. This entity answers "which of the three modes did
    the user choose", which only changes when somebody changes it. Both are useful
    and neither replaces the other. (A separate on/off *switch* used to exist; it
    was removed because this select's ``on``/``off`` options publish the identical
    command, so it was pure duplication.)

    Selecting an option publishes to the command topic the app has always listened
    on (``command/app_persistence``); "DYNAMIC" is simply a third payload it now
    accepts. No new command topic, so an automation that already publishes
    ``ON``/``OFF`` there keeps working verbatim.

    Version skew, both directions:

    * **Older app, this integration.** ``sensor/app_persistence_mode`` is never
      published, ``get_dashboard_state`` answers "Unknown", and the dropdown
      sits ``unavailable`` forever. It never invents a mode, and in particular
      never claims "off" for a phone whose persistence is on. An older app that
      does receive "DYNAMIC" on the command topic ignores it and republishes its
      unchanged state, so the entity snaps back to the truth rather than to the
      value that was asked for.
    * **Newer app, older integration.** The app publishes a topic nobody
      subscribes to. Harmless; nothing existing changes shape.

    No optimistic state: the published echo is the receipt, and showing the
    requested value immediately would destroy the one signal that says whether
    the phone actually acted.
    """

    _attr_has_entity_name = True
    _attr_name = "App Persistence Mode"
    _attr_icon = "mdi:battery-clock"
    _attr_options = _APP_PERSISTENCE_MODES

    def __init__(self, coordinator: AllariseCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = (
            f"allarise_{coordinator.device_name}_app_persistence_mode_select"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"allarise_{coordinator.device_name}_dashboard")},
        }

    def _reported_mode(self) -> str:
        """Return the published mode, lowercased, or "" when there isn't one.

        The app publishes lower case, but normalising costs nothing and means a
        hand-published retained "DYNAMIC" is not silently treated as unknown.
        `get_dashboard_state` answers "Unknown" for a topic that has never
        arrived, which is not one of the three modes and therefore reads as
        "no value" here.
        """
        raw = self._coordinator.get_dashboard_state("app_persistence_mode")
        return clean_text(str(raw)).strip().lower()

    @property
    def available(self) -> bool:
        """True only when the app is online AND has published a mode."""
        return (
            self._coordinator.app_online
            and self._reported_mode() in _APP_PERSISTENCE_MODES
        )

    @property
    def current_option(self) -> str | None:
        """Return the published mode, or None when the app has not said.

        Belt and braces with `available`: an integration reload can render an
        entity before its first message arrives, and "unknown" is truthful where
        a defaulted "off" would be an assertion we cannot back up.
        """
        mode = self._reported_mode()
        return mode if mode in _APP_PERSISTENCE_MODES else None

    async def async_select_option(self, option: str) -> None:
        """Publish the uppercase payload and wait for the app's echo."""
        if option not in _APP_PERSISTENCE_MODES:
            return
        await self._coordinator.async_publish_command(
            "app_persistence", option.upper()
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class RadioStationSelectEntity(SelectEntity):
    """Dropdown to start/stop internet radio on the iOS device.

    Deliberately the same shape as `SleepSoundSelectEntity`: options come from
    the retained inventory the app publishes, picking an entry starts playback,
    and picking "none" stops it. `radio_start` accepts a favourited station by
    name, so the option value is sent through verbatim.

    Unlike sleep sounds there is NO static fallback list — stations are whatever
    the user has favourited in the app, so before the first publish arrives the
    dropdown offers only "none" rather than inventing names that would fail to
    resolve.

    Shares the "Audio:" prefix with the sleep-sound select and the six
    transport buttons — see that class for why. Radio playback also needs App
    Persistence on the phone; without it iOS suspends the app and the stream
    stops.
    """

    _attr_has_entity_name = True
    _attr_name = "Audio: Radio Station"
    _attr_icon = "mdi:radio"

    def __init__(self, coordinator: AllariseCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"allarise_{coordinator.device_name}_radio_station_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"allarise_{coordinator.device_name}_dashboard")},
        }

    @property
    def available(self) -> bool:
        return self._coordinator.app_online

    @property
    def options(self) -> list[str]:
        # Already cleaned in the coordinator; cleaned again here so the
        # de-duplication also covers whatever an older app build published.
        return ["none", *clean_options(
            self._coordinator.get_available_radio_stations(), ignore_case=True
        )]

    @property
    def current_option(self) -> str:
        val = clean_text(self._coordinator.get_dashboard_state("radio_station"))
        return val if val in self.options else "none"

    async def async_select_option(self, option: str) -> None:
        # The option is the CLEANED station name, which need not be byte-identical
        # to the name stored in the app's favourite (a trailing space or a
        # zero-width joiner may have been removed). App builds from this release
        # on match on the normalised name after an exact match fails, so the
        # cleaned name resolves; older builds match case-insensitively on the
        # exact name, which still covers every station whose name needed no
        # cleaning — i.e. almost all of them.
        if option == "none":
            await self._coordinator.async_publish_command("radio_stop")
        else:
            payload = json.dumps({"station": option})
            await self._coordinator.async_publish_command("radio_start", payload)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
