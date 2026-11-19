import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook

import sync_public


class SyncPublicTests(unittest.TestCase):
    def workbook_bytes(self, *, consent: str = "Oui", photo_url: str = "") -> bytes:
        workbook = Workbook()
        registrations = workbook.active
        registrations.title = "Réponses au formulaire 1"
        registrations.append(
            ["Photo de profil", sync_public.PHOTO_CONSENT_HEADER]
        )
        registrations.append([photo_url, consent])
        config = workbook.create_sheet(sync_public.CONFIG_SHEET_NAME)
        config["A1"] = "BRACKETS_LOCKED"
        config["B1"] = "FALSE"
        workbook.active = 1
        output = io.BytesIO()
        workbook.save(output)
        return output.getvalue()

    def test_extract_sheet_links_uses_registration_sheet_when_config_is_active(self):
        workbook = self.workbook_bytes(
            photo_url="https://drive.google.com/file/d/abcdefghijk/view"
        )

        links, consent = sync_public.extract_sheet_links(workbook)

        self.assertEqual(
            links,
            {2: "https://drive.google.com/file/d/abcdefghijk/view"},
        )
        self.assertEqual(consent, {2: True})

    def test_unauthorized_photo_is_not_downloaded(self):
        fighter = sync_public.Fighter(
            last_name="Nom",
            first_name="Prénom",
            sex="Homme",
            age_band="M1",
            weight="-60",
            level="Unique",
            source_row=2,
        )

        photos, manifest = sync_public.consented_photos(
            service=None,
            registrations=pd.DataFrame({"Photo de profil": [""]}),
            fighters=[fighter],
            links={},
            consent={2: False},
        )

        self.assertEqual(photos, {})
        self.assertEqual(manifest[0]["status"], "Photo ignorée : autorisation absente")
        self.assertEqual(manifest[0]["drive_file_id"], "")

    def test_authorized_photo_without_valid_drive_link_fails(self):
        fighter = sync_public.Fighter(
            last_name="Nom",
            first_name="Prénom",
            sex="Homme",
            age_band="M1",
            weight="-60",
            level="Unique",
            source_row=2,
        )

        with self.assertRaisesRegex(sync_public.SyncError, "photo"):
            sync_public.consented_photos(
                service=None,
                registrations=pd.DataFrame({"Photo de profil": [""]}),
                fighters=[fighter],
                links={},
                consent={2: True},
            )

    def test_bracket_lock_setting_is_read_from_syncconfig_sheet(self):
        workbook = self.workbook_bytes()
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(workbook))
        book[sync_public.CONFIG_SHEET_NAME]["B1"] = "TRUE"
        output = io.BytesIO()
        book.save(output)

        self.assertTrue(sync_public.load_bracket_lock(output.getvalue()))

    def test_roster_change_is_detected(self):
        fighter = sync_public.Fighter(
            last_name="Nom",
            first_name="Prénom",
            sex="Homme",
            age_band="M1",
            weight="-60",
            level="unique",
            source_row=2,
        )
        category = ("Homme", "M1", "-60", "unique")

        self.assertTrue(sync_public.roster_changes({category: [fighter]}, {"categories": []}))

    def test_invalid_existing_bracket_is_not_reused(self):
        fighters = [
            sync_public.Fighter(
                last_name=name,
                first_name="Prénom",
                sex="Homme",
                age_band="M1",
                weight="-60",
                level="unique",
                source_row=row,
            )
            for name, row in (("Un", 2), ("Deux", 3))
        ]
        category = ("Homme", "M1", "-60", "unique")
        current_data = {
            "categories": [
                {
                    "category": list(category),
                    "fighters": [
                        {"last_name": fighter.last_name, "first_name": fighter.first_name}
                        for fighter in fighters
                    ],
                    "rounds": [],
                }
            ]
        }

        self.assertEqual(
            sync_public.reused_brackets({category: fighters}, current_data),
            {},
        )

    def test_photo_indices_follow_public_grouping_order(self):
        workbook = Workbook()
        registrations_sheet = workbook.active
        registrations_sheet.title = "Réponses au formulaire 1"
        registrations_sheet.append(
            [
                "Nom",
                "Prénom",
                "Sexe",
                "Catégorie de combat - Masculins",
                "Catégorie de poids - Masculins (13 / 14 ans)",
                "Catégorie de poids - Masculins (15 / 16 ans)",
                "Catégorie de poids - Masculins +17 ans",
                "Catégorie de poids - Féminines",
                "Niveau - Masculins +17 ans",
                "Photo de profil",
                sync_public.PHOTO_CONSENT_HEADER,
            ]
        )
        registrations_sheet.append(
            ["M2", "Participant", "Homme", "15/16 ans", "", "-65", "", "", "", "", "Oui"]
        )
        registrations_sheet.append(
            ["M1", "Participant", "Homme", "13/14 ans", "-60", "", "", "", "", "", "Oui"]
        )
        config = workbook.create_sheet(sync_public.CONFIG_SHEET_NAME)
        config["A1"] = "BRACKETS_LOCKED"
        config["B1"] = "FALSE"
        workbook.active = 0
        source = io.BytesIO()
        workbook.save(source)

        def fake_consented_photos(service, registrations, fighters, links, consent):
            manifest = [
                {
                    "excel_row": str(fighter.source_row),
                    "last_name": fighter.last_name,
                    "first_name": fighter.first_name,
                    "status": "Téléchargée et associée par ligne Excel",
                }
                for fighter in fighters
            ]
            return {
                index: f"photo:{fighter.last_name}".encode()
                for index, fighter in enumerate(fighters)
            }, manifest

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "tournament_public.json").write_text(
                json.dumps({"version": 1, "categories": []}),
                encoding="utf-8",
            )
            with (
                patch.object(sync_public, "ROOT", root),
                patch.object(
                    sync_public,
                    "export_registration_sheet",
                    return_value=(
                        {"name": "registrations", "modifiedTime": "2025-01-01"},
                        source.getvalue(),
                    ),
                ),
                patch.object(sync_public, "extract_sheet_links", return_value=({}, {2: True, 3: True})),
                patch.object(sync_public, "consented_photos", side_effect=fake_consented_photos),
                patch.object(sync_public, "write_private_json"),
            ):
                sync_public.sync(service=object(), reports_service=object())

            public_data = json.loads(
                (root / "tournament_public.json").read_text(encoding="utf-8")
            )
            fighters_by_name = {
                fighter["last_name"]: fighter
                for category in public_data["categories"]
                for fighter in category["fighters"]
            }
            self.assertEqual(fighters_by_name["M1"]["photo"], "fighter_photos/000.jpg")
            self.assertEqual(fighters_by_name["M2"]["photo"], "fighter_photos/001.jpg")
            self.assertEqual(
                (root / "fighter_photos/000.jpg").read_bytes(),
                b"photo:M1",
            )
            self.assertEqual(
                (root / "fighter_photos/001.jpg").read_bytes(),
                b"photo:M2",
            )


if __name__ == "__main__":
    unittest.main()
