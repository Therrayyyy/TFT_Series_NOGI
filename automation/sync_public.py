from __future__ import annotations

import io
import json
import os
import random
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import parse_qs, urlparse

import google.auth
from google.auth.exceptions import DefaultCredentialsError
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openpyxl import load_workbook
from PIL import Image, ImageOps

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


ROOT = Path(__file__).resolve().parents[1]
SHEET_ID = os.environ.get(
    "SPREADSHEET_ID",
    "1WZ65ztvHvXi2TH6KvaJrDg29OzxQot6nm3FuLHdhLmo",
)
MANIFEST_SPREADSHEET_ID = os.environ.get("SYNC_MANIFEST_SPREADSHEET_ID", "")
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
)
REPORT_SHEETS = {
    "photo_manifest.json": "PhotoManifest",
    "sync_status.json": "SyncStatus",
}
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GOOGLE_SHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
CONFIG_SHEET_NAME = "SyncConfig"
MAX_PHOTO_COUNT = 100
MAX_PHOTO_FILE_BYTES = 15 * 1024 * 1024
MAX_PHOTO_TOTAL_BYTES = 50 * 1024 * 1024
PHOTO_SIZE = (320, 320)

FACE_CASCADE = None
EYE_CASCADE = None

WEIGHT_COLUMNS = {
    "F": ("Catégorie de poids - Féminines", "Catégorie de poids (F)"),
    "M1": (
        "Catégorie de poids - Masculins (13 / 14 ans)",
        "Catégorie de poids (M1)",
    ),
    "M2": (
        "Catégorie de poids - Masculins (15 / 16 ans)",
        "Catégorie de poids (M2)",
    ),
    "M3": (
        "Catégorie de poids - Masculins +17 ans",
        "Catégorie de poids (M3)",
    ),
}
FIELD_COLUMNS = {
    "Nom": ("Nom",),
    "Prénom": ("Prénom",),
    "Sexe": ("Sexe",),
    "Catégorie de combat": ("Catégorie de combat - Masculins", "Catégorie de combat"),
    "Niveau": ("Niveau - Masculins +17 ans", "Niveau"),
}
WEIGHTS_BY_CATEGORY = {
    "F": ("-60", "+60"),
    "M1": ("-60", "+60"),
    "M2": ("-65", "+65"),
    "M3": ("-68", "-75", "-85", "+85"),
}
CATEGORY_LABELS = {
    "M1": "13/14 ans",
    "M2": "15/16 ans",
    "M3": "17 ans et plus",
    "F": "Féminine",
}
REQUIRED_FIELDS = (
    "Nom",
    "Prénom",
    "Sexe",
    "Catégorie de combat",
    *WEIGHT_COLUMNS,
    "Niveau",
)


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class Fighter:
    last_name: str
    first_name: str
    sex: str
    age_band: str
    weight: str
    level: str
    source_row: int


@dataclass(frozen=True)
class Match:
    index: int
    first: Fighter | None
    second: Fighter | None
    is_bye: bool = False


Category = tuple[str, str, str, str]


def normalize(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    without_marks = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(without_marks.strip().casefold().split())


def normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(value))


def category_from_label(value: object) -> str | None:
    label = re.sub(r"\s*/\s*", "/", normalize(value))
    if "13/14" in label or label == "m1":
        return "M1"
    if "15/16" in label or label == "m2":
        return "M2"
    if "17+" in label or label == "17" or label == "m3":
        return "M3"
    if "fem" in label:
        return "F"
    return None


def canonical_weight(value: object, category: str, row_number: int) -> str:
    weight = re.sub(r"\s+", "", str(value)).casefold()
    if weight.endswith("kg"):
        weight = weight[:-2]
    if weight not in WEIGHTS_BY_CATEGORY[category]:
        raise SyncError(f"Invalid weight in registration row {row_number}.")
    return weight


