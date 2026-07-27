"""Constants for the Allarise Alarm integration."""

DOMAIN = "allarise"
PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "media_player", "number", "select", "notify"]

# Config keys
CONF_DEVICE_NAME = "device_name"
CONF_TOPIC_PREFIX = "topic_prefix"

DEFAULT_DEVICE_NAME = "iPhone"
DEFAULT_TOPIC_PREFIX = "allarise"

# ─── MQTT topic templates ────────────────────────────────────────────
# {prefix} = user-configured topic prefix (default "allarise")
# {device}  = sanitised device name (lowercase, no special chars)

# Availability
TOPIC_AVAILABILITY = "{prefix}/{device}/availability"

# Dashboard sensor state  →  {prefix}/{device}/sensor/{key}
TOPIC_SENSOR = "{prefix}/{device}/sensor/{key}"

# Dashboard button availability  →  {prefix}/{device}/dashboard/{key}
TOPIC_DASHBOARD_AVAIL = "{prefix}/{device}/dashboard/{key}"

# Per-alarm sensor state  →  {prefix}/{device}/alarm/{index}/{key}
TOPIC_ALARM_SENSOR = "{prefix}/{device}/alarm/{index}/{key}"

# Per-alarm availability
TOPIC_ALARM_AVAILABILITY = "{prefix}/{device}/alarm/{index}/availability"

# Per-alarm button availability  →  {prefix}/{device}/alarm/{index}/{key}
TOPIC_ALARM_BUTTON_AVAIL = "{prefix}/{device}/alarm/{index}/{key}"

# ─── Command topics (integration publishes TO these) ─────────────────
TOPIC_COMMAND = "{prefix}/{device}/command/{cmd}"
TOPIC_ALARM_COMMAND = "{prefix}/{device}/alarm/{index}/command/{cmd}"

# ─── Wildcard subscription topics (coordinator subscribes) ───────────
SUB_SENSOR_WILDCARD = "{prefix}/{device}/sensor/#"
SUB_DASHBOARD_WILDCARD = "{prefix}/{device}/dashboard/#"
SUB_ALARM_WILDCARD = "{prefix}/{device}/alarm/+/#"
SUB_AVAILABILITY = "{prefix}/{device}/availability"
# Zone arm state — {prefix}/alarm/{zone}/state; wildcard discovers all zones automatically.
# {zone} = armZoneSlug from the iOS app (e.g. "home", "garage").
# No {device} — all phones sharing a zone name share this topic.
SUB_ARM_STATE_WILDCARD = "{prefix}/alarm/+/state"
SUB_ARM_COMMAND_WILDCARD = "{prefix}/alarm/+/set"
# Command status — app publishes {name}/status = fired|idle for each fired command
SUB_COMMAND_STATUS_WILDCARD = "{prefix}/{device}/command/+/status"

# Home Assistant birth topic
TOPIC_HA_STATUS = "homeassistant/status"

