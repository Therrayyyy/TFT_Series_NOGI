from __future__ import annotations

import base64
import html
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import streamlit as st

from automation.schedule_tournament import (
    export_schedule_pdf_bytes,
    generate_schedule,
)


APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = APP_DIR / "logo_club.png"
PLANNING_PATH = APP_DIR / "planning_29nov.jpg"
PUBLIC_TOURNAMENT_PATH = APP_DIR / "tournament_public.json"
PHOTO_PATH_PATTERN = re.compile(r"fighter_photos/\d{3,}\.jpg")

CATEGORY_LABELS = {
    "M1": "13/14 ans",
    "M2": "15/16 ans",
    "M3": "17 ans et plus",
    "F": "Féminine",
}
WEIGHTS_BY_CATEGORY = {
    "F": ("-60", "+60"),
    "M1": ("-60", "+60"),
    "M2": ("-65", "+65"),
    "M3": ("-68", "-75", "-85", "+85"),
}


@dataclass(frozen=True)
class Fighter:
    last_name: str
    first_name: str
    sex: str
    age_band: str
    weight: str
    level: str
    photo_data: bytes | None = None

    @property
    def display_name(self) -> str:
        return f"{self.last_name.upper()} {self.first_name}"


@dataclass(frozen=True)
class Match:
    index: int
    first: Fighter | None
    second: Fighter | None
    is_bye: bool = False


Category = tuple[str, str, str, str]


def load_public_tournament() -> tuple[list[Fighter], dict[Category, list[list[Match]]]]:
    if not PUBLIC_TOURNAMENT_PATH.exists():
        raise ValueError(
            "Aucun tirage public n’est disponible. L’administrateur doit publier "
            "le fichier tournament_public.json."
        )
    try:
        data = json.loads(PUBLIC_TOURNAMENT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Impossible de lire le fichier de tirage public : {error}") from error

    if (
        not isinstance(data, dict)
        or isinstance(data.get("version"), bool)
        or data.get("version") != 1
    ):
        raise ValueError("Le fichier de tirage public a un format ou une version invalide.")
    categories = data.get("categories")
    if not isinstance(categories, list):
        raise ValueError("Le fichier de tirage public ne contient pas de catégories valides.")

    def read_fighter(value: object) -> Fighter | None:
        if value is None:
            return None
        fields = ("last_name", "first_name", "sex", "age_band", "weight", "level")
        if not isinstance(value, dict) or any(
            not isinstance(value.get(field), str) for field in fields
        ):
            raise ValueError("Un combattant du fichier public a des données invalides.")
        return Fighter(
            last_name=value["last_name"],
            first_name=value["first_name"],
            sex=value["sex"],
            age_band=value["age_band"],
            weight=value["weight"],
            level=value["level"],
            photo_data=read_public_photo(value.get("photo")),
        )

    fighters: list[Fighter] = []
    bracket_by_category: dict[Category, list[list[Match]]] = {}
    for category_data in categories:
        if not isinstance(category_data, dict):
            raise ValueError("Une catégorie du fichier public est invalide.")
        category_value = category_data.get("category")
        fighter_values = category_data.get("fighters")
        round_values = category_data.get("rounds")
        if (
            not isinstance(category_value, list)
            or len(category_value) != 4
            or any(not isinstance(value, str) for value in category_value)
            or not isinstance(fighter_values, list)
            or not isinstance(round_values, list)
        ):
            raise ValueError("Les données d’une catégorie du fichier public sont invalides.")
        category: Category = (
            category_value[0],
            category_value[1],
            category_value[2],
            category_value[3],
        )
        if category in bracket_by_category or not fighter_values:
            raise ValueError("Le fichier public contient une catégorie vide ou en doublon.")

        category_fighters = [read_fighter(value) for value in fighter_values]
        if any(fighter is None for fighter in category_fighters):
            raise ValueError("Un combattant de catégorie est manquant.")
        actual_fighters = [fighter for fighter in category_fighters if fighter is not None]
        if set(group_fighters(actual_fighters)) != {category}:
            raise ValueError("Les combattants ne correspondent pas à leur catégorie.")
        fighters.extend(actual_fighters)

        rounds: list[list[Match]] = []
        for round_value in round_values:
            if not isinstance(round_value, list):
                raise ValueError("Un tour du tableau public est invalide.")
            round_matches: list[Match] = []
            for match_value in round_value:
                if not isinstance(match_value, dict):
                    raise ValueError("Un combat du tableau public est invalide.")
                if not {"index", "first", "second", "is_bye"}.issubset(match_value):
                    raise ValueError("Les données d’un combat du tableau public sont incomplètes.")
                match_index = match_value.get("index")
                is_bye = match_value.get("is_bye")
                if (
                    isinstance(match_index, bool)
                    or not isinstance(match_index, int)
                    or not isinstance(is_bye, bool)
                ):
                    raise ValueError("Les données d’un combat du tableau public sont invalides.")
                round_matches.append(
                    Match(
                        index=match_index,
                        first=read_fighter(match_value.get("first")),
                        second=read_fighter(match_value.get("second")),
                        is_bye=is_bye,
                    )
                )
            rounds.append(round_matches)
        bracket_by_category[category] = rounds

    return fighters, bracket_by_category


def read_public_photo(value: object) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str) or PHOTO_PATH_PATTERN.fullmatch(value) is None:
        raise ValueError("Le chemin d’une photo du fichier public est invalide.")
    photo_path = APP_DIR / value
    try:
        return photo_path.read_bytes()
    except OSError as error:
        raise ValueError(
            f"Impossible de lire la photo publique « {Path(value).name} » : {error}"
        ) from error


def fighter_photo_html(fighter: Fighter | None) -> str:
    if fighter is None or fighter.photo_data is None:
        return ""
    encoded = base64.b64encode(fighter.photo_data).decode("ascii")
    return f'<img class="fighter-avatar" src="data:image/jpeg;base64,{encoded}" alt="">'


def draw_pdf_fighter(
    pdf: Any,
    image_reader: Any,
    fighter: Fighter | None,
    x: float,
    y: float,
    size: int,
) -> None:
    if fighter is not None and fighter.photo_data is not None:
        pdf.drawImage(
            image_reader(io.BytesIO(fighter.photo_data)),
            x,
            y,
            width=size,
            height=size,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )


