"""Pure contract tests for the live native LocalSearch qualification fixture."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "desktop_localsearch_fixture",
    Path(__file__).parents[1] / "fixtures" / "desktop-localsearch.py",
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class LocalSearchFixtureTests(unittest.TestCase):
    def test_unversioned_world_entry_preserves_other_constraints(self):
        fixture.assert_unpinned("busybox=1.37.0-r1\nlocalsearch\nlocalsearch-lang=3.11.1-r1\n")

    def test_checksum_version_and_repository_pins_are_rejected(self):
        for entry in (
            "",
            "localsearch=3.11.1-r1",
            "localsearch><Q1abc",
            "localsearch@sentinel",
            "localsearch~3.11",
            "!localsearch",
        ):
            with self.subTest(entry=entry), self.assertRaises(AssertionError):
                fixture.assert_unpinned(entry)

    def test_duplicate_world_entry_is_rejected(self):
        with self.assertRaises(AssertionError):
            fixture.assert_unpinned("localsearch\nlocalsearch\n")

    def test_jsonld_text_content_is_required(self):
        fixture.assert_content_extracted(
            {
                "@graph": [
                    {
                        "nie:plainTextContent": [{"@value": "actual uniquetoken text"}],
                    }
                ]
            },
            "uniquetoken",
        )
        fixture.assert_content_extracted(
            {
                "http://www.semanticdesktop.org/ontologies/2007/01/19/nie#plainTextContent": "actual uniquetoken text",
            },
            "uniquetoken",
        )

    def test_filename_or_uri_match_cannot_fake_extracted_content(self):
        for metadata in (
            {"nie:url": "file:///tmp/uniquetoken.txt"},
            {"nfo:fileName": "uniquetoken.txt", "nie:plainTextContent": "other"},
            {"nie:plainTextContent": []},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(AssertionError):
                fixture.assert_content_extracted(metadata, "uniquetoken")


if __name__ == "__main__":
    unittest.main()