# Dashboard sensor definitions grouped by type: (key, name_suffix, icon, default_value)
# All "Alarm *" sensors are queue-based: they reflect the ringing alarm when active,
# or the next upcoming alarm when idle.
DASHBOARD_SENSORS = [
    # ── Alarm State ────────────────────────────────────────────────────
    ("alarm_state", "Alarm State", "mdi:alarm-check", "idle"),
    ("active_alarm_name", "Alarm Name", "mdi:label", "None"),
    ("active_alarm_mission", "Alarm Mission", "mdi:target", "None"),
    ("active_alarm_fire_time", "Alarm Fire Time", "mdi:calendar-clock", "None"),
    ("active_alarm_snooze_fire_time", "Alarm Snooze Fire Time", "mdi:clock-alert", "None"),
    # ── Alarm Sound & Hardware ─────────────────────────────────────────
    ("alarm_sound", "Alarm Sound", "mdi:music-note", "Unknown"),
    ("alarm_volume", "Alarm Volume", "mdi:volume-high", "0%"),
    ("alarm_vibrate", "Alarm Vibrate", "mdi:vibrate", "off"),
    ("alarm_fade_in", "Alarm Fade In", "mdi:sunrise", "Off"),
    # ── Alarm Details ──────────────────────────────────────────────────
    # REMOVED in 3.0: "alarm_notes" and "active_alarm_widget_command_1..4".
    # The app's V1 alarm-screen notes widget is gone, so it stopped publishing
    # those topics — the entities did not go unavailable, they froze on their
    # last pre-upgrade value and kept reporting it as current. Per-alarm notes
    # and command buttons are still exposed, on alarm/<n>/notes and
    # alarm/<n>/commands, and now describe the after-alarm Notes page.
    ("active_alarm_index", "Alarm ID", "mdi:identifier", "-1"),
    # ── Snooze ─────────────────────────────────────────────────────────
    ("snooze_count", "Alarm Snooze Count", "mdi:sleep", "0"),
    ("snoozes_remaining", "Snoozes Remaining", "mdi:sleep", "0"),
    # ── Alarm Counts ───────────────────────────────────────────────────
    ("enabled_alarm_count", "Alarm Count", "mdi:alarm-multiple", "0"),
    # ── App & Connection ───────────────────────────────────────────────
    ("broker_connection", "Broker Connection", "mdi:server-network", "Unknown"),
    ("app_version", "App Version", "mdi:cellphone-arrow-down", "Unknown"),
    ("media_alert_volume", "Alert Volume", "mdi:volume-medium", "75"),
    # ── Alert Configuration ────────────────────────────────────────────
    ("alert_sound", "Alert Sound", "mdi:bell-alert", "Default"),
    ("alert_vibrate", "Alert Vibrate", "mdi:vibrate", "on"),
    ("alert_loop_media", "Alert Loop Media", "mdi:repeat", "off"),
    ("alert_loop_delay", "Alert Loop Delay", "mdi:timer-outline", "0"),
    # ── Quick Alarm ────────────────────────────────────────────────────
    ("quick_alarm", "Quick Alarm", "mdi:timer-alert", "none"),
    ("quick_alarm_fire_time", "Quick Alarm Fire Time", "mdi:timer-sand", "none"),
    ("quick_alarm_label", "Quick Alarm Label", "mdi:label", "none"),
    ("quick_alarm_count", "Quick Alarm Count", "mdi:counter", "0"),
    # ── Sleep Sounds ───────────────────────────────────────────────────
    # The currently-playing sound name, mirroring the radio pair below
    # (radio_state / radio_station). The Sleep Sound select already read this
    # key via get_dashboard_state("sleep_sound"); it just had no sensor row, so
    # the value was invisible on the device page while its radio twin was not.
    # "none" when nothing is playing, which is exactly what the app publishes.
    ("sleep_sound", "Sleep Sound", "mdi:music-note", "none"),
    ("sleep_sound_volume", "Sleep Volume", "mdi:volume-high", "0"),
    # ── Radio ──────────────────────────────────────────────────────────
    # The app publishes these on every transport transition, and the
    # favourites list once on connect so a dashboard can build a dropdown
    # instead of hardcoding station names.
    ("radio_state", "Radio State", "mdi:radio", "stopped"),
    ("radio_station", "Radio Station", "mdi:radio-tower", "none"),
    ("radio_stations_available", "Radio Stations Available", "mdi:playlist-music", "[]"),
    # ── System Volume ──────────────────────────────────────────────────
    ("system_volume", "System Volume", "mdi:volume-high", "Unknown"),
    # ── Arm Widget Commands ────────────────────────────────────────────
    ("arm_custom_command", "Last Command Fired", "mdi:console", "None"),
]