def parse_registrations(source: pd.DataFrame) -> list[Fighter]:
    if source.empty or not len(source.columns):
        raise SyncError("The registration sheet is empty.")

    column_indexes: dict[str, int] = {}
    for index, header in enumerate(source.columns):
        key = normalize_header(header)
        if key and key not in column_indexes:
            column_indexes[key] = index

    field_indexes: dict[str, int] = {}
    missing_columns: list[str] = []
    for field in REQUIRED_FIELDS:
        candidates = FIELD_COLUMNS.get(field, WEIGHT_COLUMNS.get(field, ()))
        for candidate in candidates:
            index = column_indexes.get(normalize_header(candidate))
            if index is not None:
                field_indexes[field] = index
                break
        else:
            missing_columns.append(candidates[0] if candidates else field)
    if missing_columns:
        raise SyncError(
            "Required registration columns are missing: " + ", ".join(missing_columns)
        )

    def cell(row: Sequence[object], field: str) -> str:
        index = field_indexes[field]
        if index >= len(row) or pd.isna(row[index]):
            return ""
        return str(row[index]).strip()

    fighters: list[Fighter] = []
    seen_names: dict[tuple[str, str], int] = {}
    errors: list[str] = []
    for row_number, row in enumerate(source.itertuples(index=False, name=None), start=2):
        if all(pd.isna(value) or not str(value).strip() for value in row):
            continue
        last_name = cell(row, "Nom")
        first_name = cell(row, "Prénom")
        if not last_name or not first_name:
            errors.append(f"row {row_number}: name is missing")
            continue
        name_key = (normalize(last_name), normalize(first_name))
        if name_key in seen_names:
            errors.append(f"row {row_number}: duplicate name (row {seen_names[name_key]})")
            continue
        seen_names[name_key] = row_number

        sex_label = cell(row, "Sexe")
        sex = next(
            (
                candidate
                for candidate in ("Homme", "Femme")
                if normalize(candidate) == normalize(sex_label)
            ),
            None,
        )
        if sex is None:
            errors.append(f"row {row_number}: invalid sex")
            continue

        populated_categories = [
            category for category in WEIGHT_COLUMNS if cell(row, category)
        ]
        if len(populated_categories) != 1:
            errors.append(f"row {row_number}: expected exactly one weight category")
            continue
        age_band = populated_categories[0]
        declared_category = category_from_label(cell(row, "Catégorie de combat"))
        if sex == "Homme" and declared_category is None:
            errors.append(f"row {row_number}: invalid combat category")
            continue
        if (sex == "Femme" and age_band != "F") or (
            sex == "Homme" and (age_band == "F" or declared_category != age_band)
        ):
            errors.append(f"row {row_number}: combat and weight categories do not match")
            continue

        try:
            weight = canonical_weight(cell(row, age_band), age_band, row_number)
        except SyncError as error:
            errors.append(str(error))
            continue

        raw_level = cell(row, "Niveau")
        if age_band == "M3" and not raw_level:
            errors.append(f"row {row_number}: level is required")
            continue
        fighters.append(
            Fighter(
                last_name=last_name,
                first_name=first_name,
                sex=sex,
                age_band=age_band,
                weight=weight,
                level=raw_level if age_band == "M3" else "Unique",
                source_row=row_number,
            )
        )
    if errors:
        raise SyncError("Invalid registrations: " + "; ".join(errors[:20]))
    if not fighters:
        raise SyncError("No valid registrations were found.")
    return fighters


def category_sort_key(category: Category) -> tuple[int, int, int, str]:
    sex, age_band, weight, level = category
    return (
        0 if sex == "Homme" else 1,
        ("M1", "M2", "M3", "F").index(age_band),
        WEIGHTS_BY_CATEGORY[age_band].index(weight),
        level,
    )


def group_fighters(fighters: Sequence[Fighter]) -> dict[Category, list[Fighter]]:
    grouped: dict[Category, list[Fighter]] = {}
    for fighter in fighters:
        category = (
            fighter.sex,
            fighter.age_band,
            fighter.weight,
            normalize(fighter.level),
        )
        grouped.setdefault(category, []).append(fighter)
    return dict(sorted(grouped.items(), key=lambda item: category_sort_key(item[0])))


