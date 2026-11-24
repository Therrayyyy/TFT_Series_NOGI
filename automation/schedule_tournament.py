from __future__ import annotations

import io
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd


START_TIME = "14:00"
MATCH_DURATION_MINUTES = 6

PHASE_PRIORITY = {
    "Poule": 0,
    "Quart": 0,
    "Finale directe": 0,
    "Demi": 1,
    "Petite Finale": 2,
    "Finale": 3,
}


@dataclass(frozen=True)
class BoutInput:
    id: str
    categorie: str
    phase: str
    combattant_1: str
    combattant_2: str
    dependances: tuple[str, ...] = ()


def normalize_bouts(raw_bouts: Iterable[dict]) -> list[BoutInput]:
    bouts: list[BoutInput] = []
    seen: set[str] = set()
    for raw in raw_bouts:
        required = ("id", "categorie", "phase", "combattant_1", "combattant_2")
        if any(field not in raw for field in required):
            raise ValueError("Chaque combat doit contenir tous les champs obligatoires.")
        bout_id = str(raw["id"]).strip()
        if not bout_id or bout_id in seen:
            raise ValueError("Les identifiants de combat doivent être uniques et non vides.")
        seen.add(bout_id)
        dependencies = tuple(str(item) for item in raw.get("dependances", ()) if str(item))
        bouts.append(
            BoutInput(
                id=bout_id,
                categorie=str(raw["categorie"]),
                phase=str(raw["phase"]),
                combattant_1=str(raw["combattant_1"]),
                combattant_2=str(raw["combattant_2"]),
                dependances=dependencies,
            )
        )
    known_ids = set(seen)
    for bout in bouts:
        unknown = set(bout.dependances) - known_ids
        if unknown:
            raise ValueError("Une dépendance référence un combat inconnu.")
    return bouts


def _phase_priority(phase: str) -> int:
    return PHASE_PRIORITY.get(phase, 1)


def _bout_priority(bout: BoutInput, bouts: Sequence[BoutInput]) -> int:
    return _phase_priority(bout.phase)


def _chronological_group(position: int, nb_tatamis: int) -> int:
    if nb_tatamis == 1:
        return position
    return position // nb_tatamis


