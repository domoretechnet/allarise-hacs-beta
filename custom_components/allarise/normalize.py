"""String normalisation for values crossing the Allarise boundary.

Two directions, two different problems.

**Outbound — Home Assistant to the phone.** A service field is free text typed
by a human, so it arrives carrying whatever the automation editor, a template,
or a paste from a web page left in it: a trailing space, a stray newline, a
zero-width joiner, occasionally a novel. Nothing in this module *rejects* a
value that used to be accepted — every function normalises and returns
something, per the backwards-compatibility rule. The fields the phone uses as a
lookup key (a command name, a station name, a sound name) get their ends
trimmed so ``" Lights"`` finds the command called ``"Lights"``; the app matches
tolerantly as well, so both the raw and the trimmed form resolve on any app
version, in either direction of version skew.

**Inbound — the phone to Home Assistant.** A value the app publishes becomes an
entity state, and Home Assistant refuses any state longer than 255 characters:
it raises ``InvalidStateError``, logs a traceback and parks the entity on
``unknown``. Two of ours blow past that as a matter of routine — an after-alarm
note built up over several ``append_notes`` calls, and the JSON list of
favourited radio stations, which passes 255 characters at roughly six
favourites. :func:`truncate_state` caps what becomes the state; the sensor keeps
the full text in a ``full_value`` attribute so nothing is actually lost.

**Wire-shape normalisation.** A third group of helpers at the bottom of the file
turns what the *service UI* is comfortable producing into the exact shape the
*app* reads. The app is the published API and it is not negotiable: ``days`` is
read as ``[Int]`` and nothing else, an icon colour component is a 0.0–1.0
float. Home Assistant's selectors, and the automations people already wrote by
hand, produce neither. Rather than change what either side accepts, these
functions widen the middle — every historical shape in, one canonical shape
out. See :func:`normalize_days`, :func:`normalize_color_components` and
:func:`mission_difficulty_config`.

Everything here is pure and dependency-free so it can be reasoned about (and,
if a test harness ever lands in this repo, tested) without Home Assistant.
"""

from __future__ import annotations

import re
from typing import Any

# Home Assistant's hard cap on a state string — MAX_LENGTH_STATE_STATE in
# homeassistant/const.py. Exceeding it does not truncate, it fails the update.
MAX_STATE_LENGTH = 255

# Marker appended to a value that had to be shortened, so a dashboard shows
# that there is more rather than implying the text simply ends there.
TRUNCATION_SUFFIX = "…"

# C0 controls, DEL and the C1 block. Tab and newline are handled separately
# because they are legitimate inside a multi-line field and noise everywhere
# else. Also stripped: the zero-width and bidi-control characters, which are
# invisible in the UI but make two apparently identical names compare unequal.
_CONTROL_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\ufeff]"
)

# Any run of whitespace, for the single-line collapse.
_WHITESPACE_RUN_RE = re.compile(r"\s+")

# Fields whose value is genuinely multi-line. Newlines and tabs survive in
# these; everywhere else they become spaces.
MULTILINE_FIELDS = frozenset({"notes", "append_notes", "message"})

# Fields holding a URL. Trimmed and control-stripped, but never whitespace-
# collapsed — mangling the inside of a URL is worse than leaving it alone.
URL_FIELDS = frozenset(
    {"media_url", "image_url", "video_url", "link_url", "dismiss_app_uri", "snooze_app_uri"}
)

# Per-field length caps. Generous by design: the point is to stop an accident
# (a template that looped, a file pasted into a text box) from becoming an MQTT
# message the phone has to parse, not to police what anyone writes.
_FIELD_MAX_LENGTH: dict[str, int] = {
    "notes": 10000,
    "append_notes": 10000,
    "message": 2000,
    "title": 255,
}
_DEFAULT_MAX_LENGTH = 255


