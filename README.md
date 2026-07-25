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

## 🔗 Links

- [Allarise Beta (TestFlight)](https://testflight.apple.com/join/DYTMR1v2)
- [Allarise App](https://allarise.app)
- [Setup Guide & Documentation](https://beta.allarise.app/home-assistant.html)
- [MQTT Payload Builder](https://beta.allarise.app/mqtt-builder.html)
- [Report an issue](https://github.com/domoretechnet/allarise-hacs-beta/issues)