def generate_schedule(
    raw_bouts: Iterable[dict],
    nb_tatamis: int = 2,
    seed: int = 20261128,
) -> tuple[pd.DataFrame, dict]:
    if nb_tatamis not in (1, 2):
        raise ValueError("nb_tatamis doit valoir 1 ou 2.")

    bouts = normalize_bouts(raw_bouts)
    if not bouts:
        raise ValueError("La liste des combats est vide.")

    by_id = {bout.id: bout for bout in bouts}
    rng = random.Random(seed)

    def schedule_order() -> list[str]:
        remaining = set(by_id)
        scheduled: list[str] = []
        last_position: dict[str, int] = {}
        category_last_position: dict[str, int] = {}

        while remaining:
            eligible = [
                bout_id
                for bout_id in remaining
                if all(dependency in scheduled for dependency in by_id[bout_id].dependances)
            ]
            if not eligible:
                raise ValueError("Les dépendances de l’arbre ne sont pas acycliques.")
            minimum_phase = min(
                _bout_priority(by_id[bout_id], bouts)
                for bout_id in eligible
            )
            eligible = [
                bout_id
                for bout_id in eligible
                if _bout_priority(by_id[bout_id], bouts) == minimum_phase
            ]

            position = len(scheduled)
            time_group = _chronological_group(position, nb_tatamis)
            tatami_slot = position % nb_tatamis
            current_tatami_fighters: set[str] = set()

            if nb_tatamis == 2 and position >= 1 and position % 2 == 1:
                partner_id = scheduled[position - 1]
                partner = by_id[partner_id]
                current_tatami_fighters.update((partner.combattant_1, partner.combattant_2))

            def category_priority(bout_id: str) -> tuple:
                bout = by_id[bout_id]
                if bout.phase == "Petite Finale":
                    final_scheduled = any(
                        by_id[item].phase == "Finale"
                        and by_id[item].categorie == bout.categorie
                        for item in scheduled
                    )
                    bronze_before_final_penalty = 0 if not final_scheduled else 100
                else:
                    bronze_before_final_penalty = 0

                return (
                    _bout_priority(bout, bouts),
                    bronze_before_final_penalty,
                    category_last_position.get(bout.categorie, -100),
                    rng.random(),
                )

            eligible.sort(key=category_priority)
            chosen_id: str | None = None
            best_score: tuple | None = None

            for bout_id in eligible:
                bout = by_id[bout_id]
                fighters = (bout.combattant_1, bout.combattant_2)

                if set(fighters) & current_tatami_fighters:
                    continue

                rest_penalty = 0
                for fighter in fighters:
                    previous = last_position.get(fighter)
                    if previous is not None:
                        distance = position - previous
                        if distance == 1:
                            rest_penalty += 100
                        elif distance == 2:
                            rest_penalty += 12
                        elif distance < 4:
                            rest_penalty += 3

                category_gap = category_last_position.get(bout.categorie)
                category_penalty = 0 if category_gap is None else max(0, 3 - (position - category_gap))

                phase = _bout_priority(bout, bouts)
                if phase == 4:
                    category_bronze_done = any(
                        by_id[item].phase == "Petite Finale"
                        and by_id[item].categorie == bout.categorie
                        for item in scheduled
                    )
                    category_has_bronze = any(
                        candidate.phase == "Petite Finale"
                        and candidate.categorie == bout.categorie
                        for candidate in bouts
                    )
                    bronze_penalty = 100 if category_has_bronze and not category_bronze_done else 0
                else:
                    bronze_penalty = 0

                phase_penalty = phase * 0
                score = (
                    rest_penalty,
                    bronze_penalty,
                    category_penalty,
                    rng.random(),
                )
                if best_score is None or score < best_score:
                    best_score = score
                    chosen_id = bout_id

            if chosen_id is None:
                # Deux tatamis ne peuvent pas accueillir simultanément le même combattant.
                # L’algorithme doit continuer sur une autre paire plutôt que produire un ordre invalide.
                remaining_for_pair = [
                    bout_id
                    for bout_id in eligible
                    if not (set((by_id[bout_id].combattant_1, by_id[bout_id].combattant_2))
                            & current_tatami_fighters)
                ]
                if not remaining_for_pair:
                    chosen_id = eligible[0]
                    for fighter in (
                        by_id[chosen_id].combattant_1,
                        by_id[chosen_id].combattant_2,
                    ):
                        if fighter in current_tatami_fighters:
                            raise ValueError("Impossible d’affecter les tatamis sans conflit.")
                else:
                    chosen_id = min(
                        remaining_for_pair,
                        key=lambda bout_id: (_bout_priority(by_id[bout_id], bouts), rng.random()),
                    )

            chosen = by_id[chosen_id]
            scheduled.append(chosen_id)
            remaining.remove(chosen_id)
            for fighter in (chosen.combattant_1, chosen.combattant_2):
                last_position[fighter] = position
            category_last_position[chosen.categorie] = position

        return scheduled

    best_order: list[str] | None = None
    best_report: dict | None = None

    for attempt in range(250):
        rng.seed(seed + attempt)
        candidate = schedule_order()
        frame, report = build_dataframe(candidate, by_id, nb_tatamis)
        report_score = (
            report["critical_violations"],
            report["rest_violations"],
            report["phase_violations"],
            report["tatami_load_difference"],
        )
        if best_report is None or report_score < (
            best_report["critical_violations"],
            best_report["rest_violations"],
            best_report["phase_violations"],
            best_report["tatami_load_difference"],
        ):
            best_order = candidate
            best_report = report
            best_frame = frame

    if best_order is None or best_report is None:
        raise ValueError("Impossible de construire un ordre de passage.")

    best_report["order"] = best_order
    return best_frame, best_report


