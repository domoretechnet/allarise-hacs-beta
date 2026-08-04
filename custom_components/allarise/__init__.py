"""The Allarise Alarm integration — MQTT-based."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_DEVICE_NAME,
    CONF_TOPIC_PREFIX,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import AllariseCoordinator
from .media import async_resolve_media_url
from .normalize import (
    clean_service_data,
    mission_difficulty_config,
    normalize_color_components,
    normalize_days,
    rgb_list_to_components,
)
from .service_schema import (
    async_register_service_schemas,
    async_unregister_service_schemas,
)

_LOGGER = logging.getLogger(__name__)

type AllariseConfigEntry = ConfigEntry[AllariseCoordinator]

# ─── Permissive field validators ──────────────────────────────────────
#
# Each of these widens what the schema *accepts* without narrowing anything.
# The rule they exist to serve: a selector may change what the UI produces, it
# may never change what an automation written years ago is allowed to send.

# "days" has been advertised as a comma-separated string, is read by the app as
# [Int], and is now produced by a weekday multi-select as a list of names. All
# three arrive here; `normalize_days` converts whichever it is in the handler.
DAYS_VALUE = vol.Any(
    vol.Coerce(int),
    cv.string,
    [vol.Any(vol.Coerce(int), cv.string)],
)


def _fade_in_value(maximum: int):
    """Build a validator that keeps a boolean distinguishable from a number.

    `fade_in` is a boolean to the app and a number in this integration's UI, and
    the two mean different things — `true` flips the fade toggle and leaves the
    duration alone, `5` asks for a five-minute ramp. `cv.boolean` would swallow
    the number (it treats any non-zero number as true) and `vol.Coerce(int)`
    would flatten the boolean to 1, so neither can be used on its own. This
    keeps a real bool a bool and everything else a number, which is what lets
    the handler decide correctly which extra key to add.
    """

    def validator(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "on", "yes"):
                return True
            if lowered in ("false", "off", "no"):
                return False
        return vol.All(vol.Coerce(int), vol.Range(min=0, max=maximum))(value)

    return validator


# Alarm fade-in is in MINUTES. The app clamps to 1–15 (0 = off), the selector
# now offers 0–15, and 60 stays accepted because it always was.
FADE_IN_MINUTES = _fade_in_value(60)

# Alert fade-in is in SECONDS and is a different feature — the alert handler
# clamps a number to 5–300 and reads `true` as the app's 30-second ramp.
ALERT_FADE_IN_SECONDS = _fade_in_value(300)

# The app clamps `fade_in_duration` to 15 minutes; anything above that is the
# same alarm with a misleading payload, so the clamp happens before publish.
MAX_FADE_IN_MINUTES = 15

# ─── Service schemas ──────────────────────────────────────────────────

SERVICE_UPDATE_ALARM = "update_alarm"
SERVICE_TRIGGER_ALERT = "trigger_alert"
SERVICE_DISMISS = "dismiss"
SERVICE_SNOOZE = "snooze"
SERVICE_SKIP = "skip"
SERVICE_CREATE_ALARM = "create_alarm"
SERVICE_DELETE_ALARM = "delete_alarm"
SERVICE_CREATE_COMMAND = "create_command"
SERVICE_DELETE_COMMAND = "delete_command"

ATTR_DEVICE_NAME = "device_name"

SCHEMA_UPDATE_ALARM = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_NAME): cv.string,
        vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("time"): cv.string,
        vol.Optional("label"): cv.string,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("days"): DAYS_VALUE,
        vol.Optional("sound"): cv.string,
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("vibrate"): cv.boolean,
        vol.Optional("fade_in"): FADE_IN_MINUTES,
        vol.Optional("fade_in_duration"): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        # snooze_limit/snooze_interval are this integration's original names and
        # keep working; snooze_duration/max_snooze_count are the names the app
        # actually reads, accepted here so one payload works on both services.
        vol.Optional("snooze_limit"): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
        vol.Optional("snooze_interval"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional("snooze_duration"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional("max_snooze_count"): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
        vol.Optional("snooze_mode"): cv.string,
        vol.Optional("skip_mode"): cv.string,
        vol.Optional("snooze_hold_duration"): vol.Coerce(float),
        vol.Optional("skip_hold_duration"): vol.Coerce(float),
        # "mission" sets the FIRST mission and clears any others the alarm has.
        # "missions" sets the whole ordered sequence (max 5). Each element is
        # either a bare type name ("math") or an object in the same shape as the
        # single-mission form: {"mission": "math", "mission_config": {...}}.
        # Both stay accepted verbatim; only "missions" and "mission_config" were
        # taken out of the visible UI, which changes nothing about the API.
        vol.Optional("mission"): cv.string,
        vol.Optional("mission_config"): dict,
        vol.Optional("missions"): vol.All(cv.ensure_list, vol.Length(min=0, max=5)),
        # Integration-only convenience: folded into mission_config on the way
        # out under whichever key the chosen mission uses. Never published.
        vol.Optional("mission_difficulty"): cv.string,
        # "notes" replaces the after-alarm Notes page text; "append_notes" adds a
        # line to it instead (prefixed "HA:", divider-separated) so a routine can
        # build a briefing up over several steps without re-sending the whole
        # note or racing anything else writing to it.
        vol.Optional("notes"): cv.string,
        vol.Optional("append_notes"): cv.string,
        vol.Optional("alarm_screen_commands"): vol.All(cv.ensure_list, vol.Length(min=0, max=4)),
        vol.Optional("morning_weather"): cv.boolean,
        # The ordered after-alarm sequence: notes / verse / weather / appLink.
        # Deliberately validated as "a list of strings" and no more — the app
        # ignores an id it does not know with a log line, so rejecting one here
        # would be stricter than the wire and would break the day the app adds
        # a fifth step. Omitting the key changes nothing; see Documents/15.
        vol.Optional("after_alarm_actions"): vol.All(cv.ensure_list, [cv.string]),
        # Single-step toggle for the one after-alarm step that has no config
        # field of its own. `verse` is the app's own alias and is accepted so a
        # payload copied out of Documents/15 is not rejected here; both are
        # passed through verbatim and the app resolves them.
        vol.Optional("verse_of_day"): cv.boolean,
        vol.Optional("verse"): cv.boolean,
        vol.Optional("dismiss_app_uri"): cv.string,
        vol.Optional("snooze_app_uri"): cv.string,
        # A favourited station's name or UUID; "" clears the wake-up station.
        vol.Optional("radio_station"): cv.string,
        vol.Optional("swipe_left_command"): cv.string,
        vol.Optional("swipe_right_command"): cv.string,
    }
)

SCHEMA_TRIGGER_ALERT = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_NAME): cv.string,
        vol.Required("message"): cv.string,
        vol.Optional("title"): cv.string,
        # Dedupe key. Re-sending the same alert_id replaces the pending
        # notification instead of stacking a second one beside it.
        vol.Optional("alert_id"): cv.string,
        vol.Optional("sound"): cv.string,
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("play_sound"): cv.boolean,
        # Seconds, not minutes — this is the alert's own ramp, unrelated to an
        # alarm's fade_in. `true` selects the app's 30-second ramp.
        vol.Optional("fade_in"): ALERT_FADE_IN_SECONDS,
        vol.Optional("media_url"): cv.string,
        vol.Optional("image_url"): cv.string,
        vol.Optional("video_url"): cv.string,
        vol.Optional("link_url"): cv.string,
    }
)

SCHEMA_SIMPLE = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_NAME): cv.string,
    }
)

# create_alarm mirrors update_alarm's field set, minus "index" (the app assigns
# one) and plus "ephemeral". Only "time" is required — everything else falls back
# to the user's defaults on the phone, which is what the app does for a manually
# created alarm too.
SCHEMA_CREATE_ALARM = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_NAME): cv.string,
        vol.Required("time"): cv.string,
        vol.Optional("label"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("days"): DAYS_VALUE,
        vol.Optional("sound"): cv.string,
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("vibrate"): cv.boolean,
        vol.Optional("fade_in"): FADE_IN_MINUTES,
        vol.Optional("fade_in_duration"): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        vol.Optional("mission"): cv.string,
        vol.Optional("mission_config"): dict,
        vol.Optional("missions"): vol.All(cv.ensure_list, vol.Length(min=0, max=5)),
        vol.Optional("mission_difficulty"): cv.string,
        vol.Optional("snooze_duration"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional("max_snooze_count"): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
        # Accepted for symmetry with update_alarm, so a payload copied between
        # the two services keeps working either way round.
        vol.Optional("snooze_interval"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional("snooze_limit"): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
        vol.Optional("snooze_mode"): cv.string,
        vol.Optional("skip_mode"): cv.string,
        vol.Optional("snooze_hold_duration"): vol.Coerce(float),
        vol.Optional("skip_hold_duration"): vol.Coerce(float),
        vol.Optional("notes"): cv.string,
        vol.Optional("alarm_screen_commands"): vol.All(cv.ensure_list, vol.Length(min=0, max=4)),
        vol.Optional("morning_weather"): cv.boolean,
        # Same pair as update_alarm — the app accepts both on create, and
        # "absent = unchanged" holds even on a brand new alarm.
        vol.Optional("after_alarm_actions"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("verse_of_day"): cv.boolean,
        vol.Optional("verse"): cv.boolean,
        vol.Optional("dismiss_app_uri"): cv.string,
        vol.Optional("snooze_app_uri"): cv.string,
        vol.Optional("swipe_left_command"): cv.string,
        vol.Optional("swipe_right_command"): cv.string,
        vol.Optional("radio_station"): cv.string,
        # One-shot alarm: deleted by the app after it fires. Gets no per-alarm
        # MQTT entities (index 0), so nothing to clean up in HA either.
        vol.Optional("ephemeral"): cv.boolean,
        vol.Optional("mqtt_id"): cv.string,
    }
)

# The app accepts EITHER index or name here. Enforcing "exactly one" in the
# schema gives a clear error in the UI instead of a silent no-op on the phone.
SCHEMA_DELETE_ALARM = vol.Schema(
    vol.All(
        {
            vol.Optional(ATTR_DEVICE_NAME): cv.string,
            vol.Optional("index"): vol.All(vol.Coerce(int), vol.Range(min=1)),
            vol.Optional("name"): cv.string,
        },
        cv.has_at_least_one_key("index", "name"),
    )
)

SCHEMA_CREATE_COMMAND = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_NAME): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("icon"): cv.string,
        vol.Optional("color"): cv.string,
        # The app reads these as 0.0–1.0 floats and clamps. This integration
        # offered 0–255 integers, which the app clamped to 1.0 — every colour
        # set from the UI came out white. Both scales are now accepted and
        # converted on publish; see `normalize_color_components` for how the
        # scale is decided. The range stays 0–255 so nothing that validated
        # before is rejected now, and Coerce(float) replaces Coerce(int) so a
        # documented 0.35 stops being truncated to 0.
        vol.Optional("color_r"): vol.All(vol.Coerce(float), vol.Range(min=0, max=255)),
        vol.Optional("color_g"): vol.All(vol.Coerce(float), vol.Range(min=0, max=255)),
        vol.Optional("color_b"): vol.All(vol.Coerce(float), vol.Range(min=0, max=255)),
        # New, additive: Home Assistant's colour wheel hands back [r, g, b] as
        # 0–255. Converted into color_r/color_g/color_b on the way out — the
        # wire keys are unchanged and this one never reaches the phone.
        vol.Optional("color_rgb"): vol.All(
            cv.ensure_list, vol.Length(min=3, max=3), [vol.Coerce(float)]
        ),
        vol.Optional("arm_action"): cv.string,
        vol.Optional("show_in_list"): cv.boolean,
    }
)

SCHEMA_DELETE_COMMAND = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_NAME): cv.string,
        vol.Required("name"): cv.string,
    }
)

def _find_coordinator(
    hass: HomeAssistant, device_name: str | None
) -> AllariseCoordinator | None:
    """Find the coordinator for a given device name.

    If device_name is None or empty, auto-resolves to the only configured
    entry when exactly one exists. Useful when only one Allarise device is set up.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not device_name:
        if len(entries) == 1:
            return entries[0].runtime_data
        _LOGGER.error(
            "device_name is required when multiple Allarise devices are configured "
            "(found %d). Available: %s",
            len(entries),
            [e.data.get(CONF_DEVICE_NAME) for e in entries],
        )
        return None
    for entry in entries:
        if entry.data.get(CONF_DEVICE_NAME) == device_name:
            return entry.runtime_data
    return None