# Per-alarm sensor definitions: (key, name_suffix, icon, default_value)
PER_ALARM_SENSORS = [
    ("name", "Name", "mdi:label", "Unknown"),
    ("enabled", "Enabled", "mdi:alarm-check", "off"),
    ("state", "State", "mdi:bell-ring", "idle"),
    ("fire_time", "Fire Time", "mdi:calendar-clock", "None"),
    ("snooze_fire_time", "Snooze Fire Time", "mdi:clock-alert", "None"),
    ("days", "Days", "mdi:calendar-week", "None"),
    # "mission" is the FIRST mission only, kept for backward compatibility.
    # "missions" is the full ordered sequence (comma-separated) and
    # "mission_count" its length — an alarm can require up to five missions, and
    # before 3.0 a three-mission alarm was indistinguishable from a one-mission
    # alarm in Home Assistant.
    ("mission", "Mission", "mdi:target", "None"),
    ("missions", "Missions", "mdi:format-list-numbered", "None"),
    ("mission_count", "Mission Count", "mdi:counter", "0"),
    # Tap-dismiss detail — only published when the first mission is "none" (Tap).
    ("tap_dismiss_mode", "Tap Dismiss Mode", "mdi:gesture-tap", "tap"),
    ("tap_count", "Tap Count", "mdi:gesture-tap-button", "1"),
    ("tap_hold_duration", "Tap Hold Duration", "mdi:timer-outline", "3"),
    ("sound", "Sound", "mdi:music-note", "Unknown"),
    ("snoozes", "Snoozes", "mdi:sleep", "0"),
    ("volume", "Volume", "mdi:volume-high", "0%"),
    ("vibrate", "Vibrate", "mdi:vibrate", "off"),
    ("fade_in", "Fade In", "mdi:sunrise", "Off"),
    # Notes text and command-button names for the after-alarm Notes page.
    # Both publish the literal string "None" when empty (they used to publish an
    # empty string), and "commands" is comma-separated with NO space after the
    # comma — templates that split on ", " need updating to split on ",".
    # Human-readable siblings of the timestamp sensors. Fire Time / Snooze Fire
    # Time are device_class timestamp so the Time trigger can target them, which
    # means Home Assistant renders and stores them as datetimes. These plain
    # strings keep the app's own short-format rendering available for dashboards
    # and notification templates.
    ("fire_time_display", "Fire Time (Display)", "mdi:calendar-clock", "None"),
    ("snooze_fire_time_display", "Snooze Fire Time (Display)", "mdi:clock-alert", "None"),
    ("notes", "Notes", "mdi:note-text", "None"),
    # Wake-up radio station for this alarm — the station NAME, or "None".
    ("radio_station", "Radio Station", "mdi:radio", "None"),
    ("sort_order", "Sort Order", "mdi:sort-numeric-ascending", "0"),
    ("commands", "Commands", "mdi:console", "None"),
    ("swipe_left_command", "Swipe Left Command", "mdi:gesture-swipe-left", "None"),
    ("swipe_right_command", "Swipe Right Command", "mdi:gesture-swipe-right", "None"),
    # After-alarm extras.
    ("morning_weather", "Morning Weather", "mdi:weather-partly-cloudy", "off"),
    ("dismiss_app_uri", "Dismiss App Link", "mdi:link-variant", "None"),
    ("snooze_app_uri", "Snooze App Link", "mdi:link-variant", "None"),
    # REMOVED in 3.0: widget_command_1..4 — the alarm-screen widget they
    # described no longer exists in the app. Use "commands" for the after-alarm
    # Notes page buttons.
]

# Dashboard button definitions: (key, name_suffix, icon)
DASHBOARD_BUTTONS = [
    ("dismiss", "Dismiss Alarm", "mdi:alarm-off"),
    ("snooze", "Snooze Alarm", "mdi:alarm-snooze"),
    ("skip", "Skip Alarm", "mdi:skip-next"),
    ("unskip", "Unskip Alarm", "mdi:skip-previous"),
    ("delete_next_alarm", "Delete Next Alarm", "mdi:delete-forever"),
    ("kill_snoozed", "Kill Snoozed Alarm", "mdi:alarm-off"),
    ("sleep_sound_stop", "Sleep Sound Stop", "mdi:stop"),
    ("sleep_sound_pause", "Sleep Sound Pause", "mdi:pause"),
    ("sleep_sound_resume", "Sleep Sound Resume", "mdi:play"),
    ("radio_stop", "Radio Stop", "mdi:stop"),
    ("radio_pause", "Radio Pause", "mdi:pause"),
    ("radio_resume", "Radio Resume", "mdi:play"),
]

# Quick alarm button definitions: (key, name_suffix, icon)
# Quick alarms are one-time ephemeral alarms — only dismiss (when ringing) and delete are relevant.
# The dismiss key stays "dismiss" — it is the dashboard command the app
# listens for — but the display name must not collide with the dashboard
# button of the same name, or the device page shows two "Dismiss Alarm" rows
# and HA suffixes one entity_id with "_2".
QUICK_ALARM_BUTTONS = [
    ("dismiss", "Dismiss Quick Alarm", "mdi:alarm-off"),
    ("delete_quick_alarm", "Delete Quick Alarm", "mdi:trash-can"),
]

# Per-alarm button definitions: (key, name_suffix, icon)
PER_ALARM_BUTTONS = [
    ("dismiss", "Dismiss", "mdi:alarm-off"),
    ("snooze", "Snooze", "mdi:alarm-snooze"),
    ("skip", "Skip", "mdi:skip-next"),
    ("unskip", "Unskip", "mdi:skip-previous"),
    ("kill_snoozed", "Kill Snoozed", "mdi:alarm-off"),
    ("delete", "Delete", "mdi:delete"),
]

# Text entity definitions: (key, name_suffix, icon)
# (currently empty — update_alarm removed)
TEXT_ENTITIES: list = []
