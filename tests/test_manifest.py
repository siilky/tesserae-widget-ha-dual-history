from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ManifestContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    def test_declared_locales_ship_catalogs_with_identical_keys(self) -> None:
        catalogs = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "strings").glob("*.json")
        }
        self.assertEqual(sorted(catalogs), sorted(self.manifest["locales"]))
        for locale, catalog in catalogs.items():
            self.assertEqual(set(catalog), set(catalogs["en"]), locale)

    def test_static_select_defaults_are_offered_choices(self) -> None:
        for option in self.manifest["cell_options"]:
            if "choices" in option:
                values = [choice["value"] for choice in option["choices"]]
                self.assertIn(option["default"], values, option["name"])

    def test_schema_fields_exist_in_the_sample(self) -> None:
        schema = self.manifest["data_schema"]
        for field in schema["fields"]:
            self.assertIn(field["name"], schema["sample"])


if __name__ == "__main__":
    unittest.main()