def clean_text(
    value: str,
    *,
    multiline: bool = False,
    collapse_whitespace: bool = True,
    max_length: int | None = _DEFAULT_MAX_LENGTH,
) -> str:
    """Normalise one free-text value.

    Strips control characters, normalises CR/CRLF line endings to LF, collapses
    whitespace runs (single-line values only), trims the ends, and caps the
    length. Letters, digits, punctuation and emoji are left exactly as written —
    an emoji in an alert title is a perfectly reasonable thing to send and this
    module has no business removing it.
    """
    if not isinstance(value, str):
        return value

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)

    if multiline:
        # Keep the line structure; only tidy each line's trailing whitespace,
        # which is what a copy-paste usually leaves behind.
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        text = text.strip()
    else:
        text = text.replace("\t", " ").replace("\n", " ")
        if collapse_whitespace:
            text = _WHITESPACE_RUN_RE.sub(" ", text)
        text = text.strip()

    if max_length is not None and len(text) > max_length:
        text = text[:max_length].rstrip()

    return text


def clean_field(key: str, value: Any) -> Any:
    """Normalise one service-call field according to what that field is for."""
    if isinstance(value, str):
        if key in URL_FIELDS:
            return clean_text(value, collapse_whitespace=False, max_length=2000)
        return clean_text(
            value,
            multiline=key in MULTILINE_FIELDS,
            max_length=_FIELD_MAX_LENGTH.get(key, _DEFAULT_MAX_LENGTH),
        )
    if isinstance(value, list):
        return [clean_field(key, item) for item in value]
    if isinstance(value, dict):
        # mission_config, a radio_station object, an element of `missions`.
        return {k: clean_field(k, v) for k, v in value.items()}
    return value


def clean_service_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalise every string in a service call's data before it is published.

    Applied to the payload as a whole rather than field by field so a key added
    to a schema later is covered without anyone having to remember this file.
    """
    return {key: clean_field(key, value) for key, value in data.items()}


def clean_state_payload(key: str, payload: str) -> str:
    """Normalise a value the app published, on its way to becoming a state.

    ``notes`` is the one multi-line state we publish — an after-alarm note is
    meant to have lines in it, and a template that splits on ``\\n`` would break
    if they were flattened here. Everything else is a single-line value where a
    stray newline or control character only ever renders as damage.

    Length is *not* capped here: the full text stays in the coordinator so the
    select entities can match against it and the sensor can expose it as an
    attribute. :func:`truncate_state` does the capping at the point where the
    string actually becomes a state.
    """
    return clean_text(payload, multiline=key == "notes", max_length=None)


def truncate_state(value: Any) -> Any:
    """Cap a value at Home Assistant's 255-character state limit.

    Truncating is strictly better than the alternative, which is not "the long
    value" but ``unknown`` plus a traceback in the log on every update.
    """
    if not isinstance(value, str) or len(value) <= MAX_STATE_LENGTH:
        return value
    return value[: MAX_STATE_LENGTH - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


def clean_options(names: list[str], *, ignore_case: bool = False) -> list[str]:
    """Clean, de-duplicate and drop blanks from a select entity's options.

    ``SelectEntity`` does not de-duplicate: two favourites that clean to the
    same name give the user two identical rows, only one of which the app can
    ever resolve. A blank option is worse — it renders as an empty row and
    selecting it is a silent no-op on the phone. Order is preserved; the caller
    decides whether to sort.

    ``ignore_case`` follows how the *app* resolves that kind of name, and the
    two dropdowns differ. A radio station is looked up with a case-insensitive
    compare, so "WDET" and "wdet" are the same favourite as far as
    ``radio_start`` is concerned and offering both is offering a choice that
    does not exist. A sleep sound resolves to a file name, where case is load-
    bearing — a bundled ``white_noise`` and an imported ``White_Noise`` are two
    different sounds, and folding them would make one of them disappear from
    the dropdown with no way to select it.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = clean_text(str(raw), max_length=MAX_STATE_LENGTH)
        if not name:
            continue
        key = name.casefold() if ignore_case else name
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


# ─── Wire-shape normalisation ─────────────────────────────────────────
#
# Everything below converts a value a human (or a Home Assistant selector) is
# willing to produce into the exact shape the app parses. None of it rejects a
# value: an unrecognised token is reported back to the caller so it can be
# logged, because a field that quietly does nothing is the failure mode this
# whole module exists to prevent.

