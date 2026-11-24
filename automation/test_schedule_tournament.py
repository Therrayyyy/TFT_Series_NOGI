import tempfile
import unittest
from pathlib import Path

import pandas as pd

from schedule_tournament import (
    export_schedule_csv,
    export_schedule_pdf,
    generate_schedule,
    test_bouts_22,
    validate_schedule,
)


class TournamentScheduleTests(unittest.TestCase):
    def test_test_dataset_has_22_bouts(self) -> None:
        bouts = test_bouts_22()
        self.assertEqual(len(bouts), 22)

    def test_two_tatami_schedule_is_valid_and_balanced(self) -> None:
        frame, report = generate_schedule(test_bouts_22(), nb_tatamis=2, seed=42)
        self.assertEqual(len(frame), 22)
        self.assertEqual(report["critical_violations"], 0)
        self.assertLessEqual(report["tatami_load_difference"], 1)
        self.assertEqual(report["missing_bouts"], [])
        self.assertEqual(report["duplicate_bouts"], 0)

    def test_one_tatami_schedule_respects_dependencies(self) -> None:
        frame, report = generate_schedule(test_bouts_22(), nb_tatamis=1, seed=7)
        self.assertEqual(len(frame), 22)
        self.assertEqual(report["critical_violations"], 0)
        self.assertEqual(report["simultaneous_violations"], [])

    def test_csv_and_pdf_exports_are_created(self) -> None:
        frame, report = generate_schedule(test_bouts_22(), nb_tatamis=2, seed=42)
        with tempfile.TemporaryDirectory() as temporary_dir:
            csv_path = Path(temporary_dir) / "schedule.csv"
            pdf_path = Path(temporary_dir) / "schedule.pdf"
            export_schedule_csv(frame, csv_path)
            export_schedule_pdf(frame, pdf_path, report)
            self.assertTrue(csv_path.exists())
            self.assertGreater(csv_path.stat().st_size, 100)
            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 100)

    def test_duplicate_or_unknown_dependencies_are_rejected(self) -> None:
        bouts = test_bouts_22()
        duplicate = bouts + [bouts[0].copy()]
        with self.assertRaises(ValueError):
            generate_schedule(duplicate, nb_tatamis=2)

        unknown = bouts + [{
            "id": "UNKNOWN",
            "categorie": "Test",
            "phase": "Finale",
            "combattant_1": "A",
            "combattant_2": "B",
            "dependances": ["MISSING"],
        }]
        with self.assertRaises(ValueError):
            generate_schedule(unknown, nb_tatamis=2)


if __name__ == "__main__":
    unittest.main()
