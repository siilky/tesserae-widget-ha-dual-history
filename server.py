"""One or two current Home Assistant values plus the primary entity's history."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from flask import current_app


_EM_DASH = "—"
_UNAVAILABLE = {"", "none", "unknown", "unavailable"}
_SEMANTIC_LABELS: dict[str, str] = {
    "temperature": "Temperature",
    "humidity": "Humidity",
    "moisture": "Moisture",
    "battery": "Battery",
    "current": "Current",
    "energy": "Energy",
    "power": "Power",
    "voltage": "Voltage",
    "pressure": "Pressure",
    "atmospheric_pressure": "Atmospheric pressure",
    "precipitation": "Precipitation",
    "precipitation_intensity": "Precipitation intensity",
    "carbon_dioxide": "CO₂",
    "pm1": "PM1",
    "pm25": "PM2.5",
    "pm10": "PM10",
    "signal_strength": "Signal",
    "wind_speed": "Wind speed",
}
_AUTO_ICONS = {
    "temperature": "thermometer",
    "humidity": "drop",
    "moisture": "drop",
    "precipitation": "drop",
    "precipitation_intensity": "drop",
    "battery": "battery-medium",
    "current": "lightning",
    "energy": "lightning",
    "power": "lightning",
    "voltage": "lightning",
    "gas": "flame",
    "illuminance": "sun",
    "wind_speed": "wind",
    "signal_strength": "wifi-high",
    "carbon_dioxide": "cloud",
    "carbon_monoxide": "cloud",
    "pm1": "cloud",
    "pm25": "cloud",
    "pm10": "cloud",
    "volatile_organic_compounds": "cloud",
}
_ICON_OPTIONS = {
    "auto",
    "none",
    "thermometer",
    "drop",
    "battery-medium",
    "lightning",
    "gauge",
    "house",
    "chart-line",
    "wind",
    "sun",
    "cloud",
    "wifi-high",
    "flame",
}
_MAX_HISTORY_POINTS = 80
_WINDOW_HOURS = {6, 12, 24, 48, 72, 168}
# The number-pattern vocabulary of the bundled ha_sensor and ha_history widgets.
_NUMBER_FORMAT = re.compile(r"0(?:\.(0+))?")


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices()
    return []


def _finite_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _epoch_ms(raw: Any) -> int | None:
    """A HA timestamp as epoch milliseconds; a naive one is read as UTC."""
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


def _select_int(raw: Any, allowed: set[int], default: int) -> int:
    try:
        value = int(str(raw))
    except ValueError:
        return default
    return value if value in allowed else default


def _current_item(
    core: Any,
    entity_id: str,
    state: dict[str, Any] | None,
    *,
    label_override: Any,
    icon_override: Any,
    number_format: Any,
) -> dict[str, Any]:
    attrs = (state or {}).get("attributes") or {}
    friendly = core.friendly_name(state) if state is not None else entity_id
    friendly = str(friendly or entity_id)
    device_class = str(attrs.get("device_class") or "")
    raw_state = ""
    if state is not None and state.get("state") is not None:
        raw_state = str(state.get("state"))
    if state is None or raw_state.strip().lower() in _UNAVAILABLE:
        value = _EM_DASH
    else:
        numeric_value = _finite_float(raw_state)
        pattern = _NUMBER_FORMAT.fullmatch(str(number_format or "").strip())
        decimals = len(pattern.group(1) or "") if pattern else 1
        value = raw_state if numeric_value is None else f"{numeric_value:.{decimals}f}"

    explicit_label = str(label_override or "").strip()
    semantic = _SEMANTIC_LABELS.get(device_class)
    if explicit_label:
        label, label_key = explicit_label, ""
    elif semantic is not None:
        label, label_key = semantic, f"label.{device_class}"
    else:
        label, label_key = friendly, ""

    requested_icon = str(icon_override or "auto").strip()
    if requested_icon not in _ICON_OPTIONS:
        requested_icon = "auto"
    icon = (
        _AUTO_ICONS.get(device_class, "gauge")
        if requested_icon == "auto"
        else requested_icon
    )
    return {
        "entity_name": friendly,
        "value": value,
        "unit": str(attrs.get("unit_of_measurement") or ""),
        "label": label,
        "label_key": label_key,
        "icon": icon,
    }


def _history_series(samples: list[dict[str, Any]]) -> tuple[list[float], list[int]]:
    # A sample is plotted only when both its value and its time parse, so the
    # client can place every point it receives on a real time axis.
    pairs: list[tuple[float, int]] = []
    for sample in samples:
        value = _finite_float(sample.get("state"))
        timestamp = _epoch_ms(sample.get("last_changed") or sample.get("last_updated"))
        if value is None or timestamp is None:
            continue
        pairs.append((value, timestamp))
    if len(pairs) < 2:
        return [], []
    if len(pairs) > _MAX_HISTORY_POINTS:
        step = (len(pairs) - 1) / (_MAX_HISTORY_POINTS - 1)
        pairs = [pairs[round(index * step)] for index in range(_MAX_HISTORY_POINTS)]
    return [value for value, _ in pairs], [timestamp for _, timestamp in pairs]


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    primary_id = str(options.get("primary_entity") or "").strip()
    secondary_id = str(options.get("secondary_entity") or "").strip()
    # Every state shows the title bar; without sensor data only the user's own
    # title is known, and the client supplies a default when it is blank.
    title = str(options.get("title") or "").strip()
    if not primary_id:
        return {"setup": True, "title": title}

    core = _core()
    if core is None:
        # The host recognises a failed fetch only by a non-empty "error".
        return {
            "error_key": "error.missing_core",
            "error": "ha_core plugin is not installed",
            "title": title,
        }
    try:
        states = core.get_states()
    except Exception as error:
        return {
            "error_key": "error.fetch",
            "error": core.coerce_error(error),
            "title": title,
        }

    by_id = {str(state.get("entity_id") or ""): state for state in states}
    primary = _current_item(
        core,
        primary_id,
        by_id.get(primary_id),
        label_override=options.get("primary_label"),
        icon_override=options.get("primary_icon"),
        number_format=options.get("primary_format"),
    )
    # A configured entity always yields a value (an em dash when unavailable),
    # so an empty secondary value tells the client that no secondary is set.
    secondary = (
        _current_item(
            core,
            secondary_id,
            by_id.get(secondary_id),
            label_override=options.get("secondary_label"),
            icon_override=options.get("secondary_icon"),
            number_format=options.get("secondary_format"),
        )
        if secondary_id
        else dict.fromkeys(("value", "unit", "label", "label_key", "icon"), "")
    )
    title = title or primary["entity_name"]
    position = "left" if options.get("values_position") == "left" else "right"
    minimum_y_span = _finite_float(options.get("minimum_y_span"))
    if minimum_y_span is None or minimum_y_span <= 0:
        minimum_y_span = 5.0
    hours = _select_int(options.get("window"), _WINDOW_HOURS, 24)
    # Older cells stored the width as a select string; float() reads both.
    chart_width = _finite_float(options.get("chart_width"))
    chart_width = 65 if chart_width is None else min(max(round(chart_width), 40), 80)

    output: dict[str, Any] = {
        "title": title,
        "values_position": position,
        "chart_width": chart_width,
        "minimum_y_span": minimum_y_span,
        "history_hours": hours,
        "history_values": [],
        "history_times": [],
        "chart_error_key": "",
        "chart_error": "",
    }
    # Flatten the two identical item contracts so Studio can mine/bind the
    # useful top-level values and the client has no fallback policy of its own.
    for prefix, item in (("primary", primary), ("secondary", secondary)):
        for key in ("value", "unit", "label", "label_key", "icon"):
            output[f"{prefix}_{key}"] = item[key]
    # History is a separate failure domain: an HA history timeout must not
    # erase two current states that were already fetched successfully.
    try:
        samples = core.history(primary_id, hours=hours)
        output["history_values"], output["history_times"] = _history_series(samples)
    except Exception as error:
        output["chart_error_key"] = "chart.history_error"
        output["chart_error"] = core.coerce_error(error)
    return output