def build_bracket(fighters: Sequence[Fighter]) -> list[list[Match]]:
    if not fighters or len(fighters) == 3:
        return []
    if len(fighters) == 1:
        return [[Match(index=0, first=fighters[0], second=None)]]
    entrants = list(fighters)
    random.SystemRandom().shuffle(entrants)
    bracket_size = 1 << (len(entrants) - 1).bit_length()
    bye_count = bracket_size - len(entrants)
    slots: list[Fighter | None] = []
    for index in range(bye_count):
        slots.extend((entrants[index], None))
    slots.extend(entrants[bye_count:])
    rounds = [[
        Match(
            index=index // 2,
            first=slots[index],
            second=slots[index + 1],
            is_bye=(slots[index] is None) != (slots[index + 1] is None),
        )
        for index in range(0, bracket_size, 2)
    ]]
    current = rounds[0]
    while len(current) > 1:
        current = [
            Match(
                index=index // 2,
                first=(
                    current[index].first
                    if current[index].is_bye
                    else None
                ),
                second=(
                    current[index + 1].first
                    if current[index + 1].is_bye
                    else None
                ),
            )
            for index in range(0, len(current), 2)
        ]
        rounds.append(current)
    return rounds


def fighter_public_data(fighter: Fighter, photo_path: str | None) -> dict[str, str]:
    data = {
        "last_name": fighter.last_name,
        "first_name": fighter.first_name,
        "sex": fighter.sex,
        "age_band": fighter.age_band,
        "weight": fighter.weight,
        "level": fighter.level,
    }
    if photo_path:
        data["photo"] = photo_path
    return data


def drive_file_id_from_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {
        "drive.google.com",
        "docs.google.com",
    }:
        return None
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and re.fullmatch(r"[A-Za-z0-9_-]{10,}", query_id):
        return query_id
    match = re.search(r"/d/([A-Za-z0-9_-]{10,})(?:/|$)", parsed.path)
    return match.group(1) if match else None


def extract_sheet_links(workbook: bytes) -> tuple[dict[int, str], dict[int, bool]]:
    try:
        source = load_workbook(io.BytesIO(workbook), data_only=False, read_only=False)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SyncError("The exported spreadsheet could not be read.") from error
    worksheet = source.worksheets[0]
    headers = {
        normalize_header(cell.value): cell.column
        for cell in worksheet[1]
        if cell.value is not None
    }
    photo_column = headers.get(normalize_header("Photo de profil"))
    if photo_column is None:
        source.close()
        raise SyncError("The required Photo de profil column is missing.")

    links: dict[int, str] = {}
    consent: dict[int, bool] = {}
    for row_number in range(2, worksheet.max_row + 1):
        photo_cell = worksheet.cell(row=row_number, column=photo_column)
        target = photo_cell.hyperlink.target if photo_cell.hyperlink else None
        photo_value = (
            str(photo_cell.value).strip()
            if photo_cell.value is not None
            else ""
        )
        if not target and isinstance(photo_cell.value, str):
            if photo_value.startswith(("https://", "http://")):
                target = photo_value
        if target:
            links[row_number] = target
        consent[row_number] = bool(target or photo_value)
    source.close()
    return links, consent