# Sun=1 … Sat=7, matching the app's `recurringDays` and the Days Mapping table
# in the Inbound Commands Reference. Abbreviations are here because people type
# them, not because anything ever documented them.
WEEKDAY_NUMBERS: dict[str, int] = {
    "sunday": 1, "sun": 1, "su": 1,
    "monday": 2, "mon": 2, "mo": 2,
    "tuesday": 3, "tues": 3, "tue": 3, "tu": 3,
    "wednesday": 4, "weds": 4, "wed": 4, "we": 4,
    "thursday": 5, "thurs": 5, "thur": 5, "thu": 5, "th": 5,
    "friday": 6, "fri": 6, "fr": 6,
    "saturday": 7, "sat": 7, "sa": 7,
}

# Group words. A one-line convenience, and the difference between "weekdays"
# working and it being silently dropped.
WEEKDAY_GROUPS: dict[str, tuple[int, ...]] = {
    "weekday": (2, 3, 4, 5, 6),
    "weekdays": (2, 3, 4, 5, 6),
    "weekend": (1, 7),
    "weekends": (1, 7),
    "everyday": (1, 2, 3, 4, 5, 6, 7),
    "every_day": (1, 2, 3, 4, 5, 6, 7),
    "daily": (1, 2, 3, 4, 5, 6, 7),
    "all": (1, 2, 3, 4, 5, 6, 7),
}

# What splits a free-text day list. Commas are the documented form; the rest
# are what actually turns up.
_DAY_SPLIT_RE = re.compile(r"[,;/|]+|\s+")


def normalize_days(value: Any) -> tuple[list[int], list[str]]:
    """Turn any plausible "days" value into the ``[Int]`` the app reads.

    The app parses ``payload["days"]`` as ``[Int]`` and *only* as ``[Int]``
    (1 = Sunday … 7 = Saturday). Anything else — the comma-separated string the
    integration has always advertised, a list of names from the new weekday
    selector, a list of numeric strings out of a template — was accepted by the
    schema and then dropped on the floor by the phone with no error anywhere.
    This is the al-d44 failure repeated, so the fix is to accept all of it.

    Returns ``(days, unrecognised)``. ``days`` is sorted and de-duplicated; an
    empty list means a one-time alarm, which is exactly what the app does with
    an empty array. ``unrecognised`` carries every token that could not be read
    so the caller can log it — dropping it silently is the bug, not the fix.
    """
    if value is None:
        return [], []

    tokens: list[Any]
    if isinstance(value, str):
        tokens = [t for t in _DAY_SPLIT_RE.split(value.strip()) if t]
    elif isinstance(value, (list, tuple, set, frozenset)):
        tokens = list(value)
    else:
        tokens = [value]

    days: set[int] = set()
    unrecognised: list[str] = []

    for token in tokens:
        # bool is an int subclass; `days: true` is nonsense, not day 1.
        if isinstance(token, bool):
            unrecognised.append(str(token))
            continue
        if isinstance(token, (int, float)):
            number = int(token)
            if 1 <= number <= 7:
                days.add(number)
            else:
                unrecognised.append(str(token))
            continue
        if isinstance(token, str):
            text = token.strip().strip("\"'").lower().replace(" ", "_")
            if not text:
                continue
            if text.lstrip("+-").isdigit():
                number = int(text)
                if 1 <= number <= 7:
                    days.add(number)
                else:
                    unrecognised.append(token)
                continue
            if text in WEEKDAY_GROUPS:
                days.update(WEEKDAY_GROUPS[text])
                continue
            if text in WEEKDAY_NUMBERS:
                days.add(WEEKDAY_NUMBERS[text])
                continue
            unrecognised.append(token)
            continue
        unrecognised.append(str(token))

    return sorted(days), unrecognised


# The three icon-colour components, in payload order.
COLOR_COMPONENT_KEYS: tuple[str, str, str] = ("color_r", "color_g", "color_b")


