from pathlib import Path
import runpy
import unittest

check_report = runpy.run_path(str(Path(__file__).with_name("check-storage-report.py")))[
    "check_report"
]


class StorageReportTests(unittest.TestCase):
    def test_zero_captures_with_other_statistics(self):
        check_report(
            "diagnostic\nstorage_stats upload_bytes=65536 read_captures=0 capture_bytes=0\n"
        )

    def test_missing_report_is_not_a_pass(self):
        with self.assertRaises(ValueError):
            check_report("PASS upload-only fixture\n")

    def test_every_report_must_have_both_zero_counters(self):
        valid = "storage_stats read_captures=0 capture_bytes=0\n"
        for invalid in (
            "storage_stats read_captures=1 capture_bytes=0",
            "storage_stats read_captures=0 capture_bytes=1048576",
            "storage_stats read_captures=0",
            "storage_stats capture_bytes=0",
            "storage_stats read_captures=unknown capture_bytes=0",
        ):
            with self.subTest(report=invalid), self.assertRaises(ValueError):
                check_report(valid + invalid)


if __name__ == "__main__":
    unittest.main()