def build_dataframe(
    order: Sequence[str],
    by_id: dict[str, BoutInput],
    nb_tatamis: int,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    start = datetime.strptime(START_TIME, "%H:%M")

    last_position: dict[str, int] = {}
    position_by_id: dict[str, int] = {}
    tatami_load = [0] * nb_tatamis

    for position, bout_id in enumerate(order):
        bout = by_id[bout_id]
        position_by_id[bout_id] = position
        tatami_index = position % nb_tatamis if nb_tatamis == 2 else 0
        tatami_load[tatami_index] += 1
        time_slot = position // nb_tatamis
        departure = start + timedelta(minutes=MATCH_DURATION_MINUTES * time_slot)
        rows.append(
            {
                "Ordre (#)": position + 1,
                "Heure estimée": departure.strftime("%H:%M"),
                "Tatami": tatami_index + 1,
                "Catégorie": bout.categorie,
                "Phase": bout.phase,
                "Combattant A": bout.combattant_1,
                "Combattant B": bout.combattant_2,
                "Repos min. respecté (Oui/Non)": "À valider",
                "_bout_id": bout_id,
            }
        )

    frame = pd.DataFrame(rows)
    report = validate_schedule(order, by_id, nb_tatamis)
    report["tatami_load"] = tatami_load
    report["tatami_load_difference"] = max(tatami_load) - min(tatami_load)

    rest_ok = report["rest_violations"] == 0
    frame["Repos min. respecté (Oui/Non)"] = "Oui" if rest_ok else "Non"

    def resolve_slot_reference(value: str) -> str:
        for prefix in ("Vainqueur du combat ", "Perdant du combat "):
            if value.startswith(prefix):
                bout_id = value[len(prefix):]
                global_number = position_by_id.get(bout_id)
                if global_number is not None:
                    label = "Vainqueur" if prefix.startswith("Vainqueur") else "Perdant"
                    return f"{label} Combat #{global_number + 1}"
        return value

    frame["Combattant A"] = frame["Combattant A"].map(resolve_slot_reference)
    frame["Combattant B"] = frame["Combattant B"].map(resolve_slot_reference)
    frame["_bout_id"] = [order[position] for position in range(len(order))]
    return frame, report


def validate_schedule(
    order: Sequence[str],
    by_id: dict[str, BoutInput],
    nb_tatamis: int,
) -> dict:
    if nb_tatamis not in (1, 2):
        raise ValueError("nb_tatamis doit valoir 1 ou 2.")

    position_by_id = {bout_id: position for position, bout_id in enumerate(order)}
    dependency_violations: list[str] = []
    simultaneous_violations: list[str] = []
    rest_violations: list[str] = []
    phase_violations: list[str] = []

    for bout_id in order:
        position = position_by_id[bout_id]
        bout = by_id[bout_id]
        for dependency in bout.dependances:
            if dependency not in position_by_id or position_by_id[dependency] >= position:
                dependency_violations.append(bout_id)

    if nb_tatamis == 2:
        for position in range(0, len(order), 2):
            first_id = order[position] if position < len(order) else None
            second_id = order[position + 1] if position + 1 < len(order) else None
            if first_id is None or second_id is None:
                continue
            fighters_1 = {by_id[first_id].combattant_1, by_id[first_id].combattant_2}
            fighters_2 = {by_id[second_id].combattant_1, by_id[second_id].combattant_2}
            if fighters_1 & fighters_2:
                simultaneous_violations.append(f"{first_id}/{second_id}")

    last_position: dict[str, int] = {}
    for position, bout_id in enumerate(order):
        bout = by_id[bout_id]
        for fighter in (bout.combattant_1, bout.combattant_2):
            previous = last_position.get(fighter)
            if previous is not None and position - previous < 3:
                rest_violations.append(fighter)
            last_position[fighter] = position

    final_before_bronze: list[str] = []
    for bout in by_id.values():
        if bout.phase != "Finale":
            continue
        bronzes = [
            candidate
            for candidate in by_id.values()
            if candidate.phase == "Petite Finale"
            and candidate.categorie == bout.categorie
        ]
        for bronze in bronzes:
            if (
                bronze.id in position_by_id
                and position_by_id[bronze.id] >= position_by_id[bout.id]
            ):
                final_before_bronze.append(bout.id)

    if final_before_bronze:
        phase_violations.extend(final_before_bronze)

    base_critical = (
        len(dependency_violations)
        + len(simultaneous_violations)
        + len(final_before_bronze)
    )
    return {
        "valid": base_critical == 0 and not rest_violations,
        "critical_violations": base_critical,
        "dependency_violations": sorted(set(dependency_violations)),
        "simultaneous_violations": sorted(set(simultaneous_violations)),
        "rest_violations": len(set(rest_violations)),
        "phase_violations": len(set(phase_violations)),
        "missing_bouts": sorted(set(by_id) - set(order)),
        "duplicate_bouts": len(order) - len(set(order)),
    }


def export_schedule_csv(frame: pd.DataFrame, output_path: Path) -> None:
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def export_schedule_pdf(frame: pd.DataFrame, output_path: Path, report: dict) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title="TFT Série No-GI #1 - Ordre de passage",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("TFT Série No-GI #1 - Ordre de passage", styles["Title"]),
        Spacer(1, 4 * mm),
        Paragraph(
            f"Planification : {len(frame)} combats · "
            f"{'Valide' if report['valid'] else 'À corriger'}",
            styles["Normal"],
        ),
        Spacer(1, 5 * mm),
    ]
    data = [list(frame.columns)]
    data.extend([str(value) for value in row] for row in frame.itertuples(index=False, name=None))
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111111")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#ddddda")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f1")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(table)
    doc.build(story)