def _call_data(call: ServiceCall, exclude: set[str] | None = None) -> dict[str, Any]:
    """Return the service data minus the routing fields."""
    exclude = (exclude or set()) | {ATTR_DEVICE_NAME}
    return {k: v for k, v in call.data.items() if k not in exclude}


def _prepare_alarm_data(call: ServiceCall) -> dict[str, Any]:
    """Translate create_alarm / update_alarm data into what the app reads.

    Three fields have been accepted by the schema, shown in the UI, and then
    silently ignored by the phone because the key or the type on the wire was
    not the one the app parses (al-b7w). Every fix here is **additive** — the
    key the caller sent is left in the payload exactly as sent, and the key the
    app reads is added beside it — so an older app keeps behaving the way it
    always did and a newer one starts behaving the way the UI always claimed.

    - ``days``: the app reads ``[Int]`` only. A comma-separated string (what
      this integration documented), a list of weekday names (what the new
      selector produces) and a list of numeric strings (what a template
      produces) all became "no recurring days" without a word in any log.
    - ``snooze_interval`` / ``snooze_limit`` on update_alarm: the app reads
      ``snooze_duration`` / ``max_snooze_count``. Both pairs are now published.
    - ``fade_in`` as a number: the app treats ``fade_in`` as a boolean and takes
      the minutes from ``fade_in_duration``, so the number was read as a toggle
      or as nothing at all.
    """
    data = _call_data(call)

    if "days" in data:
        days, unrecognised = normalize_days(data["days"])
        if unrecognised:
            _LOGGER.warning(
                "Allarise %s: ignoring unrecognised day(s) %s — use weekday names "
                "or numbers 1-7 (1=Sunday). Sending days=%s",
                call.service,
                ", ".join(unrecognised),
                days,
            )
        data["days"] = days

    # A real boolean means "flip the fade toggle, leave the duration alone" and
    # must stay a lone boolean. A number is minutes, which only reaches the app
    # through fade_in_duration. The original fade_in value is deliberately left
    # untouched: an app build that read it as a number still gets the number.
    fade_in = data.get("fade_in")
    if (
        fade_in is not None
        and not isinstance(fade_in, bool)
        and "fade_in_duration" not in data
    ):
        data["fade_in_duration"] = min(max(int(fade_in), 0), MAX_FADE_IN_MINUTES)

    for legacy, current in (
        ("snooze_interval", "snooze_duration"),
        ("snooze_limit", "max_snooze_count"),
    ):
        if legacy in data and current not in data:
            data[current] = data[legacy]

    _apply_mission_difficulty(call, data)
    return data


