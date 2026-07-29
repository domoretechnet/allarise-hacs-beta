"""Service descriptions filled in at runtime from what the phone has told us.

``services.yaml`` is static, so every field it describes has to be written for
a user who has no devices, no commands and no alarms. That is why "which
command runs on a swipe" was a free-text box: the answer lives on the phone,
not in this repo.

Home Assistant lets an integration replace a service's *description* at
runtime with :func:`homeassistant.helpers.service.async_set_service_schema`.
The dict it takes has exactly the shape of one service's entry in
``services.yaml``, so the mechanism here is deliberately small:

1. Read ``services.yaml`` once — it stays the single source of every name,
   description and example, and remains the fallback Home Assistant shows if
   this module never runs.
2. Deep-copy it and swap the *selector* on the handful of fields whose valid
   values are known at runtime, leaving all the copy alone.
3. Push the result, and push it again when the data behind it changes.

Two rules this module must not break.

**It changes what the UI produces, never what the service accepts.** The
voluptuous schemas in ``__init__.py`` are untouched by anything here — every
dropdown carries ``custom_value: true``, so a name that is not in the list can
still be typed, and a YAML automation written years ago never sees any of this.

**Empty is a valid answer.** An older app publishes no sound inventory and no
command statuses; a phone that has been offline since restart has published
nothing at all. Every list here can be empty, and an empty list yields an empty
dropdown the user can type into — never an error, and never an invented value.

Known limitation: a Home Assistant page that is *already open* keeps the
description it fetched when it loaded. New options appear on the next page
load or reload of the automation editor, not while the user is looking at it.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.util.yaml import load_yaml_dict

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import AllariseCoordinator
from .normalize import clean_options

_LOGGER = logging.getLogger(__name__)

DATA_SERVICE_SCHEMA = f"{DOMAIN}_service_schema"

# Seconds to let MQTT settle before rebuilding. The coordinator notifies its
# listeners on every message it handles — a phone coming online replays dozens
# of retained topics in a second — and rebuilding nine descriptions each time
# would be pure waste. Nothing here is time-critical: the automation editor
# reads the description when it opens.
REFRESH_COOLDOWN = 15.0

SERVICES_YAML = Path(__file__).parent / "services.yaml"

# ─── Which field takes which list ─────────────────────────────────────

DEVICE = "device"
COMMAND = "command"
COMMAND_LIST = "command_list"
ALARM_NAME = "alarm_name"
ALARM_INDEX = "alarm_index"
SOUND = "sound"
RADIO_STATION = "radio_station"

_ALARM_FIELDS = {
    "device_name": DEVICE,
    "sound": SOUND,
    "radio_station": RADIO_STATION,
    "swipe_left_command": COMMAND,
    "swipe_right_command": COMMAND,
    "alarm_screen_commands": COMMAND_LIST,
}

# Anything not listed keeps the selector services.yaml gives it. `days`,
# `mission`, `snooze_mode`, `arm_action` and the colour presets are fixed sets
# that belong in the static file — they are behaviours, not device data.
DYNAMIC_FIELDS: dict[str, dict[str, str]] = {
    # update_alarm's index is REQUIRED and is the number the whole service
    # hangs off, so it gets the same "3 — Weekday Wake" picker delete_alarm
    # has. The select emits a digit string, which the field's existing
    # `vol.Coerce(int)` accepts, so a typed or templated number is unaffected.
    # create_alarm has no index — the app assigns it.
    "update_alarm": {**_ALARM_FIELDS, "index": ALARM_INDEX},
    "create_alarm": _ALARM_FIELDS,
    "trigger_alert": {"device_name": DEVICE, "sound": SOUND},
    "dismiss": {"device_name": DEVICE},
    "snooze": {"device_name": DEVICE},
    "skip": {"device_name": DEVICE},
    "delete_alarm": {
        "device_name": DEVICE,
        "name": ALARM_NAME,
        "index": ALARM_INDEX,
    },
    "create_command": {"device_name": DEVICE},
    "delete_command": {"device_name": DEVICE, "name": COMMAND},
}


@dataclass(frozen=True)
class AllariseOptions:
    """Everything the dropdowns are filled from, as of one moment.

    Lists are the union across every loaded device, because a service field is
    a single field for all of them — the device picker is what narrows it. With
    the usual single phone the union is exact; with two, a name from the other
    phone may be offered and the app that receives it ignores a command it does
    not have, exactly as it always did for a typed name.
    """

    devices: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    alarm_choices: list[tuple[int, str]] = field(default_factory=list)
    alarm_sounds: list[str] = field(default_factory=list)
    radio_stations: list[str] = field(default_factory=list)

    @property
    def alarm_names(self) -> list[str]:
        """Alarm names in index order, de-duplicated."""
        return clean_options([name for _, name in self.alarm_choices])


def collect_options(
    pairs: list[tuple[str, AllariseCoordinator]],
) -> AllariseOptions:
    """Build the option lists from (raw device name, coordinator) pairs.

    The device name is the RAW value from the config entry, not
    ``coordinator.device_name``: the latter is sanitised for MQTT topics
    (lowercased, spaces to dashes) while ``_find_coordinator`` matches the raw
    string case-sensitively. A picker offering the sanitised form would produce
    values that resolve to no device at all.
    """
    devices: list[str] = []
    commands: list[str] = []
    alarm_choices: list[tuple[int, str]] = []
    sounds: list[str] = []
    stations: list[str] = []

    for raw_name, coordinator in pairs:
        devices.append(raw_name)
        commands.extend(coordinator.known_commands)
        alarm_choices.extend(coordinator.get_alarm_choices())
        sounds.extend(coordinator.get_available_alarm_sounds())
        stations.extend(coordinator.get_available_radio_stations())

    return AllariseOptions(
        devices=clean_options(devices),
        # Alphabetical: the union of two phones' commands otherwise arrives in
        # device order, which is not an order the reader can see.
        commands=sorted(clean_options(commands), key=str.casefold),
        # Indexes are per device, so two phones can both have an alarm 3. The
        # pair is kept whole and merged onto one option below.
        alarm_choices=sorted(alarm_choices, key=lambda choice: choice[0]),
        alarm_sounds=clean_options(sounds),
        # Matched case-insensitively by the app, same as the Radio Station
        # select entity — offering "WDET" and "wdet" offers a choice that does
        # not exist.
        radio_stations=clean_options(stations, ignore_case=True),
    )


# ─── Selector construction ────────────────────────────────────────────


def _select(options: list[Any], *, multiple: bool = False) -> dict[str, Any]:
    """A dropdown that is always typeable.

    ``custom_value: true`` is the whole point: the list is a convenience, and
    the underlying schema still takes any string. It is set even when the list
    is empty, which is what turns "the app has told us nothing" into an empty
    box the user can type into rather than a dead control.
    """
    config: dict[str, Any] = {"custom_value": True, "options": options}
    if multiple:
        config["multiple"] = True
    return {"select": config}


def _field_selector(kind: str, options: AllariseOptions) -> dict[str, Any] | None:
    """Return the selector for one dynamic field, or None to leave it alone."""
    if kind == DEVICE:
        return _select(options.devices)
    if kind == COMMAND:
        return _select(options.commands)
    if kind == COMMAND_LIST:
        # With no commands known there is nothing to pick from, and an empty
        # multi-select is a worse box to type four names into than the object
        # editor this field has always had. Fall back to whatever
        # services.yaml specified rather than to a select with no options.
        if not options.commands:
            return None
        return _select(options.commands, multiple=True)
    if kind == ALARM_NAME:
        return _select(options.alarm_names)
    if kind == ALARM_INDEX:
        # Value stays a string of digits, which `vol.Coerce(int)` accepts, so
        # the schema is unchanged and a typed or templated number still works.
        #
        # Indexes are per device, so with two phones set up the same number can
        # name a different alarm on each. A select with the same value twice is
        # a broken control, so the names are merged onto one option — which
        # also puts the ambiguity in front of the user instead of hiding it.
        by_index: dict[int, list[str]] = {}
        for index, name in options.alarm_choices:
            names = by_index.setdefault(index, [])
            if name not in names:
                names.append(name)
        choices = [
            {"value": str(index), "label": f"{index} — {' / '.join(names)}"}
            for index, names in sorted(by_index.items())
        ]
        if not choices:
            # Nothing discovered yet: a number box is a better empty state than
            # an empty dropdown, and it is what the static file already says.
            return None
        return _select(choices)
    if kind == SOUND:
        return _select(options.alarm_sounds)
    if kind == RADIO_STATION:
        return _select(options.radio_stations)
    _LOGGER.debug("Unknown dynamic field kind %r — leaving selector alone", kind)
    return None


def build_service_descriptions(
    base: dict[str, Any], options: AllariseOptions
) -> dict[str, dict[str, Any]]:
    """Return the full description for every service, dropdowns filled in.

    Pure: no hass, no I/O. ``base`` is the parsed ``services.yaml``; the result
    is one ``async_set_service_schema`` payload per service. Any field not
    named in :data:`DYNAMIC_FIELDS` — and any service that is not in the base
    file — comes through untouched, so the static file stays the source of all
    copy and this function only ever swaps selectors.
    """
    descriptions: dict[str, dict[str, Any]] = {}

    for service, body in base.items():
        description = copy.deepcopy(body)
        fields = description.get("fields") or {}
        for name, kind in DYNAMIC_FIELDS.get(service, {}).items():
            spec = fields.get(name)
            if spec is None:
                # The field was removed from services.yaml, or renamed. Not
                # worth an error: the service still accepts it either way.
                _LOGGER.debug(
                    "Service %s has no field %r to make dynamic", service, name
                )
                continue
            selector = _field_selector(kind, options)
            if selector is not None:
                spec["selector"] = selector
        descriptions[service] = description

    return descriptions


# ─── Wiring ───────────────────────────────────────────────────────────


class AllariseServiceSchemas:
    """Keeps the nine service descriptions in step with the loaded devices."""

    def __init__(self, hass: HomeAssistant, base: dict[str, Any]) -> None:
        """Store the parsed services.yaml and build the debouncer."""
        self.hass = hass
        self._base = base
        self._entries: dict[str, ConfigEntry] = {}
        self._unsubs: dict[str, Any] = {}
        self._fingerprint: AllariseOptions | None = None
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=REFRESH_COOLDOWN,
            immediate=False,
            function=self.async_refresh,
            background=True,
        )

    @property
    def tracked(self) -> bool:
        """True while at least one entry is loaded."""
        return bool(self._entries)

    def track(self, entry: ConfigEntry) -> None:
        """Follow one loaded config entry and its coordinator's updates."""
        self._entries[entry.entry_id] = entry
        coordinator: AllariseCoordinator = entry.runtime_data
        self._unsubs[entry.entry_id] = coordinator.async_add_listener(
            self._schedule_refresh
        )

    def untrack(self, entry: ConfigEntry) -> None:
        """Stop following an entry that is being unloaded."""
        self._entries.pop(entry.entry_id, None)
        unsub = self._unsubs.pop(entry.entry_id, None)
        if unsub is not None:
            unsub()

    @callback
    def _schedule_refresh(self) -> None:
        """Coalesce a burst of MQTT updates into one rebuild."""
        self._debouncer.async_schedule_call()

    def _collect(self) -> AllariseOptions:
        return collect_options(
            [
                (entry.data[CONF_DEVICE_NAME], entry.runtime_data)
                for entry in self._entries.values()
            ]
        )

    async def async_refresh(self) -> None:
        """Rebuild and publish the descriptions, if anything actually changed."""
        options = self._collect()
        if options == self._fingerprint:
            return
        self._fingerprint = options

        for service, description in build_service_descriptions(
            self._base, options
        ).items():
            if not self.hass.services.has_service(DOMAIN, service):
                # A service described in the YAML that this version does not
                # register. Setting a description for it would be harmless but
                # pointless.
                continue
            async_set_service_schema(self.hass, DOMAIN, service, description)

        _LOGGER.debug(
            "Refreshed Allarise service descriptions: %d device(s), %d command(s), "
            "%d alarm(s), %d sound(s), %d station(s)",
            len(options.devices),
            len(options.commands),
            len(options.alarm_choices),
            len(options.alarm_sounds),
            len(options.radio_stations),
        )

    async def async_shutdown(self) -> None:
        """Drop every listener and cancel any pending rebuild."""
        for unsub in self._unsubs.values():
            unsub()
        self._unsubs.clear()
        self._entries.clear()
        self._debouncer.async_cancel()


