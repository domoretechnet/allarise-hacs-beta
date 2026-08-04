"""Media URL resolution shared by the three doors into the `alert` command.

`notify.send_message`, `media_player.play_media` and the `allarise.trigger_alert`
service all hand the phone a URL, and all three used to resolve it slightly
differently — only some of them resolved `media-source://` ids, and none of them
survived a bad one. A raise here aborted the whole alert, so a mistyped media id
meant the user got a stack trace instead of the alert they asked for, without the
audio. Resolving in one place makes the three doors agree and makes failure
recoverable: the caller drops the offending field and sends the alert anyway.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.core import HomeAssistant

from .normalize import redact_url

_LOGGER = logging.getLogger(__name__)


async def async_resolve_media_url(
    hass: HomeAssistant,
    url: Any,
    target_media_player: str | None = None,
) -> str | None:
    """Resolve a media reference to a URL the phone can fetch.

    Handles `media-source://` ids, signs local Home Assistant paths so the phone
    can fetch them without a bearer token, and passes external URLs through.

    Returns ``None`` if the reference cannot be resolved — the caller is expected
    to log-and-continue without that field rather than abort the alert.
    """
    try:
        resolved = str(url)
        if media_source.is_media_source_id(resolved):
            play_item = await media_source.async_resolve_media(
                hass, resolved, target_media_player
            )
            resolved = play_item.url
        return async_process_play_media_url(hass, resolved)
    except Exception:  # noqa: BLE001 — any resolve failure must stay recoverable
        _LOGGER.warning(
            "Could not resolve media %s — sending the alert without it",
            redact_url(url),
            exc_info=True,
        )
        return None