def _apply_mission_difficulty(call: ServiceCall, data: dict[str, Any]) -> None:
    """Fold the Mission Difficulty dropdown into ``mission_config`` in place.

    Difficulty has a different key per mission type, which is why the UI needs
    one dropdown and the payload needs a lookup. An explicit ``mission_config``
    from the caller always wins — this only fills a key the caller did not set,
    so a hand-written config is never rewritten by a dropdown they did not
    touch.

    It deliberately does nothing without ``mission``. The app only applies
    ``mission_config`` when ``mission`` (or ``missions``) is present, so making
    this work alone would mean synthesising a ``mission`` key — and sending
    ``mission`` *clears mission slots 2–5*. Quietly destroying a three-mission
    alarm because someone nudged a difficulty is far worse than the field not
    applying, so it warns instead.
    """
    difficulty = data.pop("mission_difficulty", None)
    if difficulty in (None, ""):
        return

    if "missions" in data:
        _LOGGER.warning(
            "Allarise %s: mission_difficulty ignored because 'missions' was also "
            "supplied — set the difficulty inside each entry's mission_config",
            call.service,
        )
        return

    mission = data.get("mission")
    if not mission:
        _LOGGER.warning(
            "Allarise %s: mission_difficulty needs 'mission' to be set in the same "
            "call — it cannot change the difficulty of the alarm's existing "
            "mission, because sending 'mission' on its own would clear mission "
            "slots 2-5",
            call.service,
        )
        return

    mapped = mission_difficulty_config(mission, difficulty)
    if not mapped:
        _LOGGER.warning(
            "Allarise %s: mission '%s' has no difficulty setting, or '%s' is not a "
            "level it understands — difficulty not applied",
            call.service,
            mission,
            difficulty,
        )
        return

    config = dict(data.get("mission_config") or {})
    for key, value in mapped.items():
        config.setdefault(key, value)
    data["mission_config"] = config