def detect_face_anchor(image: Image.Image) -> tuple[int, int] | None:
    if cv2 is None or np is None:
        return None

    global FACE_CASCADE, EYE_CASCADE
    if FACE_CASCADE is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        FACE_CASCADE = cv2.CascadeClassifier(cascade_path)
    if EYE_CASCADE is None:
        eye_cascade_path = cv2.data.haarcascades + "haarcascade_eye.xml"
        EYE_CASCADE = cv2.CascadeClassifier(eye_cascade_path)
    if FACE_CASCADE.empty():
        return None

    width, height = image.size
    largest_side = max(width, height)
    if largest_side > 1200:
        scale = 1200 / largest_side
        analysis_image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    else:
        scale = 1.0
        analysis_image = image

    gray = cv2.cvtColor(np.asarray(analysis_image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    faces = FACE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.05,
        minNeighbors=3,
        minSize=(24, 24),
    )
    if len(faces) == 0:
        hsv = cv2.cvtColor(np.asarray(analysis_image.convert("RGB")), cv2.COLOR_RGB2HSV)
        lower_skin = np.array([0, 20, 60], dtype=np.uint8)
        upper_skin = np.array([25, 170, 255], dtype=np.uint8)
        skin_mask = cv2.inRange(hsv, lower_skin, upper_skin)
        kernel = np.ones((9, 9), dtype=np.uint8)
        skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(
            skin_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if contours:
            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)
            image_area = gray.shape[0] * gray.shape[1]
            if area >= image_area * 0.02:
                moments = cv2.moments(largest)
                if moments["m00"] > 0:
                    anchor_x = moments["m10"] / moments["m00"]
                    anchor_y = moments["m01"] / moments["m00"]
                    return round(anchor_x / scale), round(anchor_y / scale)
        return None

    x, y, face_width, face_height = max(
        faces,
        key=lambda face: int(face[2]) * int(face[3]),
    )
    eyes: tuple[object, ...] = ()
    if not EYE_CASCADE.empty():
        face_roi = gray[y : y + int(face_height * 0.65), x : x + face_width]
        eyes = tuple(EYE_CASCADE.detectMultiScale(face_roi, scaleFactor=1.1, minNeighbors=4))

    if eyes:
        eye_x = sum(int(eye[0]) + int(eye[2]) // 2 for eye in eyes) / len(eyes)
        eye_y = sum(int(eye[1]) + int(eye[3]) // 2 for eye in eyes) / len(eyes)
        anchor_x = x + eye_x
        anchor_y = y + eye_y
    else:
        anchor_x = x + face_width / 2
        anchor_y = y + face_height / 2

    return round(anchor_x / scale), round(anchor_y / scale)


def smart_square_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    if side == 0:
        return image.resize(PHOTO_SIZE, Image.Resampling.LANCZOS)

    anchor = detect_face_anchor(image)
    if anchor is None:
        return ImageOps.fit(
            image,
            PHOTO_SIZE,
            method=Image.Resampling.LANCZOS,
        )

    anchor_x, anchor_y = anchor
    left = round(anchor_x - side / 2)
    top = round(anchor_y - side / 2)
    left = min(max(left, 0), max(0, width - side))
    top = min(max(top, 0), max(0, height - side))
    cropped = image.crop((left, top, left + side, top + side))
    return cropped.resize(PHOTO_SIZE, Image.Resampling.LANCZOS)


def consented_photos(
    service,
    registrations: pd.DataFrame,
    fighters: Sequence[Fighter],
    links: dict[int, str],
    consent: dict[int, bool],
) -> tuple[dict[int, bytes], list[dict[str, str]]]:
    fighters_by_row = {fighter.source_row: index for index, fighter in enumerate(fighters)}
    manifest: list[dict[str, str]] = []
    rows_by_file_id: dict[str, list[int]] = {}
    photo_column = next(
        index
        for index, header in enumerate(registrations.columns)
        if normalize_header(header) == normalize_header("Photo de profil")
    )

    for row_number, values in enumerate(
        registrations.itertuples(index=False, name=None),
        start=2,
    ):
        fighter_index = fighters_by_row.get(row_number)
        if fighter_index is None:
            continue
        fighter = fighters[fighter_index]
        has_consent = consent.get(row_number, False)
        url = links.get(
            row_number,
            str(values[photo_column]).strip() if photo_column < len(values) else "",
        )
        row = {
            "excel_row": str(row_number),
            "last_name": fighter.last_name,
            "first_name": fighter.first_name,
            "drive_file_id": "",
            "status": "Photo non fournie",
            "public_path": "",
        }
        manifest.append(row)
        if not has_consent:
            continue
        file_id = drive_file_id_from_url(url)
        if not file_id:
            row["status"] = "Erreur : lien Drive photo absent ou invalide"
            continue
        row["drive_file_id"] = file_id
        row["status"] = "En attente de téléchargement"
        rows_by_file_id.setdefault(file_id, []).append(row_number)

    failures = [row for row in manifest if row["status"].startswith("Erreur :")]
    if failures:
        raise SyncError(
            "A photo marked as authorized has no valid Drive URL "
            f"(registration rows: {', '.join(row['excel_row'] for row in failures)})."
        )
    duplicates = [
        rows
        for rows in rows_by_file_id.values()
        if len(rows) > 1
    ]
    if duplicates:
        raise SyncError(
            "A Drive photo URL is assigned to multiple registrations; "
            "publication was stopped."
        )
    if len(rows_by_file_id) > MAX_PHOTO_COUNT:
        raise SyncError("The number of authorized photos exceeds the configured limit.")

    photos_by_fighter: dict[int, bytes] = {}
    total_bytes = 0
    for file_id, row_numbers in rows_by_file_id.items():
        row_number = row_numbers[0]
        try:
            metadata = (
                service.files()
                .get(fileId=file_id, fields="mimeType,size")
                .execute()
            )
            if not str(metadata.get("mimeType", "")).startswith("image/"):
                raise SyncError(f"Authorized photo at row {row_number} is not an image.")
            if int(metadata.get("size", 0)) > MAX_PHOTO_FILE_BYTES:
                raise SyncError(f"Authorized photo at row {row_number} is too large.")
            image_data = service.files().get_media(fileId=file_id).execute()
            if len(image_data) > MAX_PHOTO_FILE_BYTES:
                raise SyncError(f"Authorized photo at row {row_number} is too large.")
            with Image.open(io.BytesIO(image_data)) as image:
                if image.width * image.height > 40_000_000:
                    raise SyncError(f"Authorized photo at row {row_number} is too large.")
                image = ImageOps.exif_transpose(image).convert("RGB")
                image = smart_square_crop(image)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=84, optimize=True)
                normalized_image = output.getvalue()
        except HttpError as error:
            raise SyncError(
                f"Google Drive could not read an authorized photo (HTTP {error.resp.status})."
            ) from error
        except (OSError, ValueError) as error:
            raise SyncError(
                f"An authorized photo at row {row_number} could not be decoded."
            ) from error

        total_bytes += len(normalized_image)
        if total_bytes > MAX_PHOTO_TOTAL_BYTES:
            raise SyncError("The total size of authorized photos exceeds 50 MB.")
        fighter_index = fighters_by_row[row_number]
        photos_by_fighter[fighter_index] = normalized_image
        for row in manifest:
            if row["excel_row"] == str(row_number):
                row["status"] = "Téléchargée et associée par ligne Excel"
    return photos_by_fighter, manifest


def category_key(value: object) -> Category | None:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(item, str) for item in value)
    ):
        return None
    return value[0], value[1], value[2], value[3]


