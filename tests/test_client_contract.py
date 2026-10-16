from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClientContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (ROOT / "client.js").read_text(encoding="utf-8")

    def test_keeps_forbidden_browser_features_out(self) -> None:
        # "animation: false" is the one allowed use: it switches Chart.js motion off.
        for forbidden in (
            r"@media",
            r"transition:",
            r"animation:(?! false)",
            r"requestAnimationFrame",
            r"XMLHttpRequest",
            r"WebSocket",
            r"fetch\(",
        ):
            self.assertIsNone(re.search(forbidden, self.source), forbidden)

    def test_every_named_translation_key_is_in_the_catalog(self) -> None:
        catalog = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
        keys = set(re.findall(r'"((?:setup|error|chart|title)\.\w+)"', self.source))
        self.assertTrue(keys)
        self.assertLessEqual(keys, set(catalog))


if __name__ == "__main__":
    unittest.main()