def wrap_pdf_text(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, font_name, font_size) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def normalize(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    without_marks = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(without_marks.strip().casefold().split())


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


def category_sort_key(category: Category) -> tuple[int, int, int, str]:
    sex, age_band, weight, level = category
    sex_order = 0 if sex == "Homme" else 1
    age_order = ("M1", "M2", "M3", "F").index(age_band)
    weight_order = WEIGHTS_BY_CATEGORY[age_band].index(weight)
    return sex_order, age_order, weight_order, level


def display_weight(category: Category) -> str:
    sex, age_band, weight, level = category
    if (
        sex == "Homme"
        and age_band == "M3"
        and weight == "-68"
        and level == "intermediaire ( +1,5 ans & -4 ans)"
    ):
        return "Open -68/-75 kg"
    return f"{weight} kg"


def round_label(round_index: int, round_count: int) -> str:
    if round_count == 1:
        return "Finale"
    if round_count == 2:
        return "Demi-finales"
    if round_count == 4:
        return "Quarts de finale"
    if round_count == 8:
        return "Huitièmes de finale"
    if round_count == 16:
        return "Seizièmes de finale"
    if round_index == 0:
        return "1er tour"
    return "Tour suivant"


def combat_numbers(rounds: Sequence[Sequence[Match]]) -> dict[tuple[int, int], int]:
    """Number real bouts sequentially across all rounds in one category."""
    numbers: dict[tuple[int, int], int] = {}
    next_number = 1
    for round_index, round_matches in enumerate(rounds):
        for match in round_matches:
            if match.is_bye:
                continue
            numbers[(round_index, match.index)] = next_number
            next_number += 1
    return numbers


def category_bout_id(category: Category, round_index: int, match_index: int) -> str:
    return f"{category_filename(category)}-{round_index + 1}-{match_index + 1}"


def global_combat_numbers(
    category: Category,
    rounds: Sequence[Sequence[Match]],
    global_numbers: dict[str, int] | None,
) -> dict[tuple[int, int], int]:
    if not global_numbers:
        return combat_numbers(rounds)
    return {
        (round_index, match.index): global_numbers[
            category_bout_id(category, round_index, match.index)
        ]
        for round_index, round_matches in enumerate(rounds)
        for match in round_matches
        if not match.is_bye
        and category_bout_id(category, round_index, match.index) in global_numbers
    }


def bracket_layout(rounds: Sequence[Sequence[Match]]) -> tuple[list[tuple[int, str]], int]:
    """Return (round index, side) columns and the final column index."""
    final_index = len(rounds) - 1
    if final_index == 0:
        return [(0, "left")], 0
    if len(rounds[0]) <= 4:
        return [(round_index, "left") for round_index in range(len(rounds))], final_index

    layout: list[tuple[int, str]] = []
    for round_index in range(final_index):
        midpoint = (len(rounds[round_index]) + 1) // 2
        if midpoint:
            layout.append((round_index, "left"))
    layout.append((final_index, "final"))
    for round_index in range(final_index - 1, -1, -1):
        midpoint = (len(rounds[round_index]) + 1) // 2
        if len(rounds[round_index]) - midpoint:
            layout.append((round_index, "right"))
    return layout, final_index


def bracket_height(
    rounds: Sequence[Sequence[Match]],
    card_height: int,
    vertical_gap: int,
) -> float:
    if not rounds or not rounds[0]:
        return float(card_height)
    spacing = card_height + vertical_gap
    return (len(rounds[0]) - 1) * spacing + card_height


def match_y(
    index: int,
    round_index: int,
    card_height: int,
    vertical_gap: int,
) -> float:
    spacing = card_height + vertical_gap
    match_span = 2**round_index
    return (index * match_span + (match_span - 1) / 2) * spacing


def match_participants(
    match: Match,
    round_index: int,
    rounds: Sequence[Sequence[Match]],
    numbers: dict[tuple[int, int], int],
) -> list[str]:
    if match.is_bye:
        return [match.first.display_name if match.first else "À déterminer"]

    participants: list[str] = []
    for slot, fighter in enumerate((match.first, match.second)):
        if fighter is not None:
            participants.append(fighter.display_name)
        elif round_index > 0:
            source_match = rounds[round_index - 1][match.index * 2 + slot]
            source_number = numbers.get((round_index - 1, source_match.index))
            if source_number is None:
                raise ValueError("Combat source manquant pour un participant du tableau.")
            participants.append(f"Vainqueur Combat #{source_number}")
        else:
            participants.append("À déterminer")
    return participants


def fighter_card(
    match: Match,
    number: int | None,
    participants: Sequence[str],
    participant_fighters: Sequence[Fighter | None],
) -> str:
    if match.is_bye:
        heading = "Qualifié d’office"
    else:
        heading = f"COMBAT #{number}" if number is not None else "COMBAT"

    rows = "".join(
        f'<div class="fighter{" pending" if name.startswith(("À déterminer", "Vainqueur")) else ""}">'
        f"{fighter_photo_html(participant_fighters[index])}"
        f"<span>{html.escape(name)}</span></div>"
        for index, name in enumerate(participants)
    )
    return (
        f'<article class="match"><div class="match-heading">{html.escape(heading)}</div>'
        f"{rows}</article>"
    )


def round_robin_matches(fighters: Sequence[Fighter]) -> list[tuple[Fighter, Fighter]]:
    if len(fighters) != 3:
        return []
    first, second, third = fighters
    return [(first, second), (second, third), (first, third)]


def render_round_robin_html(
    fighters: Sequence[Fighter],
    category: Category | None = None,
    global_numbers: dict[str, int] | None = None,
) -> str:
    bout_numbers: dict[int, int] = {}
    if category is not None and global_numbers:
        for index in range(1, 4):
            bout_numbers[index] = global_numbers[
                f"{category_filename(category)}-P{index}"
            ]

    matches = "".join(
        f'<article class="match"><div class="match-heading">'
        f"COMBAT #{bout_numbers.get(index, index)}</div>"
        f'<div class="fighter">{fighter_photo_html(first)}<span>{html.escape(first.display_name)}</span></div>'
        f'<div class="fighter">{fighter_photo_html(second)}<span>{html.escape(second.display_name)}</span></div></article>'
        for index, (first, second) in enumerate(round_robin_matches(fighters), start=1)
    )
    standings = "".join(
        f'<tr><td class="standing-blank"></td><td>{html.escape(fighter.display_name)}</td>'
        '<td class="standing-blank"></td><td class="standing-blank"></td></tr>'
        for fighter in fighters
    )
    return (
        '<section class="round-robin"><h3>Poule unique, chaque combattant rencontre les deux autres</h3>'
        f'<div class="round-robin-matches">{matches}</div>'
        '<table class="standings"><thead><tr><th>Classement</th><th>Combattant</th>'
        '<th>Victoires</th><th>Soumissions</th></tr></thead>'
        f"<tbody>{standings}</tbody></table>"
        '<p class="help">À compléter manuellement : classer d’abord par victoires, puis par soumissions.</p>'
        "</section>"
    )


def public_schedule_bouts(
    grouped: dict[Category, list[Fighter]],
    bracket_by_category: dict[Category, list[list[Match]]],
) -> list[dict]:
    bouts: list[dict] = []

    def add_bout(
        bout_id: str,
        category_name: str,
        phase: str,
        first: str,
        second: str,
        dependencies: Sequence[str] = (),
    ) -> None:
        bouts.append(
            {
                "id": bout_id,
                "categorie": category_name,
                "phase": phase,
                "combattant_1": first,
                "combattant_2": second,
                "dependances": list(dependencies),
            }
        )

    for category, fighters in grouped.items():
        sex, age_band, weight, level = category
        level_text = "" if level == "unique" else f" | {fighters[0].level}"
        category_name = (
            f"{sex} | {CATEGORY_LABELS[age_band]} | {display_weight(category)}"
            f"{level_text}"
        )

        if len(fighters) == 3:
            for index, (first, second) in enumerate(
                round_robin_matches(fighters),
                start=1,
            ):
                add_bout(
                    f"{category_filename(category)}-P{index}",
                    category_name,
                    "Poule",
                    first.display_name,
                    second.display_name,
                )
            continue

        if len(fighters) == 2:
            add_bout(
                f"{category_filename(category)}-F",
                category_name,
                "Finale",
                fighters[0].display_name,
                fighters[1].display_name,
            )
            continue

        if len(fighters) == 1:
            continue

        rounds = bracket_by_category[category]
        bout_ids: dict[tuple[int, int], str] = {}

        for round_index, round_matches in enumerate(rounds):
            if len(round_matches) == 1:
                phase = "Finale"
            elif len(round_matches) == 2:
                phase = "Demi"
            elif len(round_matches) == 4:
                phase = "Quart"
            else:
                phase = "Tour"

            for match in round_matches:
                if match.is_bye:
                    continue
                bout_id = f"{category_filename(category)}-{round_index + 1}-{match.index + 1}"
                bout_ids[(round_index, match.index)] = bout_id

                dependencies: list[str] = []
                if round_index > 0:
                    for source_index in (match.index * 2, match.index * 2 + 1):
                        source_id = bout_ids.get((round_index - 1, source_index))
                        if source_id is not None:
                            dependencies.append(source_id)

                def participant_name(
                    fighter: Fighter | None,
                    slot_index: int,
                ) -> str:
                    if fighter is not None:
                        return fighter.display_name
                    if round_index == 0:
                        return "À déterminer"
                    source_index = match.index * 2 + slot_index
                    source_id = bout_ids.get((round_index - 1, source_index))
                    if source_id is not None:
                        return f"Vainqueur du combat {source_id}"
                    return "À déterminer"

                add_bout(
                    bout_id,
                    category_name,
                    phase,
                    participant_name(match.first, 0),
                    participant_name(match.second, 1),
                    dependencies,
                )

        has_third_place = len(fighters) >= 4 and len(rounds) >= 2 and len(rounds[-2]) == 2
        if has_third_place:
            semi_ids = [
                bout_ids[(len(rounds) - 2, match.index)]
                for match in rounds[-2]
                if (len(rounds) - 2, match.index) in bout_ids
            ]
            add_bout(
                f"{category_filename(category)}-B",
                category_name,
                "Petite Finale",
                f"Perdant du combat {semi_ids[0]}" if semi_ids else "Perdant de la demi-finale 1",
                f"Perdant du combat {semi_ids[1]}" if len(semi_ids) > 1 else "Perdant de la demi-finale 2",
                semi_ids,
            )

    return bouts


def schedule_category_badge(category_text: str) -> str:
    for marker in (" | ", " · "):
        if marker in category_text:
            parts = [part.strip() for part in category_text.split(marker)]
            break
    else:
        parts = [category_text]
    weight = next(
        (part for part in parts if re.search(r"(?:[+-]\d+|Open)", part)),
        "",
    )
    level = next(
        (part for part in parts if "Débutant" in part or "Intermédiaire" in part),
        "",
    )
    label = weight
    if "Débutant" in level:
        label = f"{weight} Déb."
    elif "Intermédiaire" in level:
        label = f"{weight} Int."
    return html.escape(label or category_text[:24])


def render_schedule_html(
    frame: pd.DataFrame,
    fighters: Sequence[Fighter],
) -> str:
    fighter_by_name = {fighter.display_name: fighter for fighter in fighters}
    rows: list[str] = []
    for row in frame.itertuples(index=False, name=None):
        values = dict(zip(frame.columns, row))
        number = int(values["Ordre (#)"])
        phase = str(values["Phase"])
        accent = phase in ("Petite Finale", "Finale")
        badge_class = " accent" if accent else ""

        def fighter_html(name: object) -> str:
            text = str(name)
            fighter = fighter_by_name.get(text)
            if fighter is not None and fighter.photo_data is not None:
                return (
                    f'<span class="schedule-fighter">'
                    f"{fighter_photo_html(fighter)}"
                    f"<span>{html.escape(text)}</span></span>"
                )
            parts = text.strip().split()
            initials = "".join(part[0] for part in parts[:2]).upper() if parts else "?"
            return (
                f'<span class="schedule-fighter">'
                f'<span class="schedule-avatar">{html.escape(initials)}</span>'
                f"<span>{html.escape(text)}</span></span>"
            )

        rows.append(
            "<tr>"
            f'<td><span class="schedule-number{badge_class}">#{number}</span></td>'
            f'<td class="schedule-time">{html.escape(str(values["Heure estimée"]))}</td>'
            f'<td><span class="schedule-category">{schedule_category_badge(str(values["Catégorie"]))}</span>'
            f'<span class="schedule-phase">{html.escape(phase)}</span></td>'
            f"<td>{fighter_html(values['Combattant A'])}</td>"
            '<td class="schedule-vs">VS</td>'
            f"<td>{fighter_html(values['Combattant B'])}</td>"
            "</tr>"
        )
    return (
        '<div class="schedule-table-wrap"><table class="schedule-table">'
        "<thead><tr><th>N°</th><th>Heure</th><th>Catégorie &amp; Phase</th>"
        "<th>Combattant A</th><th>VS</th><th>Combattant B</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_bracket_html(
    fighters: Sequence[Fighter],
    rounds: Sequence[Sequence[Match]],
    category: Category | None = None,
    global_numbers: dict[str, int] | None = None,
) -> str:
    if len(fighters) == 3:
        return render_round_robin_html(fighters, category, global_numbers)
    if len(fighters) == 2:
        direct_number = (
            global_numbers.get(f"{category_filename(category)}-F")
            if category is not None and global_numbers
            else 1
        )
        return (
            '<p class="help">Finale directe : le vainqueur remporte l’or, le perdant l’argent.</p>'
            '<div class="bracket-scroll"><div class="bracket-canvas" style="width:480px;height:140px">'
            '<article class="match" style="position:absolute;left:145px;top:34px;width:190px;height:88px">'
            f'<div class="match-heading">COMBAT #{direct_number}</div>'
            f'<div class="fighter">{fighter_photo_html(fighters[0])}<span>{html.escape(fighters[0].display_name)}</span></div>'
            f'<div class="fighter">{fighter_photo_html(fighters[1])}<span>{html.escape(fighters[1].display_name)}</span></div>'
            '</article></div></div>'
        )
    if len(fighters) == 1:
        return (
            '<div class="solo-card"><strong>Un seul combattant inscrit</strong>'
            f'{fighter_photo_html(fighters[0])}<span>{html.escape(fighters[0].display_name)}</span></div>'
        )

    layout, final_index = bracket_layout(rounds)
    single_left = len(rounds[0]) <= 4
    numbers = (
        global_combat_numbers(category, rounds, global_numbers)
        if category is not None
        else combat_numbers(rounds)
    )
    has_third_place = len(fighters) >= 4 and len(rounds) >= 2 and len(rounds[-2]) == 2
    if has_third_place:
        bronze_id = f"{category_filename(category)}-B" if category is not None else ""
        third_place_number = (
            global_numbers.get(bronze_id)
            if global_numbers and bronze_id in global_numbers
            else len(numbers) + 1
        )
    else:
        third_place_number = None
    card_width, card_height, gap = 190, 88, 74
    column_width = card_width + gap
    vertical_gap = 28
    top_padding = 34
    tree_height = bracket_height(rounds, card_height, vertical_gap)
    total_width = len(layout) * column_width
    total_height = top_padding + tree_height
    position_by_key: dict[tuple[int, int], tuple[float, float]] = {}
    columns: list[str] = []

    for column_index, (round_index, side) in enumerate(layout):
        midpoint = (len(rounds[round_index]) + 1) // 2
        matches = [
            match
            for match in rounds[round_index]
            if single_left
            or side == "final"
            or (side == "left" and match.index < midpoint)
            or (side == "right" and match.index >= midpoint)
        ]
        cards: list[str] = []
        for match in matches:
            visual_index = (
                len(rounds[round_index]) - 1 - match.index
                if side == "right"
                else match.index
            )
            y = top_padding + match_y(
                visual_index, round_index, card_height, vertical_gap
            )
            x = column_index * column_width
            position_by_key[(round_index, match.index)] = (x, y)
            match_number = numbers.get((round_index, match.index))
            if match_number is None and not match.is_bye:
                raise ValueError("Numéro de combat manquant dans le tableau.")
            card = fighter_card(
                match,
                match_number,
                match_participants(match, round_index, rounds, numbers),
                [match.first] if match.is_bye else [match.first, match.second],
            )
            cards.append(
                f'<div class="match-position" style="left:{x}px;top:{y}px;'
                f'width:{card_width}px;height:{card_height}px">{card}</div>'
            )
        columns.append(
            f'<div class="round-label" style="left:{column_index * column_width}px">'
            f"{html.escape(round_label(round_index, len(rounds[round_index])))}</div>"
        )
        columns.extend(cards)

    paths: list[str] = []
    for round_index in range(final_index):
        target_midpoint = (len(rounds[round_index + 1]) + 1) // 2
        source_midpoint = (len(rounds[round_index]) + 1) // 2
        for target in rounds[round_index + 1]:
            source_indexes = (target.index * 2, target.index * 2 + 1)
            target_position = position_by_key.get((round_index + 1, target.index))
            if target_position is None:
                continue
            target_x, target_y = target_position
            target_is_final = round_index + 1 == final_index
            target_side = (
                "left" if target.index < target_midpoint else "right"
            ) if not target_is_final else None
            target_center_y = target_y + card_height / 2

            for source_index in source_indexes:
                source_position = position_by_key.get((round_index, source_index))
                if source_position is None:
                    continue
                source_x, source_y = source_position
                source_side = "left" if source_index < source_midpoint else "right"
                flows_right = (
                    True
                    if single_left
                    else target_side == "left" if target_side else source_side == "left"
                )
                if target_is_final and not single_left:
                    flows_right = source_side == "left"
                source_anchor = source_x + (card_width if flows_right else 0)
                target_anchor = target_x + (0 if flows_right else card_width)
                source_center_y = source_y + card_height / 2
                junction_x = (source_anchor + target_anchor) / 2
                path_data = (
                    f"M {source_anchor} {source_center_y} H {junction_x} "
                    f"V {target_center_y} H {target_anchor}"
                )
                paths.append(
                    f'<path d="{path_data}" fill="none" stroke="#b7b7ae" '
                    'stroke-width="2" stroke-linecap="square" stroke-linejoin="round"/>'
                )
                paths.append(
                    f'<circle cx="{junction_x}" cy="{target_center_y}" r="3.5" '
                    'fill="#f5d900" stroke="#927f00" stroke-width="1"/>'
                )

    svg = (
        f'<svg class="bracket-svg" width="{total_width}" height="{total_height}" '
        f'viewBox="0 0 {total_width} {total_height}" aria-hidden="true">'
        f'{"".join(paths)}</svg>'
    )
    third_place = ""
    if has_third_place and third_place_number is not None:
        semi_round_index = len(rounds) - 2
        semi_1_id = (
            category_bout_id(category, semi_round_index, 0)
            if category is not None
            else ""
        )
        semi_2_id = (
            category_bout_id(category, semi_round_index, 1)
            if category is not None
            else ""
        )
        semi_1_number = global_numbers.get(semi_1_id) if global_numbers else None
        semi_2_number = global_numbers.get(semi_2_id) if global_numbers else None
        third_place = (
            '<section class="third-place"><h3>Petite finale, 3e place</h3>'
            f'<article class="match"><div class="match-heading">'
            f"COMBAT #{third_place_number} POUR LE BRONZE</div>"
            f'<div class="fighter pending">'
            f'{html.escape(f"Perdant Combat #{semi_1_number}" if semi_1_number else "Perdant demi-finale 1")}'
            '</div>'
            f'<div class="fighter pending">'
            f'{html.escape(f"Perdant Combat #{semi_2_number}" if semi_2_number else "Perdant demi-finale 2")}'
            '</div></article></section>'
        )
    direct_final_note = (
        '<p class="help">Finale directe : le vainqueur remporte l’or, le perdant l’argent.</p>'
        if len(fighters) == 2
        else ""
    )
    return (
        direct_final_note
        + '<div class="bracket-scroll"><div class="bracket-canvas" '
        f'style="width:{total_width}px;height:{total_height}px">'
        f"{svg}{''.join(columns)}</div></div>{third_place}"
    )


def build_pdf(
    grouped: dict[Category, list[Fighter]],
    bracket_by_category: dict[Category, list[list[Match]]],
    global_numbers: dict[str, int] | None = None,
) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(output, pagesize=(page_width, page_height), pageCompression=1)
    pdf.setTitle("TFT Série No-GI #1 - Tableaux de compétition")
    card_width, card_height, gap = 150, 56, 44
    accent = HexColor("#f5d900")
    ink = HexColor("#111111")
    muted = HexColor("#686868")
    connector = HexColor("#b7b7ae")

    for category, fighters in grouped.items():
        rounds = bracket_by_category[category]
        numbers = (
            global_combat_numbers(category, rounds, global_numbers)
            if global_numbers
            else combat_numbers(rounds)
        )
        is_round_robin = len(fighters) == 3
        layout, final_index = bracket_layout(rounds) if rounds else ([], -1)
        single_left = bool(rounds and len(rounds[0]) <= 4)
        has_third_place = len(fighters) >= 4 and len(rounds) >= 2 and len(rounds[-2]) == 2
        if has_third_place:
            bronze_id = f"{category_filename(category)}-B"
            third_place_number = (
                global_numbers.get(bronze_id)
                if global_numbers and bronze_id in global_numbers
                else len(numbers) + 1
            )
        else:
            third_place_number = None
        total_width = (
            740
            if len(fighters) in (2, 3)
            else len(layout) * (card_width + gap) + 40
        )
        pdf_vertical_gap = 22
        tree_height = bracket_height(rounds, 56, pdf_vertical_gap)
        sex, age_band, weight, level = category
        level_text = "" if level == "unique" else f" | {fighters[0].level}"
        title = (
            f"{sex} | {CATEGORY_LABELS[age_band]} | {display_weight(category)}"
            f"{level_text} | {len(fighters)} combattant"
            f"{'s' if len(fighters) > 1 else ''}"
        )
        title_lines = wrap_pdf_text(title, "Helvetica-Bold", 15, total_width - 40)
        header_height = 50 + 16 * len(title_lines)
        if len(fighters) == 3:
            total_height = 500
        elif len(fighters) == 2:
            total_height = 460
        else:
            bottom_padding = 150 if has_third_place else 60
            total_height = max(
                440,
                header_height + 26 + tree_height + bottom_padding,
            )
        page_margin = 36
        scale = min(
            1,
            (page_width - page_margin * 2) / total_width,
            (page_height - page_margin * 2) / total_height,
        )
        pdf.setPageSize((page_width, page_height))
        pdf.saveState()
        pdf.translate(
            (page_width - total_width * scale) / 2,
            (page_height - total_height * scale) / 2,
        )
        pdf.scale(scale, scale)

        pdf.setFillColor(ink)
        pdf.rect(0, total_height - header_height, total_width, header_height, fill=1, stroke=0)
        pdf.setFillColor(accent)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(20, total_height - 24, "TFT SÉRIE NO-GI #1")
        pdf.setFillColor(white)
        pdf.setFont("Helvetica-Bold", 15)
        for line_index, line in enumerate(title_lines):
            pdf.drawString(20, total_height - 44 - line_index * 16, line)

        if is_round_robin:
            header_bottom = total_height - header_height
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawCentredString(
                total_width / 2,
                header_bottom - 38,
                "POULE UNIQUE · CHACUN RENCONTRE LES DEUX AUTRES",
            )
            match_bottom = header_bottom - 135
            match_width, match_height = 220, 68
            for index, (first, second) in enumerate(round_robin_matches(fighters), start=1):
                x = 20 + (index - 1) * 240
                pool_number = (
                    global_numbers.get(f"{category_filename(category)}-P{index}")
                    if global_numbers
                    else index
                )
                pdf.setFillColor(white)
                pdf.setStrokeColor(HexColor("#ddddda"))
                pdf.roundRect(x, match_bottom, match_width, match_height, 6, fill=1, stroke=1)
                pdf.setFillColor(accent)
                pdf.roundRect(x, match_bottom + match_height - 21, match_width, 21, 6, fill=1, stroke=0)
                pdf.rect(x, match_bottom + match_height - 21, match_width, 6, fill=1, stroke=0)
                pdf.setFillColor(ink)
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(x + 10, match_bottom + match_height - 14, f"COMBAT #{pool_number}")
                pdf.setFont("Helvetica", 8.5)
                draw_pdf_fighter(
                    pdf, ImageReader, first, x + 10, match_bottom + 25, 16
                )
                first_name_x = x + (32 if first.photo_data else 10)
                pdf.drawString(first_name_x, match_bottom + 29, first.display_name[:32])
                pdf.setStrokeColor(HexColor("#ecece8"))
                pdf.line(x + 8, match_bottom + 24, x + match_width - 8, match_bottom + 24)
                draw_pdf_fighter(
                    pdf, ImageReader, second, x + 10, match_bottom + 4, 16
                )
                second_name_x = x + (32 if second.photo_data else 10)
                pdf.drawString(second_name_x, match_bottom + 8, second.display_name[:32])

            table_top = match_bottom - 32
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(20, table_top, "CLASSEMENT À COMPLÉTER")
            table_x = 20
            table_width = total_width - 40
            table_header_y = table_top - 28
            column_widths = (42, 265, 130, 140, table_width - 577)
            headings = ("Rang", "Combattant", "Victoires", "Soumissions", "Notes")
            pdf.setFillColor(HexColor("#f2f2ef"))
            pdf.setStrokeColor(HexColor("#ddddda"))
            pdf.rect(table_x, table_header_y, table_width, 24, fill=1, stroke=1)
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 8)
            column_left = table_x
            for width, heading in zip(column_widths, headings):
                pdf.drawString(column_left + 7, table_header_y + 8, heading)
                column_left += width

            row_height = 31
            for index, fighter in enumerate(fighters, start=1):
                row_y = table_header_y - index * row_height
                pdf.setFillColor(white)
                pdf.setStrokeColor(HexColor("#ddddda"))
                pdf.rect(table_x, row_y, table_width, row_height, fill=1, stroke=1)
                pdf.setFillColor(ink)
                pdf.setFont("Helvetica", 8.5)
                draw_pdf_fighter(
                    pdf,
                    ImageReader,
                    fighter,
                    table_x + column_widths[0] + 7,
                    row_y + 7,
                    17,
                )
                name_x = table_x + column_widths[0] + (29 if fighter.photo_data else 7)
                pdf.drawString(
                    name_x,
                    row_y + 11,
                    fighter.display_name[:36],
                )
                column_left = table_x + column_widths[0] + column_widths[1]
                for width in column_widths[2:]:
                    pdf.rect(column_left, row_y, width, row_height, fill=0, stroke=1)
                    column_left += width

            pdf.setFillColor(muted)
            pdf.setFont("Helvetica", 8)
            pdf.drawCentredString(
                total_width / 2,
                44,
                "Classer par victoires, puis par soumissions.",
            )
            pdf.restoreState()
            pdf.showPage()
            continue

        if len(fighters) == 2:
            header_bottom = total_height - header_height
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 13)
            pdf.drawCentredString(total_width / 2, header_bottom - 52, "FINALE DIRECTE")
            card_width_direct, card_height_direct = 500, 106
            card_x = (total_width - card_width_direct) / 2
            card_y = header_bottom - 220
            pdf.setFillColor(white)
            pdf.setStrokeColor(HexColor("#ddddda"))
            pdf.roundRect(
                card_x,
                card_y,
                card_width_direct,
                card_height_direct,
                7,
                fill=1,
                stroke=1,
            )
            pdf.setFillColor(accent)
            pdf.roundRect(
                card_x,
                card_y + card_height_direct - 27,
                card_width_direct,
                27,
                7,
                fill=1,
                stroke=0,
            )
            pdf.rect(
                card_x,
                card_y + card_height_direct - 27,
                card_width_direct,
                7,
                fill=1,
                stroke=0,
            )
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 10)
            direct_number = (
                global_numbers.get(f"{category_filename(category)}-F")
                if global_numbers
                else 1
            )
            pdf.drawString(card_x + 14, card_y + card_height_direct - 18, f"COMBAT #{direct_number}")
            pdf.setFont("Helvetica-Bold", 11)
            draw_pdf_fighter(
                pdf, ImageReader, fighters[0], card_x + 16, card_y + 45, 18
            )
            first_name_x = card_x + (40 if fighters[0].photo_data else 16)
            pdf.drawString(first_name_x, card_y + 53, fighters[0].display_name[:48])
            pdf.setStrokeColor(HexColor("#ecece8"))
            pdf.line(card_x + 12, card_y + 43, card_x + card_width_direct - 12, card_y + 43)
            draw_pdf_fighter(
                pdf, ImageReader, fighters[1], card_x + 16, card_y + 10, 18
            )
            second_name_x = card_x + (40 if fighters[1].photo_data else 16)
            pdf.drawString(second_name_x, card_y + 18, fighters[1].display_name[:48])
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica", 9)
            pdf.drawCentredString(
                total_width / 2,
                card_y - 42,
                "Vainqueur : médaille d’or  ·  Perdant : médaille d’argent",
            )
            pdf.restoreState()
            pdf.showPage()
            continue

        if len(fighters) == 1:
            pdf.setFillColor(accent)
            pdf.roundRect(30, total_height - 125, 250, 42, 6, fill=1, stroke=0)
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(42, total_height - 100, "UN SEUL COMBATTANT INSCRIT")
            pdf.setFont("Helvetica", 11)
            draw_pdf_fighter(
                pdf, ImageReader, fighters[0], 42, total_height - 125, 30
            )
            name_x = 80 if fighters[0].photo_data else 42
            name_y = total_height - (107 if fighters[0].photo_data else 117)
            pdf.drawString(name_x, name_y, fighters[0].display_name)
            pdf.restoreState()
            pdf.showPage()
            continue

        x_positions: dict[tuple[int, int], float] = {}
        y_positions: dict[tuple[int, int], float] = {}
        start_x = 20
        for column_index, (round_index, side) in enumerate(layout):
            midpoint = (len(rounds[round_index]) + 1) // 2
            for match in rounds[round_index]:
                if not single_left and side == "left" and match.index >= midpoint:
                    continue
                if not single_left and side == "right" and match.index < midpoint:
                    continue
                x = start_x + column_index * (card_width + gap)
                visual_index = (
                    len(rounds[round_index]) - 1 - match.index
                    if side == "right"
                    else match.index
                )
                top_y = total_height - header_height - 26 - match_y(
                    visual_index, round_index, card_height, pdf_vertical_gap
                )
                y = top_y - card_height
                x_positions[(round_index, match.index)] = x
                y_positions[(round_index, match.index)] = y
                pdf.setFillColor(muted)
                pdf.setFont("Helvetica-Bold", 8)
                pdf.drawString(
                    x,
                    total_height - header_height - 12,
                    round_label(round_index, len(rounds[round_index])).upper(),
                )

        for round_index in range(final_index):
            source_midpoint = (len(rounds[round_index]) + 1) // 2
            target_midpoint = (len(rounds[round_index + 1]) + 1) // 2
            for target in rounds[round_index + 1]:
                target_x = x_positions.get((round_index + 1, target.index))
                target_y = y_positions.get((round_index + 1, target.index))
                if target_x is None or target_y is None:
                    continue
                target_center = target_y + card_height / 2
                target_side = "left" if target.index < target_midpoint else "right"
                for source_index in (target.index * 2, target.index * 2 + 1):
                    source_x = x_positions.get((round_index, source_index))
                    source_y = y_positions.get((round_index, source_index))
                    if source_x is None or source_y is None:
                        continue
                    source_side = "left" if source_index < source_midpoint else "right"
                    flows_right = (
                        True
                        if single_left
                        else source_side == "left"
                        if round_index + 1 == final_index
                        else target_side == "left"
                    )
                    source_anchor = source_x + (card_width if flows_right else 0)
                    target_anchor = target_x + (0 if flows_right else card_width)
                    source_center = source_y + card_height / 2
                    junction_x = (source_anchor + target_anchor) / 2
                    pdf.setStrokeColor(connector)
                    pdf.setLineWidth(1.2)
                    pdf.line(source_anchor, source_center, junction_x, source_center)
                    pdf.line(junction_x, source_center, junction_x, target_center)
                    pdf.line(junction_x, target_center, target_anchor, target_center)
                    pdf.setFillColor(accent)
                    pdf.circle(junction_x, target_center, 2.5, fill=1, stroke=0)

        for (round_index, match_index), x in x_positions.items():
            y = y_positions[(round_index, match_index)]
            match = rounds[round_index][match_index]
            pdf.setFillColor(white)
            pdf.setStrokeColor(HexColor("#ddddda"))
            pdf.roundRect(x, y, card_width, card_height, 5, fill=1, stroke=1)
            pdf.setFillColor(accent if match.is_bye else HexColor("#f4f4f1"))
            pdf.roundRect(x, y + card_height - 17, card_width, 17, 5, fill=1, stroke=0)
            pdf.rect(x, y + card_height - 17, card_width, 5, fill=1, stroke=0)
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 7.5)
            match_number = numbers.get((round_index, match_index))
            if not match.is_bye and match_number is None:
                raise ValueError("Numéro de combat manquant dans le tableau PDF.")
            heading = (
                "QUALIFIÉ D’OFFICE"
                if match.is_bye
                else f"COMBAT #{match_number}"
            )
            pdf.drawString(x + 7, y + card_height - 12, heading)
            pdf.setStrokeColor(HexColor("#e8e8e4"))
            pdf.line(x, y + 19, x + card_width, y + 19)
            names = match_participants(match, round_index, rounds, numbers)
            participant_fighters = (
                [match.first] if match.is_bye else [match.first, match.second]
            )
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 8.5)
            for name_index, name in enumerate(names):
                line_y = y + card_height - 31 - name_index * 17
                fighter = participant_fighters[name_index]
                draw_pdf_fighter(
                    pdf,
                    ImageReader,
                    fighter,
                    x + 5,
                    line_y - 4,
                    14,
                )
                pdf.drawString(x + (24 if fighter and fighter.photo_data else 7), line_y, name[:23])

        if has_third_place and third_place_number is not None:
            bronze_x, bronze_y, bronze_width, bronze_height = (
                total_width / 2 - card_width / 2,
                35,
                card_width,
                card_height,
            )
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawCentredString(total_width / 2, 104, "PETITE FINALE, 3E PLACE")
            pdf.setFillColor(white)
            pdf.setStrokeColor(HexColor("#ddddda"))
            pdf.roundRect(bronze_x, bronze_y, bronze_width, bronze_height, 5, fill=1, stroke=1)
            pdf.setFillColor(accent)
            pdf.roundRect(
                bronze_x,
                bronze_y + bronze_height - 17,
                bronze_width,
                17,
                5,
                fill=1,
                stroke=0,
            )
            pdf.rect(bronze_x, bronze_y + bronze_height - 17, bronze_width, 5, fill=1, stroke=0)
            pdf.setFillColor(ink)
            pdf.setFont("Helvetica-Bold", 7.5)
            pdf.drawString(
                bronze_x + 7,
                bronze_y + bronze_height - 12,
                f"COMBAT #{third_place_number} POUR LE BRONZE",
            )
            pdf.setFont("Helvetica", 8.5)
            semi_round_index = len(rounds) - 2
            semi_1_id = category_bout_id(category, semi_round_index, 0)
            semi_2_id = category_bout_id(category, semi_round_index, 1)
            semi_1_number = global_numbers.get(semi_1_id) if global_numbers else None
            semi_2_number = global_numbers.get(semi_2_id) if global_numbers else None
            pdf.drawString(
                bronze_x + 7,
                bronze_y + 24,
                f"Perdant Combat #{semi_1_number}" if semi_1_number else "Perdant demi-finale 1",
            )
            pdf.drawString(
                bronze_x + 7,
                bronze_y + 8,
                f"Perdant Combat #{semi_2_number}" if semi_2_number else "Perdant demi-finale 2",
            )

        pdf.restoreState()
        pdf.showPage()

    pdf.save()
    return output.getvalue()