def roster_key(value: dict) -> tuple[str, str]:
    return normalize(value.get("last_name", "")), normalize(value.get("first_name", ""))


def bracket_has_expected_shape(
    fighters: Sequence[Fighter],
    rounds: list[list[Match]],
) -> bool:
    if len(fighters) == 3:
        return not rounds
    if len(fighters) == 1:
        return (
            len(rounds) == 1
            and len(rounds[0]) == 1
            and rounds[0][0].first == fighters[0]
            and rounds[0][0].second is None
            and not rounds[0][0].is_bye
        )

    bracket_size = 1 << (len(fighters) - 1).bit_length()
    expected_counts = []
    match_count = bracket_size // 2
    while match_count:
        expected_counts.append(match_count)
        match_count //= 2
    if [len(round_matches) for round_matches in rounds] != expected_counts:
        return False

    first_round = rounds[0]
    entrants = [
        (normalize(fighter.last_name), normalize(fighter.first_name))
        for match in first_round
        for fighter in (match.first, match.second)
        if fighter is not None
    ]
    expected_entrants = [
        (normalize(fighter.last_name), normalize(fighter.first_name))
        for fighter in fighters
    ]
    if sorted(entrants) != sorted(expected_entrants):
        return False
    return all(
        match.is_bye == ((match.first is None) != (match.second is None))
        for match in first_round
    )


def roster_changes(
    grouped: dict[Category, list[Fighter]],
    current_data: dict,
) -> bool:
    old_categories = {
        category_key(category.get("category")): {
            roster_key(fighter)
            for fighter in category.get("fighters", [])
            if isinstance(fighter, dict)
        }
        for category in current_data.get("categories", [])
        if isinstance(category, dict)
    }
    new_categories = {
        category: {
            (normalize(fighter.last_name), normalize(fighter.first_name))
            for fighter in fighters
        }
        for category, fighters in grouped.items()
    }
    return old_categories != new_categories


