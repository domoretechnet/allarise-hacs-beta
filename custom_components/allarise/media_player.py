"""Media player platform for Allarise Alarm integration.

Allows HA to call media_player.play_media to trigger an alert on the phone
with audio playback. Supports PLAY_MEDIA, MEDIA_ANNOUNCE, and VOLUME_SET.

Volume can be set via media_player.volume_set (0.0–1.0) before calling
play_media. It will be included in the alert payload automatically.

The volume_level is synced with the iOS app's `mediaAlertVolume` setting:
- Setting it here publishes a `set_media_alert_volume` MQTT command to the app.
- The app publishes changes back via the `media_alert_volume` sensor topic.
"""

from __future__ import annotations

import json
import logging

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AllariseCoordinator
from .media import async_resolve_media_url
from .normalize import redact_url

_LOGGER = logging.getLogger(__name__)


def _volume_percent(value: float) -> int:
    """Convert a volume to the canonical wire scale for `alert.volume`.

    The two doors into the same app-side `alert` command disagreed: notify sent
    an integer 0–100 (what every service schema validates) while this entity sent
    a 0.0–1.0 float, so a value copied between them was off by 100×. An integer
    0–100 wins because it is the documented HACS shape on the app side
    (`MQTTCommandHandler.normalizeVolume`), and the app still accepts the float,
    so nothing that already sends one breaks.

    A value at or below 1.0 is read as a fraction, above it as a percentage —
    the same rule the app applies, so both input shapes keep working here too.
    The 1% floor is deliberate: the app reads a bare ``1`` as 100%, and an alarm
    app must never turn "almost silent" into "full volume".
    """
    number = float(value)
    percent = round(number * 100) if number <= 1.0 else round(number)
    percent = max(0, min(100, percent))
    return 2 if percent == 1 else percent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[AllariseCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Allarise media player."""
    coordinator = entry.runtime_data
    async_add_entities([AllariseMediaPlayer(coordinator)])


class AllariseMediaPlayer(CoordinatorEntity[AllariseCoordinator], MediaPlayerEntity):
    """Media player entity for sending audio alerts to Allarise."""

    _attr_has_entity_name = True
    _attr_name = "Alert Media"
    _attr_icon = "mdi:speaker-wireless"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator: AllariseCoordinator) -> None:
        """Initialize the media player."""
        super().__init__(coordinator)
        self._attr_unique_id = f"allarise_{coordinator.device_name}_media_player"
        # Initialize volume from coordinator sensor, fallback 0.75
        self._sync_volume_from_coordinator()

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
    def state(self) -> MediaPlayerState:
        """Return the state — idle when online, off when offline."""
        if self.coordinator.app_online:
            return MediaPlayerState.IDLE
        return MediaPlayerState.OFF

    @property
    def available(self) -> bool:
        """Return True if the app is online."""
        return self.coordinator.app_online

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume level (0.0–1.0).

        Publishes a `set_media_alert_volume` MQTT command to the iOS app,
        which updates the app's `mediaAlertVolume` setting.
        """
        self._attr_volume_level = volume
        self.async_write_ha_state()

        # Publish command to iOS app
        payload = json.dumps({"volume": volume})
        await self.coordinator.async_publish_command(
            "set_media_alert_volume", payload
        )

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Return a BrowseMedia instance for HA's media browser."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
        )

    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        **kwargs,
    ) -> None:
        """Play media — sends an alert command to the phone.

        The iOS app shows a full-screen alert. What the media becomes on that
        alert depends on `media_type`: images render as a still, videos as an
        inline player, and everything else (music, TTS, provider streams) plays
        as audio. Extra keys (title, message, sound, image_url, video_url) can
        be passed via `extra`.

        The volume is taken from the entity's current volume_level (set via
        media_player.volume_set) unless overridden in `extra`.

        Handles three kinds of media_id:
        1. media-source:// URIs — resolved via HA's media_source component
        2. Local HA paths (/api/tts_proxy/...) — signed so the phone can
           fetch them without a Bearer token
        3. External URLs — passed through unchanged
        """
        extra = kwargs.get("extra") or {}

        # Resolve media-source:// URIs, sign local HA URLs (adds the authSig
        # query param) and make relative paths absolute so the phone can reach
        # them. A reference that will not resolve is dropped below rather than
        # aborting the alert.
        resolved_media = await async_resolve_media_url(
            self.hass, media_id, self.entity_id
        )
        if resolved_media is not None:
            # Redacted: the signed URL carries an authSig credential.
            _LOGGER.debug("Resolved media URL: %s", redact_url(resolved_media))

        # Route the resolved URL by content type. Images and videos must reach
        # the phone as image_url / video_url so the alert card renders them
        # visually — the app's AlertContentView shows a still for image_url and
        # an inline player for video_url. Only audio (music, TTS, provider
        # streams) belongs in media_url, which the app plays through AVPlayer.
        # Anything not clearly image/video stays audio, so TTS and every
        # existing media_url automation behave exactly as before.
        media_type_str = str(media_type or "").lower()
        if media_type_str.startswith("image"):
            media_key = "image_url"
        elif media_type_str.startswith("video"):
            media_key = "video_url"
        else:
            media_key = "media_url"

        payload: dict = {
            "message": extra.get("message", "Media playback"),
        }
        if resolved_media is not None:
            payload[media_key] = resolved_media

        if "title" in extra:
            payload["title"] = extra["title"]
        if "sound" in extra:
            payload["sound"] = extra["sound"]
        # extra.image_url / extra.video_url let a caller attach a still or clip
        # alongside audio in a single call; kept for backwards compatibility.
        for key in ("image_url", "video_url"):
            if key in extra:
                resolved_extra = await async_resolve_media_url(
                    self.hass, extra[key], self.entity_id
                )
                if resolved_extra is not None:
                    payload[key] = resolved_extra

        # Volume: use explicit extra override, else the entity's volume_level.
        # Sent as an integer 0–100 — see _volume_percent for why that is the
        # canonical scale. Both 0.0–1.0 and 0–100 inputs are still accepted here.
        if "volume" in extra:
            payload["volume"] = _volume_percent(extra["volume"])
        elif self._attr_volume_level is not None:
            payload["volume"] = _volume_percent(self._attr_volume_level)

        await self.coordinator.async_publish_command(
            "alert", json.dumps(payload)
        )

    def _sync_volume_from_coordinator(self) -> None:
        """Sync volume_level from the coordinator's media_alert_volume sensor."""
        raw = self.coordinator.get_dashboard_state("media_alert_volume")
        if raw not in ("Unknown", ""):
            try:
                pct = int(raw)
                self._attr_volume_level = pct / 100.0
            except (ValueError, TypeError):
                self._attr_volume_level = 0.75
        else:
            self._attr_volume_level = 0.75

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._sync_volume_from_coordinator()
        self.async_write_ha_state()