def render_category(
    category: Category,
    fighters: list[Fighter],
    global_numbers: dict[str, int] | None = None,
) -> None:
    sex, age_band, weight, normalized_level = category
    level_text = "" if normalized_level == "unique" else f" · {fighters[0].level}"
    count_text = f"{len(fighters)} combattant{'s' if len(fighters) > 1 else ''}"
    title = f"{sex} · {CATEGORY_LABELS[age_band]} · {display_weight(category)}{level_text} · {count_text}"
    rounds = st.session_state["tournament_brackets"][category]
    with st.container(key=f"print-category-{category_filename(category)}"):
        with st.expander(title, expanded=False):
            if len(fighters) == 2:
                st.caption("Finale directe : le vainqueur remporte l’or, le perdant l’argent.")
            elif len(fighters) == 3:
                st.caption("Poule Round Robin : les trois combattants s’affrontent chacun une fois.")
            st.markdown(
                render_bracket_html(fighters, rounds, category, global_numbers),
                unsafe_allow_html=True,
            )
            category_pdf = build_pdf(
                {category: fighters},
                {category: rounds},
                global_numbers,
            )
            st.download_button(
                "Enregistrer cette catégorie en PDF",
                data=category_pdf,
                file_name=f"tft-serie-no-gi-1_{category_filename(category)}.pdf",
                mime="application/pdf",
                key=f"category-pdf-{category_filename(category)}",
                on_click="ignore",
                width="stretch",
            )


