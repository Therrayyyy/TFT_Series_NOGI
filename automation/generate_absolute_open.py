from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(frozen=True)
class Bout:
    number: int
    round_name: str
    first: str
    second: str


def read_names(lines: Iterable[str]) -> list[str]:
    names = [line.strip() for line in lines if line.strip()]
    if len(names) < 2:
        raise ValueError("Il faut au moins deux participants.")
    if len(set(name.casefold() for name in names)) != len(names):
        raise ValueError("Chaque participant doit avoir un nom unique.")
    return names


def round_name(round_index: int, round_count: int) -> str:
    remaining = round_count - round_index
    if remaining == 1:
        return "Finale"
    if remaining == 2:
        return "Demi-finales"
    if remaining == 3:
        return "Quarts de finale"
    if remaining == 4:
        return "Huitièmes de finale"
    return f"Tour {round_index + 1}"


def build_elimination(names: Sequence[str], seed: str | None = None) -> list[Bout]:
    shuffled = list(names)
    if seed is None:
        random.SystemRandom().shuffle(shuffled)
    else:
        random.Random(seed).shuffle(shuffled)

    bracket_size = 1
    while bracket_size < len(shuffled):
        bracket_size *= 2

    slot_type = str | tuple[str, int] | None
    slots: list[slot_type] = list(shuffled) + [None] * (bracket_size - len(shuffled))
    bouts: list[Bout] = []
    next_number = 1
    round_count = bracket_size.bit_length() - 1

    current_slots = slots

    def label(slot: slot_type) -> str | None:
        if slot is None:
            return None
        if isinstance(slot, tuple):
            return f"Vainqueur du combat {slot[1]}"
        return slot

    for round_index in range(round_count):
        next_slots: list[slot_type] = []

        for match_index in range(0, len(current_slots), 2):
            first = current_slots[match_index]
            second = current_slots[match_index + 1]
            first_label = label(first)
            second_label = label(second)

            if first_label is not None and second_label is not None:
                bouts.append(
                    Bout(next_number, round_name(round_index, round_count), first_label, second_label)
                )
                next_slots.append(("winner", next_number))
                next_number += 1
            elif first_label is not None:
                next_slots.append(first)
            elif second_label is not None:
                next_slots.append(second)
            else:
                next_slots.append(None)

        current_slots = next_slots

    return bouts


def generate_pdf(output_path: Path, names: Sequence[str], seed: str | None = None) -> int:
    bouts = build_elimination(names, seed)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AbsoluteOpenTitle",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#111111"),
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
    )
    subtitle_style = ParagraphStyle(
        "AbsoluteOpenSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#686868"),
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="TFT - Absolute Open",
        author="TFT Série No-GI",
    )

    story = [
        Paragraph("ABSOLUTE OPEN", title_style),
        Spacer(1, 3 * mm),
        Paragraph(
            f"Ordre de passage · {len(names)} participant{'s' if len(names) > 1 else ''}",
            subtitle_style,
        ),
        Spacer(1, 7 * mm),
    ]

    if not bouts:
        story.append(Paragraph("Aucun combat : un seul participant.", styles["Normal"]))
    else:
        table_data = [["Ordre", "Phase", "Combattant 1", "Combattant 2"]]
        table_data.extend(
            [str(bout.number), bout.round_name, bout.first, bout.second]
            for bout in bouts
        )
        table = Table(
            table_data,
            colWidths=[18 * mm, 37 * mm, 58 * mm, 58 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111111")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ALIGN", (0, 0), (1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#ddddda")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f1")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)

    doc.build(story)
    return len(bouts)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère localement un PDF privé pour l’ordre de passage Absolute Open."
    )
    parser.add_argument(
        "--names-file",
        type=Path,
        help="Fichier texte privé, un participant par ligne.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("absolute_open.pdf"),
        help="PDF de sortie privé (défaut : absolute_open.pdf).",
    )
    parser.add_argument(
        "--seed",
        help="Graine optionnelle pour reproduire le tirage.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.names_file:
            names = read_names(args.names_file.read_text(encoding="utf-8").splitlines())
        else:
            names = read_names(sys.stdin)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2

    try:
        bout_count = generate_pdf(args.output, names, args.seed)
    except OSError as error:
        print(f"Erreur d’écriture du PDF : {error}", file=sys.stderr)
        return 2

    print(f"PDF privé généré : {bout_count} combat(s) prévu(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