async def async_register_service_schemas(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Start (or extend) dynamic service descriptions for a loaded entry.

    Safe to fail: if services.yaml cannot be read for any reason, Home
    Assistant simply keeps showing the static description, which is complete
    and correct — only the prefilled options are lost.
    """
    manager: AllariseServiceSchemas | None = hass.data.get(DATA_SERVICE_SCHEMA)
    if manager is None:
        try:
            base = await hass.async_add_executor_job(load_yaml_dict, SERVICES_YAML)
        except Exception:  # noqa: BLE001 — never block setup over a description
            _LOGGER.exception(
                "Could not read services.yaml — service fields will keep their "
                "static descriptions"
            )
            return
        manager = AllariseServiceSchemas(hass, base)
        hass.data[DATA_SERVICE_SCHEMA] = manager

    manager.track(entry)
    await manager.async_refresh()


async def async_unregister_service_schemas(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Drop an unloaded entry from the pickers, and tidy up after the last one."""
    manager: AllariseServiceSchemas | None = hass.data.get(DATA_SERVICE_SCHEMA)
    if manager is None:
        return

    manager.untrack(entry)
    if not manager.tracked:
        await manager.async_shutdown()
        hass.data.pop(DATA_SERVICE_SCHEMA, None)
        return

    await manager.async_refresh()
