import random
import tempfile
import unittest
from pathlib import Path

from generate_absolute_open import build_elimination, generate_pdf, read_names


class AbsoluteOpenTests(unittest.TestCase):
    def test_read_names_rejects_duplicate_names(self) -> None:
        with self.assertRaises(ValueError):
            read_names(["Alice\n", "alice\n"])

    def test_read_names_requires_two_participants(self) -> None:
        with self.assertRaises(ValueError):
            read_names(["Alice\n"])

    def test_seeded_bracket_is_reproducible(self) -> None:
        names = ["A", "B", "C", "D", "E"]
        first = build_elimination(names, seed="event")
        second = build_elimination(names, seed="event")
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(names) - 1)

    def test_pdf_is_created_and_contains_every_elimination_bout(self) -> None:
        names = ["A", "B", "C", "D"]
        bouts = build_elimination(names, seed="test")
        with tempfile.TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "private.pdf"
            count = generate_pdf(output, names, seed="test")
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 100)
            self.assertEqual(count, len(bouts))
            self.assertEqual(count, len(names) - 1)

    def test_two_participant_bracket_has_one_bout(self) -> None:
        bouts = build_elimination(["A", "B"], seed="test")
        self.assertEqual(len(bouts), 1)
        self.assertEqual(bouts[0].round_name, "Finale")


if __name__ == "__main__":
    unittest.main()
