"""Notify platform for Allarise Alarm integration.

Allows HA to call notify.send_message to trigger a full-screen alert
on the phone with a message and optional title/sound/media.
"""

from __future__ import annotations

import json
import logging

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AllariseCoordinator
from .media import async_resolve_media_url
from .normalize import clean_service_data, redact_url

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[AllariseCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Allarise notify entity."""
    coordinator = entry.runtime_data
    async_add_entities([AllariseNotify(coordinator)])


class AllariseNotify(CoordinatorEntity[AllariseCoordinator], NotifyEntity):
    """Notify entity for sending alerts to Allarise.

    Named "Send Alert" rather than "Notify": what it does is trigger a
    full-screen alert on the phone, and "Notify — Unknown" on the Notifiers
    card read like something was broken. It is not — a notify entity's state is
    the time it last sent, so it is `unknown` until the first alert goes out.
    Display only; the entity_id and unique_id are unchanged.
    """

    _attr_has_entity_name = True
    _attr_name = "Send Alert"
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: AllariseCoordinator) -> None:
        """Initialize the notify entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"allarise_{coordinator.device_name}_notify"

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
        """Return True if the app is online."""
        return self.coordinator.app_online

    async def async_send_message(self, message: str, title: str | None = None, **kwargs) -> None:
        """Send a notification — triggers a full-screen alert on the phone.

        Extra data keys supported by the iOS app:
        - sound: alert sound name
        - volume: 0-100 (defaults to the app's Media Alert Volume setting)
        - media_url: audio URL to play
        - image_url: image to display
        - video_url: video to display
        - link_url: URL to open
        """
        payload: dict = {"message": message}

        if title:
            payload["title"] = title

        # Pass through extra data
        data = kwargs.get("data") or {}
        for key in ("sound", "volume", "media_url", "image_url", "video_url", "link_url"):
            if key in data:
                payload[key] = data[key]

        # Default volume: use the app's configured Media Alert Volume if not specified.
        # An integer 0–100 is the canonical wire scale for `alert.volume` — it is
        # what the service schemas validate, what this sensor stores, and what
        # MQTTCommandHandler.normalizeVolume documents as coming from HACS. The
        # app still accepts a 0.0–1.0 float, so nothing already sending one breaks.
        if "volume" not in payload:
            vol_str = self.coordinator.get_dashboard_state("media_alert_volume")
            try:
                payload["volume"] = int(vol_str)
            except (ValueError, TypeError):
                pass  # Let the app fall back to its own default

        # Same normalisation the trigger_alert service applies — notify is the
        # other door into the identical `alert` command, and a message assembled
        # by a template is if anything MORE likely to arrive with trailing
        # newlines than one typed by hand. Done before the media resolve so a
        # URL with a stray newline still resolves.
        payload = clean_service_data(payload)

        # Resolve and sign every media reference so the phone can fetch
        # HA-hosted content (e.g. TTS proxy URLs) without a Bearer token.
        # image_url and video_url go through the same path as media_url —
        # media_player.play_media already treats all three alike, and a
        # media-source id is just as pasteable into one of them as the other.
        # A field that will not resolve is dropped, not fatal: the alert still
        # reaches the phone without it.
        for key in ("media_url", "image_url", "video_url"):
            if key in payload:
                resolved = await async_resolve_media_url(
                    self.hass, payload[key], self.entity_id
                )
                if resolved is None:
                    payload.pop(key)
                else:
                    payload[key] = resolved
                    # Redacted: the signed URL carries an authSig credential.
                    _LOGGER.debug("Resolved %s: %s", key, redact_url(resolved))

        await self.coordinator.async_publish_command(
            "alert", json.dumps(payload)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
