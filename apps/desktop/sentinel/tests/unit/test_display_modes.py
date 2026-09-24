"""Keep the finite monitor contract consistent across packaged consumers."""

import json
from pathlib import Path
import runpy
import tempfile
import unittest

DESKTOP = Path(__file__).resolve().parents[2]
APPS = DESKTOP.parents[1]
CONTRACT = DESKTOP / "native/graphics/display/modes.json"
generate = runpy.run_path(str(DESKTOP / "scripts/packaging/graphics/generate-display-modes.py"))[
    "generate"
]


class DisplayModeContractTests(unittest.TestCase):
    def test_generated_consumers_match_canonical_contract(self):
        consumers = {
            DESKTOP / "native/graphics/guest/desktop_modes.py": "python",
            APPS / "backend/sentinel/app/services/runtime/desktop_modes.py": "python",
            APPS / "frontend/sentinel/src/lib/desktop-modes.ts": "typescript",
        }
        for path, language in consumers.items():
            with self.subTest(consumer=str(path)):
                self.assertEqual(path.read_text(), generate(CONTRACT, language))

    def test_invalid_modes_are_rejected_before_compilation(self):
        contract = json.loads(CONTRACT.read_text())
        changes = [
            {"schema": True},
            {"refresh_hz": 0},
            {"modes": []},
            {"modes": [[1281, 800]]},
            {"modes": [[1280, True]]},
            {"modes": [[1280, 800], [1280, 800]]},
            {"default_geometry": "999x999"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "modes.json"
            for change in changes:
                with self.subTest(change=change):
                    path.write_text(json.dumps({**contract, **change}))
                    with self.assertRaises(ValueError):
                        generate(path)


if __name__ == "__main__":
    unittest.main()