def reused_brackets(
    grouped: dict[Category, list[Fighter]],
    current_data: dict,
) -> dict[Category, list[list[Match]]]:
    existing: dict[Category, list[list[Match]]] = {}
    for category_data in current_data.get("categories", []):
        if not isinstance(category_data, dict):
            continue
        category = category_key(category_data.get("category"))
        if category not in grouped:
            continue
        fighters_by_key = {
            (normalize(fighter.last_name), normalize(fighter.first_name)): fighter
            for fighter in grouped[category]
        }
        public_fighters = category_data.get("fighters")
        expected = set(fighters_by_key)
        if not isinstance(public_fighters, list) or {
            roster_key(value)
            for value in public_fighters
            if isinstance(value, dict)
        } != expected:
            continue
        rounds_data = category_data.get("rounds")
        if not isinstance(rounds_data, list):
            continue

        def find_fighter(value: object) -> Fighter | None:
            if value is None:
                return None
            if not isinstance(value, dict):
                raise ValueError
            return fighters_by_key[roster_key(value)]

        rounds: list[list[Match]] = []
        try:
            for round_data in rounds_data:
                if not isinstance(round_data, list):
                    raise ValueError
                matches = []
                for match_data in round_data:
                    if not isinstance(match_data, dict) or not isinstance(
                        match_data.get("index"), int
                    ):
                        raise ValueError
                    matches.append(
                        Match(
                            index=match_data["index"],
                            first=find_fighter(match_data.get("first")),
                            second=find_fighter(match_data.get("second")),
                            is_bye=match_data.get("is_bye") is True,
                        )
                    )
                rounds.append(matches)
        except (KeyError, ValueError):
            continue
        if not bracket_has_expected_shape(grouped[category], rounds):
            continue
        existing[category] = rounds
    return existing


def serialize_tournament(
    grouped: dict[Category, list[Fighter]],
    brackets: dict[Category, list[list[Match]]],
    photos_by_fighter: dict[int, bytes],
) -> bytes:
    photo_paths = {
        index: f"fighter_photos/{index:03d}.jpg"
        for index in photos_by_fighter
    }
    fighter_indexes = {
        (normalize(fighter.last_name), normalize(fighter.first_name)): index
        for index, fighter in enumerate(
            fighter for fighters in grouped.values() for fighter in fighters
        )
    }

    def public_data(fighter: Fighter | None) -> dict[str, str] | None:
        if fighter is None:
            return None
        index = fighter_indexes[
            (normalize(fighter.last_name), normalize(fighter.first_name))
        ]
        return fighter_public_data(fighter, photo_paths.get(index))

    categories = []
    for category, fighters in grouped.items():
        categories.append(
            {
                "category": list(category),
                "fighters": [public_data(fighter) for fighter in fighters],
                "rounds": [
                    [
                        {
                            "index": match.index,
                            "first": public_data(match.first),
                            "second": public_data(match.second),
                            "is_bye": match.is_bye,
                        }
                        for match in round_matches
                    ]
                    for round_matches in brackets[category]
                ],
            }
        )
    return json.dumps(
        {"version": 1, "categories": categories},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def report_sheet_range(name: str) -> str:
    sheet_name = REPORT_SHEETS.get(name)
    if not sheet_name:
        raise SyncError(f"Unsupported private report {name}.")
    if not MANIFEST_SPREADSHEET_ID:
        raise SyncError("Configure the private report spreadsheet.")
    return f"{sheet_name}!A1"


def read_private_json(service, name: str) -> dict:
    try:
        response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=MANIFEST_SPREADSHEET_ID,
                range=report_sheet_range(name),
            )
            .execute()
        )
    except HttpError as error:
        raise SyncError(
            f"Could not read the private report {name} (HTTP {error.resp.status})."
        ) from error
    values = response.get("values", [])
    if not values:
        return {}
    try:
        data = json.loads(values[0][0])
    except (IndexError, TypeError, json.JSONDecodeError) as error:
        raise SyncError(f"The private report {name} is not valid JSON.") from error
    if not isinstance(data, dict):
        raise SyncError(f"The private report {name} must contain a JSON object.")
    return data


