"""Config flow for Allarise Alarm integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_DEVICE_NAME,
    CONF_TOPIC_PREFIX,
    DEFAULT_DEVICE_NAME,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
)
from .coordinator import AllariseCoordinator

# Anything the MQTT layer cannot carry in a topic segment. A control character
# is silently mangled rather than rejected by most brokers.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _validate_input(device_name: str, topic_prefix: str) -> dict[str, str]:
    """Return per-field form errors for the values the user typed.

    Both of these are load-bearing for every topic this integration touches, and
    both fail *silently* when they are wrong: a name that sanitizes to nothing
    yields ``allarise//sensor/#`` and a prefix carrying a wildcard subscribes to
    something the app never publishes. The integration loads, no exception is
    raised, and no data ever arrives. Catching it on the form is the only place
    the user is looking.
    """
    errors: dict[str, str] = {}

    # The coordinator sanitizes the name into a topic segment; if that leaves
    # nothing, every topic collapses to an empty segment.
    if not AllariseCoordinator.sanitize_device_name(device_name):
        errors[CONF_DEVICE_NAME] = "invalid_device_name"

    prefix = topic_prefix or ""
    if (
        not prefix.strip()
        or "#" in prefix
        or "+" in prefix
        or prefix.startswith("/")
        or prefix.endswith("/")
        or _CONTROL_CHARS.search(prefix)
    ):
        errors[CONF_TOPIC_PREFIX] = "invalid_topic_prefix"

    return errors


class AllariseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Allarise Alarm."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect device name and topic prefix."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_name = user_input[CONF_DEVICE_NAME]
            topic_prefix = user_input.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)

            errors = _validate_input(device_name, topic_prefix)

            if not errors:
                # Check for duplicate device names. New entries key on the
                # SANITIZED name — that is the form the topics actually use, so
                # "Bryan's iPhone" and "bryans-iphone" are the same device and
                # must collide. Entries created before this change keep their
                # raw unique_id untouched; nothing here migrates them.
                sanitized = AllariseCoordinator.sanitize_device_name(device_name)
                await self.async_set_unique_id(f"allarise_{sanitized}")
                self._abort_if_unique_id_configured()

            # Validate MQTT is available
            if not errors and not self.hass.config_entries.async_entries("mqtt"):
                errors["base"] = "mqtt_not_configured"

            if not errors:
                return self.async_create_entry(
                    title=f"Allarise - {device_name}",
                    data={
                        CONF_DEVICE_NAME: device_name,
                        CONF_TOPIC_PREFIX: topic_prefix,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME
                    ): str,
                    vol.Optional(
                        CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "docs_url": "https://allarise.app/docs/hass-api"
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration — allow changing device name and topic prefix."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            device_name = user_input[CONF_DEVICE_NAME]
            topic_prefix = user_input.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)

            errors = _validate_input(device_name, topic_prefix)

            if not errors:
                # Update unique_id; abort if a different entry already owns the
                # new name. Deliberately still the RAW name here: this entry
                # already exists, and rewriting its unique_id to the sanitized
                # form on an unrelated reconfigure is a silent identity change.
                # Only newly created entries use the sanitized form.
                await self.async_set_unique_id(f"allarise_{device_name}")
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_DEVICE_NAME: device_name,
                        CONF_TOPIC_PREFIX: topic_prefix,
                    }
                )

                return self.async_update_reload_and_abort(
                    entry,
                    title=f"Allarise - {device_name}",
                    data={
                        CONF_DEVICE_NAME: device_name,
                        CONF_TOPIC_PREFIX: topic_prefix,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_NAME,
                        default=entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                    ): str,
                    vol.Optional(
                        CONF_TOPIC_PREFIX,
                        default=entry.data.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX),
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "docs_url": "https://allarise.app/docs/hass-api"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return AllariseOptionsFlow(config_entry)


class AllariseOptionsFlow(OptionsFlow):
    """Handle options for Allarise — info, zone management."""

    def __init__(self, config_entry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show integration info and optionally navigate to zone management."""
        if user_input is not None:
            if user_input.get("manage_zones"):
                return await self.async_step_manage_zones()
            return self.async_create_entry(data={})

        device_name = self._config_entry.data.get(CONF_DEVICE_NAME, "Unknown")
        topic_prefix = self._config_entry.data.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)
        sanitized = AllariseCoordinator.sanitize_device_name(device_name)

        coordinator: AllariseCoordinator = self._config_entry.runtime_data
        zone_count = len(coordinator.known_zones)
        zones_summary = (
            ", ".join(
                f"`{z}`" for z in sorted(coordinator.known_zones)
            )
            if zone_count
            else "_None yet_"
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("manage_zones", default=False): bool,
                }
            ),
            description_placeholders={
                "device_name": device_name,
                "topic_prefix": topic_prefix,
                "mqtt_topic": f"{topic_prefix}/{sanitized}/#",
                "arm_topic": f"{topic_prefix}/alarm/+/state",
                "zones_summary": zones_summary,
            },
        )

    async def async_step_manage_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage alarm zones — select zones to remove."""
        coordinator: AllariseCoordinator = self._config_entry.runtime_data
        known_zones = sorted(coordinator.known_zones)

        if user_input is not None:
            zones_to_remove = user_input.get("zones_to_remove", [])
            for zone_slug in zones_to_remove:
                await coordinator.async_remove_zone(zone_slug)
            return self.async_create_entry(data={})

        if not known_zones:
            return self.async_show_form(
                step_id="manage_zones",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "zones_note": "No zones have been discovered yet. Zones appear automatically when a phone arms or disarms for the first time.",
                },
            )

        zone_choices = {z: z.replace("_", " ").title() for z in known_zones}

        return self.async_show_form(
            step_id="manage_zones",
            data_schema=vol.Schema(
                {
                    vol.Optional("zones_to_remove", default=[]): cv.multi_select(
                        zone_choices
                    ),
                }
            ),
            description_placeholders={
                "zones_note": "Select zones to remove. Only remove zones that **no phone is actively using** — an active phone will recreate the zone on its next arm or disarm.",
            },
        )