def _prepare_command_data(call: ServiceCall) -> dict[str, Any]:
    """Translate create_command data into what the app reads.

    The icon colour is the whole of it: the app takes 0.0–1.0 floats, this
    integration's UI produced 0–255 integers, and the app's clamp turned every
    one of them into white (al-3su). Both scales are accepted now and 0.0–1.0
    is what goes on the wire, under the same three keys as always.
    """
    data = _call_data(call)

    components: dict[str, float] = {}
    rgb = data.pop("color_rgb", None)
    if rgb is not None:
        components.update(rgb_list_to_components(rgb))
    # An explicitly supplied channel beats the colour wheel, matching how the
    # app already lets color_r/g/b override the `color` preset name.
    components.update(normalize_color_components(data))
    data.update(components)
    return data


def _build_payload(
    call: ServiceCall,
    exclude: set[str] | None = None,
    *,
    data: dict[str, Any] | None = None,
) -> str:
    """Build a JSON payload from service call data, excluding specified keys.

    Every string goes through `clean_service_data` on the way out. That strips
    control characters, normalises line endings, collapses whitespace runs in
    single-line fields and trims the ends — so `" Lights"` typed with a stray
    space into the automation editor finds the command actually called
    `"Lights"`, and a zero-width character pasted in from a web page stops
    making two identical-looking names compare unequal.

    Deliberately a normalisation and not a validation: nothing here can reject a
    call. A field that used to be accepted is still accepted, it is just
    accepted in a tidier form. See `normalize.py` for the field-by-field rules —
    `notes` keeps its newlines, URLs keep their internal spacing.

    Pass `data` when the caller has already translated the payload (see
    `_prepare_alarm_data`); otherwise the call's own data is used as-is.
    """
    if data is None:
        data = _call_data(call, exclude)
    return json.dumps(clean_service_data(data))


