from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# server.py imports flask at module level. A standalone checkout may not have
# Flask; under the host's pytest it is real and must stay real for
# test_smoke.py. Either way each test swaps the module's own current_app.
try:
    import flask  # noqa: F401
except ImportError:
    sys.modules["flask"] = SimpleNamespace(current_app=None)
SPEC = importlib.util.spec_from_file_location("ha_dual_history_server", ROOT / "server.py")
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)
MANIFEST = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
OPTIONS = {option["name"]: option for option in MANIFEST["cell_options"]}

MIDNIGHT_MS = round(datetime(2026, 8, 31, tzinfo=UTC).timestamp() * 1000)
HOUR_MS = 3_600_000

STATES = [
    {
        "entity_id": "sensor.outdoor_temperature",
        "state": "7.86",
        "attributes": {
            "friendly_name": "Back Garden",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
        },
    },
    {
        "entity_id": "sensor.outdoor_humidity",
        "state": "82",
        "attributes": {
            "friendly_name": "Back Garden Humidity",
            "device_class": "humidity",
            "unit_of_measurement": "%",
        },
    },
]


class FakeCore:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = states
        self.states_calls = 0
        self.history_calls: list[tuple[str, int]] = []
        self.state_error: Exception | None = None
        self.history_error: Exception | None = None
        self.history_result: list[dict[str, Any]] = []

    def entity_choices(self) -> list[dict[str, str]]:
        return [{"value": "sensor.outdoor_temperature", "label": "Back Garden"}]

    def get_states(self) -> list[dict[str, Any]]:
        self.states_calls += 1
        if self.state_error is not None:
            raise self.state_error
        return self.states

    def history(self, entity_id: str, *, hours: int = 24) -> list[dict[str, Any]]:
        self.history_calls.append((entity_id, hours))
        if self.history_error is not None:
            raise self.history_error
        return self.history_result

    def friendly_name(self, state: dict[str, Any]) -> str:
        attrs = state.get("attributes") or {}
        return str(attrs.get("friendly_name") or state.get("entity_id") or "")

    def coerce_error(self, error: Exception) -> str:
        return f"safe:{error}"


class ServerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.core = FakeCore(copy.deepcopy(STATES))
        self.registry = {"ha_core": SimpleNamespace(server_module=self.core)}
        app = SimpleNamespace(config={"PLUGIN_REGISTRY": self.registry})
        patcher = mock.patch.object(SERVER, "current_app", app)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        return SERVER.fetch(options, {}, ctx={})

    def base_options(self) -> dict[str, Any]:
        return {
            "primary_entity": "sensor.outdoor_temperature",
            "secondary_entity": "sensor.outdoor_humidity",
        }

    def test_choices_delegate_only_the_entity_key(self) -> None:
        self.assertEqual(SERVER.choices("entity"), self.core.entity_choices())
        self.assertEqual(SERVER.choices("other"), [])

    def test_every_manifest_choice_source_is_served(self) -> None:
        sources = {
            option["choices_from"]
            for option in OPTIONS.values()
            if "choices_from" in option
        }
        self.assertTrue(sources)
        for source in sources:
            self.assertEqual(SERVER.choices(source), self.core.entity_choices())

    def test_payload_keys_match_the_manifest_sample(self) -> None:
        self.assertEqual(
            set(self.fetch(self.base_options())),
            set(MANIFEST["data_schema"]["sample"]),
        )

    def test_every_manifest_icon_choice_is_honoured(self) -> None:
        for name in ("primary_icon", "secondary_icon"):
            for choice in OPTIONS[name]["choices"]:
                if choice["value"] == "auto":
                    continue
                output = self.fetch(self.base_options() | {name: choice["value"]})
                self.assertEqual(output[name], choice["value"])

    def test_invalid_stored_values_fall_back_to_the_manifest_defaults(self) -> None:
        options = self.base_options() | {
            "values_position": "middle",
            "minimum_y_span": "invalid",
        }
        output = self.fetch(options)
        for name in ("values_position", "minimum_y_span"):
            self.assertEqual(output[name], OPTIONS[name]["default"])

    def test_semantic_labels_agree_with_the_english_catalog(self) -> None:
        catalog = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {f"label.{name}": text for name, text in SERVER._SEMANTIC_LABELS.items()},
            {key: text for key, text in catalog.items() if key.startswith("label.")},
        )

    def test_automatic_icons_are_offered_choices(self) -> None:
        offered = {choice["value"] for choice in OPTIONS["primary_icon"]["choices"]}
        self.assertLessEqual(set(SERVER._AUTO_ICONS.values()) | {"gauge"}, offered)
        self.assertEqual(SERVER._ICON_OPTIONS, offered)

    def test_missing_primary_entity_returns_setup_state(self) -> None:
        self.assertEqual(self.fetch({}), {"setup": True, "title": ""})
        only_secondary = {"secondary_entity": "sensor.outdoor_humidity"}
        self.assertEqual(self.fetch(only_secondary), {"setup": True, "title": ""})
        self.assertEqual(self.core.states_calls, 0)

    def test_secondary_entity_is_optional(self) -> None:
        output = self.fetch({"primary_entity": "sensor.outdoor_temperature"})
        self.assertEqual(set(output), set(MANIFEST["data_schema"]["sample"]))
        self.assertEqual(output["primary_value"], "7.9")
        for key in ("value", "unit", "label", "label_key", "icon"):
            self.assertEqual(output[f"secondary_{key}"], "", key)

    def test_window_accepts_offered_choices_only(self) -> None:
        output = self.fetch(self.base_options() | {"window": "168"})
        self.assertEqual(output["history_hours"], 168)
        self.assertEqual(self.core.history_calls[-1], ("sensor.outdoor_temperature", 168))
        for window in (None, "13", "week", ""):
            output = self.fetch(self.base_options() | {"window": window})
            self.assertEqual(output["history_hours"], int(OPTIONS["window"]["default"]))

    def test_chart_width_is_rounded_and_clamped_to_the_slider_range(self) -> None:
        # Strings are what cells saved while the option was a select.
        for width, expected in ((72, 72), ("55", 55), (63.6, 64), (10, 40), (95, 80)):
            output = self.fetch(self.base_options() | {"chart_width": width})
            self.assertEqual(output["chart_width"], expected, width)
        for width in (None, "", "wide", float("nan")):
            output = self.fetch(self.base_options() | {"chart_width": width})
            self.assertEqual(output["chart_width"], OPTIONS["chart_width"]["default"], width)

    def test_number_formats_apply_per_value(self) -> None:
        output = self.fetch(self.base_options() | {"primary_format": "0.00", "secondary_format": "0"})
        self.assertEqual(output["primary_value"], "7.86")
        self.assertEqual(output["secondary_value"], "82")
        for invalid in ("", " ", "#.#", "1.0", "0."):
            output = self.fetch(self.base_options() | {"primary_format": invalid})
            self.assertEqual(output["primary_value"], "7.9", invalid)
        self.assertEqual(OPTIONS["primary_format"]["default"], "0.0")

    def test_missing_core_returns_localizable_error(self) -> None:
        self.registry.clear()
        output = self.fetch(self.base_options())
        self.assertEqual(output["error_key"], "error.missing_core")
        self.assertTrue(output["error"])

    def test_states_without_sensor_data_carry_only_the_explicit_title(self) -> None:
        titled = self.base_options() | {"title": " Outside "}
        self.core.state_error = RuntimeError("offline")
        self.assertEqual(self.fetch(titled)["title"], "Outside")
        self.assertEqual(self.fetch(self.base_options())["title"], "")
        self.assertEqual(self.fetch({"title": "Outside"}), {"setup": True, "title": "Outside"})
        self.registry.clear()
        self.assertEqual(self.fetch(titled)["title"], "Outside")

    def test_fetches_states_once_and_applies_automatic_fallbacks(self) -> None:
        output = self.fetch(self.base_options())
        self.assertEqual(self.core.states_calls, 1)
        self.assertEqual(output["title"], "Back Garden")
        self.assertEqual(output["values_position"], "right")
        self.assertEqual(output["primary_value"], "7.9")
        self.assertEqual(output["primary_unit"], "°C")
        self.assertEqual(output["primary_label"], "Temperature")
        self.assertEqual(output["primary_label_key"], "label.temperature")
        self.assertEqual(output["primary_icon"], "thermometer")
        self.assertEqual(output["secondary_label_key"], "label.humidity")
        self.assertEqual(output["secondary_icon"], "drop")

    def test_explicit_title_labels_icons_and_left_position_win(self) -> None:
        options = self.base_options() | {
            "title": "Outside",
            "primary_label": "Air",
            "primary_icon": "house",
            "secondary_label": "Humidity now",
            "secondary_icon": "chart-line",
            "values_position": "left",
        }
        output = self.fetch(options)
        self.assertEqual(output["title"], "Outside")
        self.assertEqual(output["primary_label"], "Air")
        self.assertEqual(output["primary_label_key"], "")
        self.assertEqual(output["primary_icon"], "house")
        self.assertEqual(output["secondary_label"], "Humidity now")
        self.assertEqual(output["secondary_icon"], "chart-line")
        self.assertEqual(output["values_position"], "left")

    def test_none_icon_is_preserved_independently(self) -> None:
        options = self.base_options() | {"primary_icon": "none"}
        output = self.fetch(options)
        self.assertEqual(output["primary_icon"], "none")
        self.assertEqual(output["secondary_icon"], "drop")

    def test_whitespace_and_invalid_stored_values_use_safe_defaults(self) -> None:
        options = self.base_options() | {
            "title": "   ",
            "primary_label": "   ",
            "primary_icon": "not-an-icon",
            "values_position": "middle",
        }
        output = self.fetch(options)
        self.assertEqual(output["title"], "Back Garden")
        self.assertEqual(output["primary_label_key"], "label.temperature")
        self.assertEqual(output["primary_icon"], "thermometer")
        self.assertEqual(output["values_position"], "right")

    def test_minimum_y_span_defaults_to_five_and_accepts_positive_values(self) -> None:
        self.assertEqual(self.fetch(self.base_options())["minimum_y_span"], 5.0)
        options = self.base_options() | {"minimum_y_span": "10"}
        self.assertEqual(self.fetch(options)["minimum_y_span"], 10.0)
        options["minimum_y_span"] = "invalid"
        self.assertEqual(self.fetch(options)["minimum_y_span"], 5.0)

    def test_unknown_device_class_uses_friendly_name_and_gauge(self) -> None:
        self.core.states[0]["attributes"]["device_class"] = "custom_measurement"
        self.core.states[0]["state"] = "open"
        output = self.fetch(self.base_options())
        self.assertEqual(output["primary_value"], "open")
        self.assertEqual(output["primary_label"], "Back Garden")
        self.assertEqual(output["primary_label_key"], "")
        self.assertEqual(output["primary_icon"], "gauge")

    def test_missing_primary_uses_entity_id_fallback_while_secondary_survives(self) -> None:
        self.core.states = [self.core.states[1]]
        output = self.fetch(self.base_options())
        self.assertEqual(output["title"], "sensor.outdoor_temperature")
        self.assertEqual(output["primary_value"], "—")
        self.assertEqual(output["primary_unit"], "")
        self.assertEqual(output["primary_label"], "sensor.outdoor_temperature")
        self.assertEqual(output["primary_label_key"], "")
        self.assertEqual(output["primary_icon"], "gauge")
        self.assertEqual(output["secondary_value"], "82.0")

    def test_unavailable_preserves_known_unit_and_missing_entity_has_none(self) -> None:
        self.core.states = [
            {
                "entity_id": "sensor.outdoor_temperature",
                "state": "unavailable",
                "attributes": {
                    "friendly_name": "Back Garden",
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                },
            }
        ]
        output = self.fetch(self.base_options())
        self.assertEqual(output["primary_value"], "—")
        self.assertEqual(output["primary_unit"], "°C")
        self.assertEqual(output["secondary_value"], "—")
        self.assertEqual(output["secondary_unit"], "")
        self.assertEqual(output["secondary_label"], "sensor.outdoor_humidity")
        self.assertEqual(output["secondary_icon"], "gauge")

    def test_same_entity_is_allowed_for_both_values(self) -> None:
        options = self.base_options() | {"secondary_entity": "sensor.outdoor_temperature"}
        output = self.fetch(options)
        self.assertEqual(output["primary_value"], output["secondary_value"])
        self.assertEqual(self.core.states_calls, 1)

    def test_current_state_failure_is_card_level(self) -> None:
        self.core.state_error = RuntimeError("offline")
        output = self.fetch(self.base_options())
        self.assertEqual(output["error_key"], "error.fetch")
        self.assertEqual(output["error"], "safe:offline")
        self.assertEqual(self.core.history_calls, [])

    def test_history_is_called_once_and_filters_non_finite_samples(self) -> None:
        self.core.history_result = [
            {"state": "1", "last_changed": "2026-08-31T00:00:00+00:00"},
            {"state": "unknown", "last_changed": "2026-08-31T01:00:00+00:00"},
            {"state": "nan", "last_changed": "2026-08-31T02:00:00+00:00"},
            {"state": "3.5", "last_changed": "2026-08-31T03:00:00+00:00"},
        ]
        output = self.fetch(self.base_options())
        self.assertEqual(
            self.core.history_calls,
            [("sensor.outdoor_temperature", 24)],
        )
        self.assertEqual(output["history_values"], [1.0, 3.5])
        self.assertEqual(
            output["history_times"],
            [MIDNIGHT_MS, MIDNIGHT_MS + 3 * HOUR_MS],
        )

    def test_history_drops_samples_without_a_usable_timestamp(self) -> None:
        self.core.history_result = [
            {"state": "1", "last_changed": "2026-08-31T00:00:00+00:00"},
            {"state": "2"},
            {"state": "3", "last_changed": "not a time"},
            {"state": "4", "last_updated": "2026-08-31T03:00:00"},
        ]
        output = self.fetch(self.base_options())
        self.assertEqual(output["history_values"], [1.0, 4.0])
        self.assertEqual(
            output["history_times"],
            [MIDNIGHT_MS, MIDNIGHT_MS + 3 * HOUR_MS],
        )

    def test_fewer_than_two_numeric_points_becomes_no_data(self) -> None:
        self.core.history_result = [
            {"state": "7.5", "last_changed": "2026-08-31T00:00:00+00:00"}
        ]
        output = self.fetch(self.base_options())
        self.assertEqual(output["history_values"], [])
        self.assertEqual(output["history_times"], [])
        self.assertEqual(output["chart_error_key"], "")

    def test_history_failure_keeps_both_current_values(self) -> None:
        self.core.history_error = RuntimeError("history offline")
        output = self.fetch(self.base_options())
        self.assertEqual(output["primary_value"], "7.9")
        self.assertEqual(output["secondary_value"], "82.0")
        self.assertEqual(output["history_values"], [])
        self.assertEqual(output["chart_error_key"], "chart.history_error")
        self.assertEqual(output["chart_error"], "safe:history offline")

    def test_unavailable_primary_still_requests_valid_history(self) -> None:
        self.core.states[0]["state"] = "unavailable"
        self.core.history_result = [
            {"state": "5", "last_changed": "2026-08-31T00:00:00+00:00"},
            {"state": "7", "last_changed": "2026-08-31T01:00:00+00:00"},
        ]
        output = self.fetch(self.base_options())
        self.assertEqual(output["primary_value"], "—")
        self.assertEqual(output["history_values"], [5.0, 7.0])

    def test_history_downsampling_preserves_first_and_last(self) -> None:
        self.core.history_result = [
            {
                "state": str(index),
                "last_changed": f"2026-08-31T{index % 24:02d}:00:00+00:00",
            }
            for index in range(161)
        ]
        output = self.fetch(self.base_options())
        self.assertEqual(len(output["history_values"]), 80)
        self.assertEqual(output["history_values"][0], 0.0)
        self.assertEqual(output["history_values"][-1], 160.0)
        self.assertEqual(len(output["history_times"]), 80)


if __name__ == "__main__":
    unittest.main()