def write_private_json(service, name: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=MANIFEST_SPREADSHEET_ID,
                range=report_sheet_range(name),
                valueInputOption="RAW",
                body={"values": [[payload]]},
            )
            .execute()
        )
    except HttpError as error:
        raise SyncError(
            f"Could not write the private report {name} (HTTP {error.resp.status})."
        ) from error


def get_google_services():
    try:
        credentials, _ = google.auth.default(scopes=GOOGLE_SCOPES)
    except DefaultCredentialsError as error:
        raise SyncError(
            "Google credentials are unavailable. Configure GitHub Workload Identity Federation."
        ) from error
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    return drive, sheets


def export_registration_sheet(service) -> tuple[dict, bytes]:
    try:
        metadata = service.files().get(
            fileId=SHEET_ID,
            fields="id,name,mimeType,modifiedTime",
        ).execute()
        if metadata.get("mimeType") != GOOGLE_SHEET_MIME_TYPE:
            raise SyncError("The configured source is not a Google spreadsheet.")
        content = service.files().export(
            fileId=SHEET_ID,
            mimeType=XLSX_MIME_TYPE,
        ).execute()
    except HttpError as error:
        raise SyncError(
            f"Google Drive could not export the registration sheet (HTTP {error.resp.status})."
        ) from error
    if len(content) > 25 * 1024 * 1024:
        raise SyncError("The exported registration sheet exceeds 25 MB.")
    return metadata, content


def load_bracket_lock(workbook: bytes) -> bool:
    try:
        book = load_workbook(io.BytesIO(workbook), read_only=True, data_only=True)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SyncError("The exported spreadsheet could not be opened.") from error
    if CONFIG_SHEET_NAME not in book.sheetnames:
        book.close()
        raise SyncError(
            f"Add the {CONFIG_SHEET_NAME} worksheet with BRACKETS_LOCKED set to FALSE or TRUE."
        )
    sheet = book[CONFIG_SHEET_NAME]
    key = normalize_header(sheet["A1"].value)
    value = normalize(sheet["B1"].value)
    book.close()
    if key != normalize_header("BRACKETS_LOCKED") or value not in {
        "true",
        "false",
        "oui",
        "non",
    }:
        raise SyncError(
            f"Set {CONFIG_SHEET_NAME}!A1 to BRACKETS_LOCKED and B1 to FALSE or TRUE."
        )
    return value in {"true", "oui"}


