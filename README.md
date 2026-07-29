# Allarise Alarm — Home Assistant Integration (Beta)

> ## 🧪 This is the BETA integration
>
> It tracks the **TestFlight** build of the Allarise app, not the App Store
> release. Things here can and will change without notice.
>
> - App (TestFlight): <https://testflight.apple.com/join/DYTMR1v2>
> - Docs: <https://beta.allarise.app/home-assistant.html>
> - **Release integration:** <https://github.com/domoretechnet/allarise-hacs>
>
> ⚠️ This integration uses the same `allarise` domain as the release one, so
> HACS cannot install both. Remove the release integration before installing
> this, and remove this before going back.


[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Min Version](https://img.shields.io/badge/HA-2024.1%2B-blue.svg)](https://www.home-assistant.io)

Control and monitor the **Allarise Alarm** iOS app from Home Assistant. Dismiss alarms, snooze, send full-screen alerts to your phone, automate alarm schedules, and expose alarm state as sensors — all over MQTT.

📖 **[Full documentation, entity reference, services, and example automations →](https://beta.allarise.app/home-assistant.html)**

---

## ✅ Requirements

- The Allarise **TestFlight** build — join at <https://testflight.apple.com/join/DYTMR1v2>
- A running MQTT broker (e.g. Mosquitto via the HA add-on)
- The MQTT integration configured in Home Assistant
- The Allarise app connected to the same MQTT broker

## 📦 Installation

### Via HACS (recommended)

1. In Home Assistant, go to **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/domoretechnet/allarise-hacs-beta` and select **Integration** as the category
3. Find **Allarise Alarm** in the HACS store and click **Download**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/allarise/` into your HA `config/custom_components/` directory
2. Restart Home Assistant

## ⚡ Quick Setup

After installing, complete setup in four steps:

1. Create a dedicated MQTT user for the app in your Mosquitto broker
2. Connect the Allarise iOS app (**Settings → MQTT Settings**) to your broker
3. Add the **Allarise Alarm** integration in **Settings → Devices & Services** and match the Device Name and Topic Prefix to what you set in the app

> **Defaults:** Device Name = `iPhone` · Topic Prefix = `allarise`

**[Full setup guide with MQTT configuration, ACL setup, entity reference, services, and example automations →](https://beta.allarise.app/home-assistant.html)**

## ⬆️ Upgrading to 3.0

Version 3.0 matches the app release that moved alarm notes off the ringing alarm
screen and into the after-alarm flow, and that added multi-mission alarms.

**Entities removed.** These described the old alarm-screen notes widget, which no
longer exists. The app stopped publishing them, so they were not going
unavailable — they were frozen on their last pre-upgrade value and still
reporting it as current:

| Removed entity | Use instead |
| --- | --- |
| `sensor.<device>_alarm_notes` | per-alarm `notes` |
| `sensor.<device>_widget_command_1..4` (active alarm) | per-alarm `commands` |
| `sensor.<device>_alarm_<n>_widget_command_1..4` | per-alarm `commands` |

The app clears the retained values for these topics once, on its first connect
after updating, so they disappear from the broker even if you stay on 2.x.

**Fire times are now timestamp sensors.** `Fire Time` and `Snooze Fire Time`
(both dashboard and per-alarm) carry `device_class: timestamp`, so Home
Assistant's Time trigger can target them directly:

```yaml
triggers:
  - trigger: time
    at:
      entity_id: sensor.allarise_iphone_alarm_1_fire_time
      offset: "-00:05:00"   # negative = before the alarm
```

That replaces the old every-minute `time_pattern` + template-condition pattern
with one scheduled callback that re-arms itself whenever the alarm moves.
Previously this required hand-building a Template sensor helper with
*Device class: Timestamp*; that helper is no longer needed.

Two knock-on effects: these entities report `unknown` (not the string `none`)
when nothing is scheduled, and Home Assistant stores them as datetimes. New
`Fire Time (Display)` / `Snooze Fire Time (Display)` sensors carry the app's
short-format text for anything that wants the old-style string. The
`minutes_until` attribute is unchanged.

**Entities added:** `missions` and `mission_count` (the full mission sequence —
an alarm can require up to five), `tap_dismiss_mode` / `tap_count` /
`tap_hold_duration`, and `morning_weather`, `dismiss_app_uri`, `snooze_app_uri`.

**Two format changes to watch for in templates:**

- Per-alarm `commands` is comma-separated with **no space** after the comma.
  Split on `","`, not `", "`.
- Per-alarm `notes` publishes the literal `"None"` when empty (it used to
  publish an empty string).

**Service changes.** `update_alarm` gained `missions`, `mission_config`,
`append_notes`, `alarm_screen_commands`, `morning_weather`, `dismiss_app_uri`,
`snooze_app_uri`, and the swipe command fields. Note that `mission` (singular)
now sets the first mission **and clears the rest** — use `missions` to set a
multi-mission sequence.

## 📻 Radio (beta.3)

The app can now be driven as an internet-radio player from Home Assistant, and
each alarm can carry a wake-up station.

**New dashboard entities**

| Entity | Value |
| --- | --- |
| `sensor.<device>_radio_state` | `playing` / `paused` / `stopped` |
| `sensor.<device>_radio_station` | Current station name, or `none` |
| `sensor.<device>_radio_stations_available` | JSON array of the device's favourited stations |
| `button.<device>_radio_stop` / `_radio_pause` / `_radio_resume` | Transport controls |
| `select.<device>_radio_station` | Pick a favourited station to start, or `none` to stop |

**The Radio Station dropdown works exactly like the Sleep Sound one.** Its
options come from `radio_stations_available`, so favourites added or removed in
the app appear and disappear automatically. Picking a station publishes
`radio_start`; picking `none` publishes `radio_stop`. Before the app's first
publish arrives the dropdown offers only `none` — unlike sleep sounds there is
no bundled set to fall back on, and inventing station names would just fail to
resolve.

`radio_stations_available` is what lets a dashboard build a station dropdown
instead of hardcoding names — the same idea as `sleep_sounds_available`.

**New per-alarm entity:** `sensor.<device>_alarm_<n>_radio_station` — that
alarm's wake-up station, or `None`.

**Starting a favourited station** is a one-tap dropdown (above). Use MQTT
directly when you need the options the dropdown can't express — a sleep timer,
a volume, or an arbitrary stream URL:

```yaml
service: mqtt.publish
data:
  topic: "hawake/<device>/command/radio_start"
  payload: >
    {"station": "WDET", "until_next_alarm": true,
     "fade_out_minutes": 5, "ts": {{ now().timestamp() | int }}}
```

`station` takes a favourited station's name or UUID, or an object
`{"url": "https://…", "name": "…"}` for any stream. `radio_start` is ignored
while an alarm is ringing.

**Per-alarm station** is set with the `radio_station` field on the Update Alarm
service (or `create_alarm` over MQTT). Send `""` to clear it.

## ⚡ App Persistence (3.2.0)

App Persistence is the app's background keep-alive. With it **on** the app stays
resident: a radio alarm streams its actual station and MQTT keeps working while
the app is in your pocket. With it **off** iOS suspends the app — alarms still
ring on time, but a radio alarm plays its own tone instead of the station, and
nothing on that phone answers MQTT until you open it. Off saves battery.

| Entity | Value |
| --- | --- |
| `switch.<device>_app_persistence` | Toggle it from a dashboard |
| `sensor.<device>_app_persistence` | `on` / `off` — the app's current setting |

**How you tell whether it worked.** There is no reply to the command. The app
publishes its setting back on `sensor/app_persistence` (retained) on connect and
on *every* change — including a command that asked for the value it already had,
so an automation gets a definitive answer either way:

```yaml
- action: switch.turn_on
  target:
    entity_id: switch.bedroom_iphone_app_persistence
- wait_template: "{{ is_state('sensor.bedroom_iphone_app_persistence', 'on') }}"
  timeout: "00:00:15"
  continue_on_timeout: true
- condition: template
  value_template: "{{ not wait.completed }}"
# …the phone did not answer. Notify, or fall back.
```

**The switch shows `unavailable` when the app cannot answer** — either it is
offline, or it has never published the setting. That is the honest answer rather
than a stale value, and it is also the real limitation worth understanding:
turning persistence *off* is what causes iOS to suspend the app, so once it is
off there is nothing connected to receive the command that would turn it back
on. Switching it on again has to happen on the phone.

You can publish the command directly as well. `ON`/`OFF` and JSON both work:

```yaml
action: mqtt.publish
data:
  topic: "allarise/<device>/command/app_persistence"
  payload: "ON"
```

Requires the newer app. An older app never publishes the topic, so the switch
stays `unavailable` rather than showing a value it cannot back up.

## 🧭 Service fields that fill themselves in

The service fields in the automation editor now offer what is actually on your
phone instead of asking you to remember it:

| Field | Offers |
| --- | --- |
| **Device** (every service) | The Allarise devices configured here |
| **Sound** (`create_alarm`, `update_alarm`, `trigger_alert`) | The phone's own tones, bundled and imported |
| **Radio station** (`create_alarm`, `update_alarm`) | The phone's favourited stations |
| **Swipe left/right command**, **Delete command → Name** | Commands the phone has reported |
| **Notes page commands** | The same commands, as a multi-select (max 4) |
| **Delete alarm → Alarm ID** | The alarms on the phone, as `3 — Weekday Wake` |
| **Delete alarm → Name** | The alarm names on the phone |

Three things worth knowing:

- **Every one of these is still free text.** The list is a convenience — type
  any value and it is sent verbatim. Nothing that worked in YAML before stops
  working, and no automation is rewritten.
- **An empty list is normal**, not a fault: an older app publishes no sound
  inventory, and a phone that has not been seen since restart has reported
  nothing. Type the value instead.
- **A command appears once the phone has published its status topic**, which is
  the same moment it gets a Home Assistant sensor. A command created seconds
  ago may not be listed yet.

The lists are rebuilt as the phone reports new data. A Home Assistant page that
is *already open* keeps the version it loaded with — reload the automation
editor to pick up something added since.

**New: `verse_of_day`** on `create_alarm` and `update_alarm` adds or removes the
Verse of the Day card in the after-alarm sequence, and **After-alarm steps**
(`after_alarm_actions`) sets that sequence outright: `notes`, `verse`,
`weather`, `appLink`. Leaving both out changes nothing, exactly as before — the
steps are still worked out from the fields you did set. `verse_of_day` needs an
app from 2026-07-28 or later; an older app ignores it and still creates or
updates the alarm.

**New: an "Alarm ID" sensor** on every per-alarm device, holding the number
that `update_alarm` and `delete_alarm` target. It is filed under Diagnostic on
the device page. Before it existed the only ways to find that number were to
read it out of an entity ID or open the alarm's edit screen on the phone.

## 🔊 Sound: one channel, eight controls

**The Sleep Sound and Radio Station dropdowns drive the same audio channel on
the phone.** Starting a station stops a sleep sound, and starting a sleep sound
stops the station — there is one player, not two. The device page did not say
so, so the eight controls now share an **Audio:** prefix and read as one group:

| Entity | Does |
| --- | --- |
| `select.<device>_sleep_sound` | Audio: Sleep Sound — pick a sound to start, `none` to stop |
| `select.<device>_radio_station` | Audio: Radio Station — pick a favourite to start, `none` to stop |
| `button.<device>_sleep_sound_stop` / `_pause` / `_resume` | Audio: transport for the sleep sound |
| `button.<device>_radio_stop` / `_pause` / `_resume` | Audio: transport for the radio |

Only the display names changed. Entity IDs, unique IDs and the MQTT commands
behind them are untouched, so existing dashboards, scripts and automations keep
working exactly as they are.

Grouped on a dashboard:

```yaml
type: entities
title: Sound
entities:
  - entity: select.bedroom_iphone_sleep_sound
    name: Sleep sound
  - entity: select.bedroom_iphone_radio_station
    name: Radio station
  - type: buttons
    entities:
      - entity: button.bedroom_iphone_radio_resume
        name: Play
      - entity: button.bedroom_iphone_radio_pause
        name: Pause
      - entity: button.bedroom_iphone_radio_stop
        name: Stop
  - entity: sensor.bedroom_iphone_radio_state
    name: Now playing
  - entity: switch.bedroom_iphone_app_persistence
    name: App Persistence
footer:
  type: graph
  entity: sensor.bedroom_iphone_radio_state
```

**Radio playback needs App Persistence on.** Without it iOS suspends the app,
which stops the stream and means a radio alarm rings with its tone instead of
the station — which is why the persistence switch belongs on the same card.
Sleep sounds have their own keep-alive while playing and do not depend on it.

**"Notify — Unknown" on the Notifiers card is not a fault.** A notify entity's
state is the time it last sent something, so it reads `unknown` until the first
alert goes out and then shows a timestamp. The entity is now called **Send
Alert**, which is what it does — trigger a full-screen alert on the phone. The
entity ID did not change.

## 🛠️ Service fixes and new fields (beta.4)

**Three fields were being accepted and then silently ignored by the phone.** All
three now work, and every payload that worked before still works — the fix adds
the key the app reads next to the one you sent, it never replaces it.

| Field | What used to happen | What happens now |
| --- | --- | --- |
| `days` on `create_alarm` / `update_alarm` | Anything but a list of integers was dropped, so `days: "2,3,4,5,6"` quietly created a one-time alarm | Weekday names, numbers, a comma-separated string and group words like `weekdays` are all understood. Unrecognised days are logged instead of vanishing |
| `snooze_interval` / `snooze_limit` on `update_alarm` | Never read by the app | Published as `snooze_duration` / `max_snooze_count` as well, so they take effect |
| `fade_in` as a number | Read as an on/off toggle, so the minutes were lost | Also published as `fade_in_duration`. A boolean `fade_in` still means "flip the toggle, leave the duration alone" |

**`create_command` colours were coming out white.** The app takes 0.0–1.0
floats and this integration offered 0–255 integers, which the app clamped to
full brightness. Both scales are accepted now. The UI has a **Icon colour**
preset dropdown (all 36 preset names) and a **Custom icon colour** wheel;
`color_r` / `color_g` / `color_b` still work exactly as documented and are
still accepted in YAML. `show_in_list` is now exposed too.

**New fields.** `trigger_alert` gained `alert_id` (re-sending the same ID
replaces the waiting notification instead of stacking another one),
`play_sound`, and its own `fade_in` in **seconds**. `create_alarm` now shows
`snooze_mode`, `skip_mode`, the hold durations, the swipe commands, the app
links, the Notes page commands and `mqtt_id` in the visual editor;
`update_alarm` gained the snooze and skip mode fields.

**A Mission difficulty dropdown** replaces having to know that Math wants
`math_difficulty`, Bricks wants `block_drop_difficulty` and Shake wants
`shake_intensity`. Set **Mission** and **Mission difficulty** together and the
integration writes the right key. Anything you put in `mission_config`
yourself always wins.

**`missions` and `mission_config` are no longer shown in the visual editor** —
they are still fully supported in YAML and are the way to build a
multi-mission sequence or to configure a mission in detail. See the
[MQTT Payload Builder](https://beta.allarise.app/mqtt-builder.html).

## 🎯 Fallback missions (beta.3)

A Home Assistant mission's fallback — what runs when the broker is unreachable —
now accepts **Bricks** (`block_drop`) and **Meteor** (`meteor`) alongside
`shake`, `math`, `balance_ball` and `none`.

Every fallback type also takes the same settings its primary form does, passed
through `mission_config`:

```yaml
mission: home_assistant
mission_config:
  fallback_mission: block_drop
  fallback_block_drop_difficulty: hard
  fallback_block_drop_lines: 8
```

Tap-as-fallback previously had no settings at all and was always a single tap;
it now takes `fallback_tap_dismiss_mode`, `fallback_tap_count` and
`fallback_tap_hold_duration`.

## 🔗 Links

- [Allarise Beta (TestFlight)](https://testflight.apple.com/join/DYTMR1v2)
- [Allarise App](https://allarise.app)
- [Setup Guide & Documentation](https://beta.allarise.app/home-assistant.html)
- [MQTT Payload Builder](https://beta.allarise.app/mqtt-builder.html)
- [Report an issue](https://github.com/domoretechnet/allarise-hacs-beta/issues)
