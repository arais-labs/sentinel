"""Downloaded bytes must be verified before entering snapd's cache."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "snap_cache", Path(__file__).parents[1] / "fixtures/snap-cache.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class SnapCacheTests(unittest.TestCase):
    def test_seed_reuse_and_reject_invalid_blobs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            cache = source / "cache"
            blob = source / "sample.snap"
            blob.write_bytes(b"verified bytes")
            entry = {
                "filename": blob.name,
                "size": blob.stat().st_size,
                "sha3_384": hashlib.sha3_384(blob.read_bytes()).hexdigest(),
            }
            manifest = source / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "blobs": [entry]}))
            for _ in range(2):
                self.assertEqual(fixture.seed(source, cache), [entry])
                self.assertEqual((cache / entry["sha3_384"]).read_bytes(), blob.read_bytes())
            for key, invalid in (
                ("size", 1),
                ("sha3_384", "0" * 96),
                ("sha3_384", "../escape"),
                ("filename", "../sample.snap"),
            ):
                manifest.write_text(
                    json.dumps({"schema_version": 1, "blobs": [{**entry, key: invalid}]})
                )
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                    fixture.seed(source, cache)
            self.assertEqual(len(list(cache.iterdir())), 1)
            manifest.write_text(json.dumps({"schema_version": 1, "blobs": [entry]}))
            blob.unlink()
            blob.symlink_to(cache / entry["sha3_384"])
            with self.assertRaises(ValueError):
                fixture.seed(source, cache)


if __name__ == "__main__":
    unittest.main()