def export_schedule_pdf_bytes(
    frame: pd.DataFrame,
    report: dict,
    fighter_by_name: dict[str, object] | None = None,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title="TFT Série No-GI #1 - Ordre de passage",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ScheduleTitle",
        parent=styles["Title"],
        textColor=colors.white,
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
    )
    kicker_style = ParagraphStyle(
        "ScheduleKicker",
        parent=styles["Normal"],
        textColor=colors.HexColor("#F5C518"),
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
    )
    subtitle_style = ParagraphStyle(
        "ScheduleSubtitle",
        parent=styles["Normal"],
        textColor=colors.HexColor("#D6D6D2"),
        fontSize=8,
        leading=10,
    )
    cell_style = ParagraphStyle(
        "ScheduleCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
    )
    header = Table(
        [[
            Paragraph("TFT SÉRIE NO-GI #1", kicker_style),
            "",
        ], [
            Paragraph(
                f"Ordre de passage officiel • {len(frame)} combats",
                title_style,
            ),
            "",
        ], [
            Paragraph(
                "Pesée : 13h00  |  Début des combats : 14h00  |  Toulouse (38 Av. de Lombez)",
                subtitle_style,
            ),
            "",
        ]],
        colWidths=[260 * mm, 12 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("SPAN", (0, 1), (1, 1)),
                ("SPAN", (0, 2), (1, 2)),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#111111")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story = [
        header,
        Spacer(1, 5 * mm),
    ]

    data = [[
        "N°",
        "Heure",
        "Catégorie & Phase",
        "Combattant A",
        "VS",
        "Combattant B",
        "Résultat",
    ]]
    accent_rows: list[int] = []
    for row in frame.itertuples(index=False, name=None):
        row_dict = dict(zip(frame.columns, row))
        number = row_dict["Ordre (#)"]
        phase = str(row_dict["Phase"])
        accent = phase in ("Petite Finale", "Finale")
        if accent:
            accent_rows.append(len(data))

        def fighter_cell(name: object) -> object:
            text = str(name)
            fighter = fighter_by_name.get(text) if fighter_by_name else None
            photo_data = getattr(fighter, "photo_data", None)
            if photo_data:
                photo = Image(io.BytesIO(photo_data), width=7 * mm, height=7 * mm)
                return Table([[photo, Paragraph(text, cell_style)]], colWidths=[8 * mm, 49 * mm])
            parts = text.strip().split()
            initials = "".join(part[0] for part in parts[:2]).upper() if parts else "?"
            return Table(
                [[
                    Paragraph(initials, ParagraphStyle(
                        "Initials",
                        parent=cell_style,
                        alignment=1,
                        textColor=colors.HexColor("#686868"),
                    )),
                    Paragraph(text, cell_style),
                ]],
                colWidths=[8 * mm, 49 * mm],
            )

        number_cell = Paragraph(
            f"<b>#{number}</b>",
            ParagraphStyle(
                "Number",
                parent=cell_style,
                alignment=1,
                textColor=colors.HexColor("#F5C518") if not accent else colors.HexColor("#111111"),
                fontName="Helvetica-Bold",
            ),
        )
        category_phase = (
            f"<b>{row_dict['Catégorie']}</b><br/>"
            f"<font color='#686868'>{phase}</font>"
        )
        data.append([
            number_cell,
            str(row_dict["Heure estimée"]),
            Paragraph(category_phase, cell_style),
            fighter_cell(row_dict["Combattant A"]),
            "VS",
            fighter_cell(row_dict["Combattant B"]),
            "",
        ])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[12 * mm, 16 * mm, 66 * mm, 65 * mm, 10 * mm, 65 * mm, 15 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111111")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#ddddda")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f1")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 1), (1, -1), "CENTER"),
                ("ALIGN", (4, 1), (4, -1), "CENTER"),
                ("ALIGN", (6, 1), (6, -1), "CENTER"),
                ("FONTNAME", (6, 1), (6, -1), "Helvetica-Bold"),
                ("FONTSIZE", (6, 1), (6, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#111111")),
                ("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#F5C518")),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return output.getvalue()


def test_bouts_22() -> list[dict]:
    bouts: list[dict] = []

    def add(
        bout_id: str,
        categorie: str,
        phase: str,
        first: str,
        second: str,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        bouts.append(
            {
                "id": bout_id,
                "categorie": categorie,
                "phase": phase,
                "combattant_1": first,
                "combattant_2": second,
                "dependances": list(dependencies),
            }
        )

    # Tableau de 8 : 4 quarts, 2 demis, petite finale et finale.
    for index in range(1, 5):
        add(f"68D-Q{index}", "Homme 17+ -68kg Debutant", "Quart",
            f"J8-{index}A", f"J8-{index}B")
    add("68D-D1", "Homme 17+ -68kg Debutant", "Demi",
        "Vainqueur 68D-Q1", "Vainqueur 68D-Q2", ("68D-Q1", "68D-Q2"))
    add("68D-D2", "Homme 17+ -68kg Debutant", "Demi",
        "Vainqueur 68D-Q3", "Vainqueur 68D-Q4", ("68D-Q3", "68D-Q4"))
    add("68D-B", "Homme 17+ -68kg Debutant", "Petite Finale",
        "Perdant 68D-D1", "Perdant 68D-D2", ("68D-D1", "68D-D2"))
    add("68D-F", "Homme 17+ -68kg Debutant", "Finale",
        "Vainqueur 68D-D1", "Vainqueur 68D-D2", ("68D-D1", "68D-D2"))

    # Tableau de 4 : 2 demis, petite finale et finale.
    add("75I-D1", "Homme 17+ -75kg Intermediaire", "Demi", "J4-1A", "J4-1B")
    add("75I-D2", "Homme 17+ -75kg Intermediaire", "Demi", "J4-2A", "J4-2B")
    add("75I-B", "Homme 17+ -75kg Intermediaire", "Petite Finale",
        "Perdant 75I-D1", "Perdant 75I-D2", ("75I-D1", "75I-D2"))
    add("75I-F", "Homme 17+ -75kg Intermediaire", "Finale",
        "Vainqueur 75I-D1", "Vainqueur 75I-D2", ("75I-D1", "75I-D2"))

    # Trois poules uniques de 3 combattants.
    for poule_index, category in enumerate(("Poule A", "Poule B", "Poule C"), start=1):
        fighters = [f"{category} {suffix}" for suffix in ("A", "B", "C")]
        add(f"P{poule_index}-1", category, "Poule", fighters[0], fighters[1])
        add(f"P{poule_index}-2", category, "Poule", fighters[1], fighters[2])
        add(f"P{poule_index}-3", category, "Poule", fighters[0], fighters[2])

    # Finale directe feminine.
    add("F-F", "Femme 17+ -60kg", "Finale", "Femme 1", "Femme 2")
    return bouts


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Génère localement un ordre de passage privé pour 22 combats."
    )
    parser.add_argument("--tatamis", type=int, choices=(1, 2), default=2)
    parser.add_argument("--seed", type=int, default=20261128)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("schedule_tournament.csv"),
        help="CSV privé de sortie.",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path("schedule_tournament.pdf"),
        help="PDF privé de sortie.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        frame, report = generate_schedule(
            test_bouts_22(),
            nb_tatamis=args.tatamis,
            seed=args.seed,
        )
        export_schedule_csv(frame, args.csv)
        export_schedule_pdf(frame, args.pdf, report)
    except (OSError, ValueError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2

    print(
        f"Planning privé généré : {len(frame)} combats, "
        f"{'valide' if report['valid'] else 'contraintes à corriger'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