def category_filename(category: Category) -> str:
    return "-".join(
        part
        for part in (
            normalize(category[0]),
            normalize(CATEGORY_LABELS[category[1]]),
            normalize(category[2]),
            normalize(category[3]),
        )
        if part and part != "unique"
    )


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #f4f4f1; color: #171717; }
        [data-testid="stToolbar"], #MainMenu, footer { display:none !important; }
        .block-container { max-width: 1440px; padding-top: 2rem; }
        .hero {
          padding: 1.5rem 2rem 1.75rem;
          border-radius: 16px;
          background: #101010;
          color: white;
          border-bottom: 5px solid #f5d900;
          margin-bottom: 1.5rem;
        }
        .hero-brand { display:flex; align-items:center; gap:1rem; margin-bottom:1.2rem; }
        .hero-brand img { width:58px; height:58px; object-fit:contain; }
        .hero-brand span, .hero-kicker {
          color:#f5d900; font-size:.78rem; font-weight:800;
          letter-spacing:.16em; text-transform:uppercase;
        }
        .hero h1 { margin:.25rem 0; font-size:clamp(2rem,5vw,3.4rem); line-height:1; }
        .hero p { color:#c7c7c7; margin:.6rem 0 0; }
        [data-testid="stMetric"] {
          padding:1rem; background:#ffffff; border:1px solid #deded9;
          border-radius:10px;
        }
        div[data-testid="stExpander"] {
          background:#ffffff; border:1px solid #deded9; border-radius:10px;
          margin-bottom:.65rem;
        }
        .bracket-scroll { overflow-x:auto; padding:20px 14px 28px; }
        .bracket-canvas { position:relative; min-width:max-content; }
        .bracket-svg { position:absolute; left:0; top:0; z-index:0; overflow:visible; }
        .round-label {
          position:absolute; top:0; width:190px; color:#77776f;
          font-size:.7rem; font-weight:800; letter-spacing:.08em;
          text-transform:uppercase; white-space:nowrap;
        }
        .match-position { position:absolute; z-index:1; }
        .match {
          box-sizing:border-box; height:100%; overflow:hidden; border:1px solid #deded9;
          border-radius:8px; background:#ffffff;
          box-shadow:0 2px 8px #1111110b;
        }
        .match-heading {
          box-sizing:border-box; height:27px; padding:6px 9px; background:#f2f2ef;
          color:#66665e; font-size:.68rem; font-weight:800;
          letter-spacing:.05em; text-transform:uppercase;
        }
        .match:has(.fighter:only-child) .match-heading { background:#fff5bf; color:#716000; }
        .fighter {
          box-sizing:border-box; display:flex; align-items:center; gap:7px;
          height:29px; padding:3px 8px; border-top:1px solid #ecece8;
          color:#171717; font-size:.76rem; font-weight:650; overflow:hidden;
        }
        .fighter-avatar {
          flex:0 0 22px; width:22px; height:22px; border-radius:50%;
          object-fit:cover; background:#ecece8;
        }
        .schedule-table-wrap { width:100%; overflow-x:auto; }
        .schedule-table {
          width:100%; border-collapse:separate; border-spacing:0;
          border:1px solid #deded9; border-radius:8px; overflow:hidden;
          font-size:.78rem;
        }
        .schedule-table th {
          background:#111111; color:#ffffff; text-align:left;
          padding:9px 8px; font-size:.72rem; letter-spacing:.04em;
        }
        .schedule-table td {
          padding:8px; border-top:1px solid #ecece8;
          vertical-align:middle; background:#ffffff;
          break-inside:avoid; page-break-inside:avoid;
        }
        .schedule-table tbody tr:nth-child(even) td { background:#f7f7f4; }
        .schedule-number {
          display:inline-flex; justify-content:center; align-items:center;
          min-width:38px; height:26px; padding:0 7px; border-radius:5px;
          background:#111111; color:#F5C518; font-weight:900;
        }
        .schedule-number.accent { background:#F5C518; color:#111111; }
        .schedule-time { font-weight:800; white-space:nowrap; }
        .schedule-category {
          display:inline-block; max-width:100%; margin-right:6px;
          padding:3px 6px; border-radius:4px; background:#ecece8;
          font-size:.68rem; font-weight:800;
        }
        .schedule-phase { color:#686868; white-space:nowrap; }
        .schedule-vs { text-align:center; color:#686868; font-weight:900; }
        .schedule-fighter {
          display:flex; align-items:center; gap:6px;
          min-width:150px; font-weight:650;
        }
        .schedule-fighter .fighter-avatar {
          flex-basis:24px; width:24px; height:24px;
        }
        .schedule-avatar {
          display:inline-flex; flex:0 0 24px; width:24px; height:24px;
          align-items:center; justify-content:center;
          border-radius:50%; background:#E5E7EB; color:#686868;
          font-size:.68rem; font-weight:900;
        }
        .fighter span {
          min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
        }
        .fighter.pending {
          color:#77776f; background:#f8f8f6; font-style:italic;
        }
        .round-robin { width:100%; max-width:100%; }
        .round-robin-matches {
          display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
          gap:12px; margin:12px 0 20px;
        }
        .round-robin-matches .match { height:88px; min-width:0; }
        .standings { width:100%; border-collapse:collapse; table-layout:fixed; }
        .standings th, .standings td {
          padding:8px 10px; border:1px solid #deded9; text-align:left;
        }
        .standings th {
          background:#f2f2ef; color:#171717; font-size:.78rem;
        }
        .standings td { height:34px; }
        .standings th:first-child, .standings td:first-child { width:14%; }
        .standings th:nth-child(2), .standings td:nth-child(2) { width:42%; }
        .standings th:nth-child(3), .standings td:nth-child(3),
        .standings th:nth-child(4), .standings td:nth-child(4) { width:22%; }
        .solo-card {
          display:flex; flex-direction:column; gap:8px; max-width:280px;
          padding:14px; border:1px solid #e3d98e; border-radius:8px;
          background:#fff8cf;
        }
        .solo-card strong { color:#776500; font-size:.75rem; text-transform:uppercase; }
        .solo-card span { font-weight:700; }
        .solo-card .fighter-avatar { width:64px; height:64px; flex-basis:64px; }
        @media print {
          @page { size: landscape; margin: 10mm; }
          .stApp { background:white; }
          [data-testid="stSidebar"], header, footer,
          [data-testid="stFileUploader"], [data-testid="stDownloadButton"],
          [data-testid="stButton"], [data-testid="stToolbar"] { display:none !important; }
          .block-container { max-width:none; padding:0; }
          .hero { padding:8px 16px 12px; margin:0 0 10px; print-color-adjust:exact; }
          [class*="st-key-print-category-"] {
            box-sizing:border-box; padding-top:18mm;
            break-before:page; page-break-before:always;
          }
          [class*="st-key-print-category-"] details {
            overflow:visible !important; border:0 !important;
          }
          [class*="st-key-print-category-"] details > summary {
            box-sizing:border-box; display:block; height:auto; min-height:0;
            margin:0 0 10px; padding:10px 12px 12px;
            overflow:visible; white-space:normal; line-height:1.4;
            font-size:12px; font-weight:700;
            break-inside:avoid; page-break-inside:avoid;
          }
          [class*="st-key-print-category-"] details > :not(summary) {
            display:block !important; overflow:visible !important;
          }
          [class*="st-key-print-category-"]:has(.round-robin),
          [class*="st-key-print-category-"]:has(.round-robin) details,
          [class*="st-key-print-category-"]:has(.round-robin) .stMarkdown {
            break-inside:avoid; page-break-inside:avoid;
          }
          .round-robin,
          .round-robin-matches,
          .standings {
            break-inside:avoid; page-break-inside:avoid;
          }
          .round-robin { width:100%; }
          .round-robin-matches {
            grid-template-columns:repeat(3,minmax(0,1fr));
            gap:8px; margin:8px 0 14px;
          }
          .round-robin-matches .match {
            box-sizing:border-box; width:100%; height:76px; break-inside:avoid;
          }
          .round-robin .match-heading { height:24px; padding:5px 8px; }
          .round-robin .fighter { height:25px; padding:3px 8px; }
          .round-robin .fighter-avatar { width:18px; height:18px; flex-basis:18px; }
          .standings { width:100%; }
          .standings thead { display:table-header-group; }
          .standings tr { break-inside:avoid; page-break-inside:avoid; }
          .standings th, .standings td { padding:6px 8px; }
          .standings td { height:28px; }
          .bracket-scroll {
            display:flex; justify-content:center; overflow:visible;
            padding:8px 0;
          }
          .bracket-canvas { min-width:0; flex:none; }
          .match { box-shadow:none; break-inside:avoid; page-break-inside:avoid; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="TFT Série No-GI #1",
        page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🥋",
        layout="wide",
    )
    apply_styles()

    logo_html = ""
    if LOGO_PATH.exists():
        encoded_logo = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        logo_html = f'<img src="data:image/png;base64,{encoded_logo}" alt="Tactical Fight Team">'

    st.markdown(
        f"""
        <div class="hero">
          <div class="hero-brand">{logo_html}<span>TFT Série No-GI #1</span></div>
          <div class="hero-kicker">Première édition · 28 novembre 2026 · Toulouse</div>
          <h1>Tableaux de compétition</h1>
          <p>Consultez les tableaux de compétition et le planning de l’événement.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        fighters, public_brackets = load_public_tournament()
    except ValueError as error:
        st.error(error)
        return
    if not fighters:
        st.info(
            "Les tableaux seront publiés le vendredi 27 novembre 2026. "
            "Ils seront affichés ici dès qu’un tirage aura été publié."
        )
        if PLANNING_PATH.exists():
            st.image(
                PLANNING_PATH,
                caption="Planning de l’événement",
                alt="Planning de l’événement TFT Série No-GI du 28 novembre 2026.",
                width="stretch",
            )
        return

    grouped = group_fighters(fighters)
    st.session_state["tournament_brackets"] = public_brackets
    metric_columns = st.columns(3)
    metric_columns[0].metric("Combattants", len(fighters))
    metric_columns[1].metric("Catégories", len(grouped))
    metric_columns[2].metric(
        "Tableaux à plusieurs",
        sum(len(category_fighters) > 1 for category_fighters in grouped.values()),
    )

    with st.expander("Voir la répartition des catégories", expanded=False):
        rows = [
            {
                "Sexe": category[0],
                "Tranche d’âge": CATEGORY_LABELS[category[1]],
                "Poids": display_weight(category),
                "Niveau": "" if category[3] == "unique" else category_fighters[0].level,
                "Combattants": len(category_fighters),
            }
            for category, category_fighters in grouped.items()
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    schedule_bouts = public_schedule_bouts(grouped, public_brackets)
    schedule_frame: pd.DataFrame | None = None
    schedule_report: dict | None = None
    global_numbers: dict[str, int] = {}
    if schedule_bouts:
        try:
            schedule_frame, schedule_report = generate_schedule(
                schedule_bouts,
                nb_tatamis=1,
                seed=20261128,
            )
        except (ValueError, KeyError) as error:
            st.error(f"Impossible de générer l’ordre de passage : {error}")
        else:
            global_numbers = {
                bout_id: position + 1
                for position, bout_id in enumerate(schedule_report["order"])
            }

    st.subheader("Tableaux de compétition")
    for category, category_fighters in grouped.items():
        render_category(category, category_fighters, global_numbers)

    try:
        pdf_bytes = build_pdf(
            grouped,
            st.session_state["tournament_brackets"],
            global_numbers,
        )
    except ImportError:
        st.warning("L’export PDF nécessite reportlab. Vous pouvez imprimer cette page avec Ctrl+P.")
    else:
        st.download_button(
            "Télécharger les tableaux en PDF",
            data=pdf_bytes,
            file_name="TFT_Serie_No-GI_1_tableaux.pdf",
            mime="application/pdf",
            type="primary",
        )

    st.subheader("Ordre de passage")
    if not schedule_bouts:
        st.info("L’ordre de passage sera disponible dès qu’au moins deux combats seront publiés.")
    elif schedule_frame is not None and schedule_report is not None:
            if schedule_report["critical_violations"] != 0:
                st.warning("L’ordre de passage nécessite une vérification avant publication.")

            with st.expander("Afficher l’ordre de passage", expanded=False):
                st.markdown(
                    render_schedule_html(schedule_frame, fighters),
                    unsafe_allow_html=True,
                )
                try:
                    schedule_pdf = export_schedule_pdf_bytes(
                        schedule_frame,
                        schedule_report,
                        {fighter.display_name: fighter for fighter in fighters},
                    )
                except ImportError:
                    st.caption("L’export PDF nécessite reportlab.")
                else:
                    st.download_button(
                        "Télécharger l’ordre en PDF",
                        data=schedule_pdf,
                        file_name="TFT_Serie_No-GI_1_ordre_de_passage.pdf",
                        mime="application/pdf",
                        key="schedule-pdf",
                    )

if __name__ == "__main__":
    main()