async def async_setup_entry(hass: HomeAssistant, entry: AllariseConfigEntry) -> bool:
    """Set up Allarise Alarm from a config entry."""
    device_name = entry.data[CONF_DEVICE_NAME]
    topic_prefix = entry.data.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)

    coordinator = AllariseCoordinator(
        hass,
        device_name=device_name,
        topic_prefix=topic_prefix,
        config_entry_id=entry.entry_id,
    )

    # Store coordinator in entry runtime data
    entry.runtime_data = coordinator

    # Start MQTT subscriptions
    await coordinator.async_setup()

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register custom services (once per domain)
    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_ALARM):
        _register_services(hass)

    # Fill the service fields' dropdowns from this device's own data — the
    # registered devices, its commands, its alarms, its sounds and stations.
    # Must run after the services exist; a failure in here never fails setup,
    # it just leaves the static services.yaml description in place.
    await async_register_service_schemas(hass, entry)

    _LOGGER.info("Allarise integration set up for %s (MQTT prefix: %s)", device_name, topic_prefix)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: AllariseConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: AllariseCoordinator = entry.runtime_data
    # Before the coordinator goes: drop this device from the service pickers
    # and stop listening to it, so a reload does not leave a stale listener
    # pointing at a dead coordinator.
    await async_unregister_service_schemas(hass, entry)
    await coordinator.async_shutdown()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Unregister services and frontend panel if no more entries.
    # The entry being unloaded is still in async_entries() at this point, so it
    # has to be excluded — counting it meant the list was never empty and the
    # services survived until a restart, still pointing at coordinators that no
    # longer exist.
    remaining = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]
    if not remaining:
        for service in (
            SERVICE_UPDATE_ALARM,
            SERVICE_TRIGGER_ALERT,
            SERVICE_DISMISS,
            SERVICE_SNOOZE,
            SERVICE_SKIP,
            SERVICE_CREATE_ALARM,
            SERVICE_DELETE_ALARM,
            SERVICE_CREATE_COMMAND,
            SERVICE_DELETE_COMMAND,
        ):
            hass.services.async_remove(DOMAIN, service)

    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    """Register all Allarise custom services."""

    async def handle_update_alarm(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        payload = _build_payload(call, data=_prepare_alarm_data(call))
        await coordinator.async_publish_command("update_alarm", payload)

    async def handle_trigger_alert(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        # Build payload and sign any local HA URLs so the phone can fetch them
        # remotely via HA's reverse proxy (same approach as sgtbatten blueprint).
        # Normalise BEFORE resolving media URLs — a trailing newline on a
        # media_source id pasted out of the media browser makes the resolve
        # fail, and the alert then arrives with no audio and no explanation.
        data = clean_service_data(
            {k: v for k, v in call.data.items() if k != ATTR_DEVICE_NAME}
        )
        # A media reference that will not resolve drops out of the payload with a
        # warning; the alert itself still goes to the phone. Losing the audio is
        # recoverable, losing the alert is not.
        for key in ("media_url", "image_url", "video_url"):
            if key in data:
                resolved = await async_resolve_media_url(hass, data[key], None)
                if resolved is None:
                    data.pop(key)
                else:
                    data[key] = resolved
        payload = json.dumps(data)
        await coordinator.async_publish_command("alert", payload)

    async def handle_dismiss(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command("dismiss")

    async def handle_snooze(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command("snooze")

    async def handle_skip(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command("skip")

    async def handle_create_alarm(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command(
            "create_alarm", _build_payload(call, data=_prepare_alarm_data(call))
        )

    async def handle_delete_alarm(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command("delete_alarm", _build_payload(call))

    async def handle_create_command(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command(
            "create_command", _build_payload(call, data=_prepare_command_data(call))
        )

    async def handle_delete_command(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data.get(ATTR_DEVICE_NAME))
        if coordinator is None:
            _LOGGER.error("Device not found: %s", call.data.get(ATTR_DEVICE_NAME))
            return
        await coordinator.async_publish_command("delete_command", _build_payload(call))

    hass.services.async_register(DOMAIN, SERVICE_UPDATE_ALARM, handle_update_alarm, schema=SCHEMA_UPDATE_ALARM)
    hass.services.async_register(DOMAIN, SERVICE_TRIGGER_ALERT, handle_trigger_alert, schema=SCHEMA_TRIGGER_ALERT)
    hass.services.async_register(DOMAIN, SERVICE_DISMISS, handle_dismiss, schema=SCHEMA_SIMPLE)
    hass.services.async_register(DOMAIN, SERVICE_SNOOZE, handle_snooze, schema=SCHEMA_SIMPLE)
    hass.services.async_register(DOMAIN, SERVICE_SKIP, handle_skip, schema=SCHEMA_SIMPLE)
    hass.services.async_register(DOMAIN, SERVICE_CREATE_ALARM, handle_create_alarm, schema=SCHEMA_CREATE_ALARM)
    hass.services.async_register(DOMAIN, SERVICE_DELETE_ALARM, handle_delete_alarm, schema=SCHEMA_DELETE_ALARM)
    hass.services.async_register(DOMAIN, SERVICE_CREATE_COMMAND, handle_create_command, schema=SCHEMA_CREATE_COMMAND)
    hass.services.async_register(DOMAIN, SERVICE_DELETE_COMMAND, handle_delete_command, schema=SCHEMA_DELETE_COMMAND)