def normalize_color_components(values: dict[str, Any]) -> dict[str, float]:
    """Normalise ``color_r``/``color_g``/``color_b`` to the app's 0.0–1.0 floats.

    The app clamps each component to 0.0–1.0, so the 0–255 integers this
    integration's own UI offered were flattened to white (255 → 1.0) or black
    (any component below 1 → ~0). Both scales now work, chosen per call rather
    than per component: if **any** supplied component is greater than 1 the
    whole triple is read as 0–255, otherwise as 0.0–1.0. Judging each component
    on its own would read ``(255, 0, 0)`` as "full red, and green/blue in the
    0–1 scale", which is the same answer only by luck.

    ``(1, 0, 0)`` stays 0–1 — that is the documented scale and the reading that
    keeps every automation written against the reference working.
    """
    supplied: dict[str, float] = {}
    for key in COLOR_COMPONENT_KEYS:
        if key in values and values[key] is not None:
            try:
                supplied[key] = float(values[key])
            except (TypeError, ValueError):
                continue
    if not supplied:
        return {}
    scale = 255.0 if any(v > 1.0 for v in supplied.values()) else 1.0
    return {
        key: round(min(max(value / scale, 0.0), 1.0), 4)
        for key, value in supplied.items()
    }


def rgb_list_to_components(value: Any) -> dict[str, float]:
    """Turn Home Assistant's ``color_rgb`` selector output into app components.

    The selector hands back ``[r, g, b]`` as 0–255 integers. Anything else that
    looks like three numbers is accepted too and put through the same scale
    detection as :func:`normalize_color_components`, so a hand-written
    ``[0.2, 0.78, 0.35]`` is not misread as near-black.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return {}
    return normalize_color_components(dict(zip(COLOR_COMPONENT_KEYS, value)))


# Where a difficulty lands inside `mission_config`, per mission type. Every
# mission names its own key — there is no shared "difficulty" field — which is
# why a single dropdown needs this table rather than a passthrough.
MISSION_DIFFICULTY_KEYS: dict[str, str] = {
    "math": "math_difficulty",
    "balance_ball": "balance_difficulty",
    "block_drop": "block_drop_difficulty",
    "meteor": "meteor_difficulty",
    "shake": "shake_intensity",
}

# Shake has no difficulty; it has an intensity, on a three-value scale of its
# own. "super_easy" has nowhere lower to go than "light", which is also where
# "easy" lands — the alternative is dropping the value, and a difficulty that
# is one notch off is better than a difficulty that silently did nothing.
SHAKE_INTENSITY_BY_DIFFICULTY: dict[str, str] = {
    "super_easy": "light",
    "easy": "light",
    "medium": "medium",
    "hard": "vigorous",
}

# Only Math has a "super easy" tier. The others start at easy, so that is where
# a super_easy request is clamped.
_DIFFICULTY_CLAMP: dict[str, str] = {"super_easy": "easy"}


def normalize_mission_type(value: Any) -> str:
    """Fold a mission name to the payload form (``balance_ball``, ``meteor``…)."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def mission_difficulty_config(mission: Any, difficulty: Any) -> dict[str, str]:
    """Map a single difficulty choice onto the mission's own config key.

    Returns ``{}`` when the mission type has no difficulty at all (Tap and the
    Home Assistant mission, whose difficulty lives on its *fallback* and cannot
    be inferred from one dropdown) or when either value is unusable. An empty
    result is the caller's cue to log — never to publish something the app will
    ignore.
    """
    mission_key = normalize_mission_type(mission)
    level = normalize_mission_type(difficulty)
    if not mission_key or not level:
        return {}

    config_key = MISSION_DIFFICULTY_KEYS.get(mission_key)
    if config_key is None:
        return {}

    if mission_key == "shake":
        intensity = SHAKE_INTENSITY_BY_DIFFICULTY.get(level)
        # An intensity typed straight in ("vigorous") is passed through, so the
        # custom_value box on the dropdown is not a trap.
        if intensity is None:
            intensity = level if level in {"light", "medium", "vigorous"} else None
        return {config_key: intensity} if intensity else {}

    if mission_key != "math":
        level = _DIFFICULTY_CLAMP.get(level, level)
    return {config_key: level}