def sync(service, reports_service) -> dict:
    metadata, workbook = export_registration_sheet(service)
    brackets_locked = load_bracket_lock(workbook)
    registrations = pd.read_excel(
        io.BytesIO(workbook),
        sheet_name=0,
        dtype=str,
        keep_default_na=False,
    )
    fighters = parse_registrations(registrations)
    grouped = group_fighters(fighters)
    try:
        current_data = json.loads(
            (ROOT / "tournament_public.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError("The current public tournament JSON is invalid.") from error

    has_roster_changes = roster_changes(grouped, current_data)
    if brackets_locked and has_roster_changes:
        raise SyncError(
            "A registration changed a locked bracket. Public files were not updated."
        )

    fighters_in_public_order = [
        fighter
        for category_fighters in grouped.values()
        for fighter in category_fighters
    ]
    photo_urls, photo_consent = extract_sheet_links(workbook)
    photos_by_fighter, manifest = consented_photos(
        service,
        registrations,
        fighters_in_public_order,
        photo_urls,
        photo_consent,
    )
    brackets = reused_brackets(grouped, current_data)
    if brackets_locked and set(brackets) != set(grouped):
        raise SyncError(
            "A locked bracket is missing or invalid. Public files were not updated."
        )
    for category, category_fighters in grouped.items():
        brackets.setdefault(category, build_bracket(category_fighters))
    public_json = serialize_tournament(grouped, brackets, photos_by_fighter)
    photo_paths = {
        index: f"fighter_photos/{index:03d}.jpg"
        for index in photos_by_fighter
    }
    fighter_indexes = {
        (normalize(fighter.last_name), normalize(fighter.first_name)): index
        for index, fighter in enumerate(
            fighter for fighters in grouped.values() for fighter in fighters
        )
    }
    for row in manifest:
        if row["status"].startswith("Téléchargée"):
            index = fighter_indexes[
                (normalize(row["last_name"]), normalize(row["first_name"]))
            ]
            row["public_path"] = photo_paths[index]

    (ROOT / "tournament_public.json").write_bytes(public_json)
    photo_directory = ROOT / "fighter_photos"
    photo_directory.mkdir(exist_ok=True)
    keep_paths = {Path(path).name for path in photo_paths.values()}
    for old_photo in photo_directory.glob("*.jpg"):
        if old_photo.name not in keep_paths:
            old_photo.unlink()
    for fighter_index, image_data in photos_by_fighter.items():
        (photo_directory / f"{fighter_index:03d}.jpg").write_bytes(image_data)

    write_private_json(
        reports_service,
        "photo_manifest.json",
        {
            "spreadsheet": metadata.get("name", ""),
            "modified_time": metadata.get("modifiedTime", ""),
            "registrations": manifest,
        },
    )
    return {
        "spreadsheet_modified_time": metadata.get("modifiedTime", ""),
        "fighter_count": len(fighters),
        "photo_count": len(photos_by_fighter),
        "brackets_locked": brackets_locked,
        "roster_changed": has_roster_changes,
    }


def write_status(service, status: dict) -> None:
    previous = read_private_json(service, "sync_status.json")
    if status.get("status") in {"prepared", "success"}:
        previous.pop("last_error", None)
    previous.update(status)
    write_private_json(service, "sync_status.json", previous)


def main() -> int:
    service = None
    reports_service = None
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        service, reports_service = get_google_services()
        if sys.argv[1:] == ["--mark-success"]:
            previous = read_private_json(reports_service, "sync_status.json")
            if previous.get("status") != "prepared":
                raise SyncError("There is no prepared synchronization to mark as successful.")
            write_status(
                reports_service,
                {
                    "status": "success",
                    "last_success": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                },
            )
            print("Successful publication recorded in the private Sheets status.")
            return 0
        if sys.argv[1:]:
            raise SyncError("Unsupported synchronization command.")

        result = sync(service, reports_service)
        write_status(
            reports_service,
            {
                "status": "prepared",
                "last_attempt": attempted_at,
                **result,
            },
        )
        print(
            "Synchronization complete: "
            f"{result['fighter_count']} registrations, "
            f"{result['photo_count']} authorized photos."
        )
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(
                    "## Tournament sync prepared\n\n"
                    f"- Registrations: {result['fighter_count']}\n"
                    f"- Consented photos: {result['photo_count']}\n"
                    f"- Brackets locked: {result['brackets_locked']}\n"
                    f"- Roster changed: {result['roster_changed']}\n"
                    f"- Source updated: {result['spreadsheet_modified_time']}\n"
                )
        return 0
    except SyncError as error:
        print(f"Synchronization stopped: {error}", file=sys.stderr)
        if reports_service is not None:
            try:
                write_status(
                    reports_service,
                    {
                        "status": "error",
                        "last_attempt": attempted_at,
                        "last_error": str(error),
                    },
                )
            except (HttpError, SyncError) as status_error:
                print(
                    "Could not update the private sync status "
                    f"(HTTP {status_error.resp.status})."
                    if isinstance(status_error, HttpError)
                    else f"Could not update the private sync status: {status_error}",
                    file=sys.stderr,
                )
        return 1
    except HttpError as error:
        safe_error = f"Google API request failed (HTTP {error.resp.status})."
        print(f"Synchronization stopped: {safe_error}", file=sys.stderr)
        if reports_service is not None:
            try:
                write_status(
                    reports_service,
                    {
                        "status": "error",
                        "last_attempt": attempted_at,
                        "last_error": safe_error,
                    },
                )
            except (HttpError, SyncError) as status_error:
                print(
                    "Could not update the private sync status "
                    f"(HTTP {status_error.resp.status})."
                    if isinstance(status_error, HttpError)
                    else f"Could not update the private sync status: {status_error}",
                    file=sys.stderr,
                )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
