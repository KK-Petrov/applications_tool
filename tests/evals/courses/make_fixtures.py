"""Generate the course-import eval fixtures from one definition (C-2c-03; docs/04 §9; DECISIONS #44, #64).

    uv run python tests/evals/courses/make_fixtures.py

Writes, next to this file: ``transcript.txt`` (the pasted-text form), ``transcript.docx`` (python-docx),
``transcript.pdf`` (fpdf2, text layer), ``scanned.pdf`` (a page with no text layer) and ``expected.json``.
The outputs are committed; rerun only when this file changes. Everything is invented: the student, both
institutions, every lecturer and every number. The identifiers come in three layouts in every format (label
and value on one line; label on one line and the value on the next; a table whose label row is followed by a
value row), in English and Dutch, plus a standalone 9-digit number that passes the BSN 11-check. Look-alikes
that must survive: course codes, years, a programme code, a register number and a 9-digit number that fails
the 11-check.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESE = "Example School of Economics"
NFU = "Northfield University"

# (code, name, grade, credits as printed, term, lecturers as printed)
ESE_COURSES = [
    (
        "FEB11001",
        "Mathematics I",
        "8,0",
        "4",
        "2021-2022 block 1",
        ["Prof. dr. A. van den Berg", "Dr. J. Smit"],
    ),
    ("FEB11002", "Microeconomics", "7,5", "6", "2021-2022 block 1", ["Prof. dr. ir. B. ter Horst"]),
    ("FEB11003", "Financial Accounting", "6,5", "4", "2021-2022 block 2", ["Prof. dr. K. Müller"]),
    ("FEB11004", "Statistics I", "9,0", "4", "2021-2022 block 2", ["Dr. J. de Vries"]),
    ("FEB11005", "Mathematics II", "8,5", "4", "2021-2022 block 3", ["Prof. dr. A. van den Berg"]),
    ("FEB11006", "Macroeconomics", "7,0", "6", "2021-2022 block 3", ["Prof. dr. ir. B. ter Horst"]),
    ("FEB11007", "Organisation and Strategy", "6,0", "4", "2021-2022 block 4", ["Dr. J. Smit"]),
    ("FEB11008", "Introduction to Finance", "8,0", "6", "2021-2022 block 4", ["Dr. C. in 't Veld"]),
    (
        "FEB12001",
        "Econometrics I",
        "7,5",
        "6",
        "2022-2023 block 1",
        ["Prof. dr. K. Müller", "drs. P. Jansen"],
    ),
    ("FEB12002", "Marketing", "6,5", "4", "2022-2023 block 1", ["drs. P. Jansen"]),
    ("FEB12003", "Corporate Finance", "9,5", "6", "2022-2023 block 2", ["Dr. L. Ødegaard"]),
    ("FEB12004", "Linear Algebra", "8,5", "4", "2022-2023 block 2", ["A. van den Berg"]),
    ("FEB12005", "Behavioural Economics", "7,0", "4", "2022-2023 block 3", ["Prof. dr. E. Bakker"]),
    (
        "FEB12006",
        "Investments",
        "9,0",
        "6",
        "2022-2023 block 3",
        ["Prof. dr. ir. B. ter Horst", "Dr. L. Ødegaard"],
    ),
    ("FEB12007", "Derivatives and Risk", "8,0", "6", "2022-2023 block 4", ["Dr. L. Ødegaard"]),
    ("FEB13001", "Empirical Methods in Finance", "8,5", "6", "2023-2024 block 1", ["Prof. dr. E. Bakker"]),
    ("FEB13002", "Bedrijfseconomie", "7,5", "4", "2023-2024 block 1", ["Prof. dr. E. Bakker"]),
    (
        "FEB13003",
        "Bachelor Thesis Finance",
        "8,0",
        "15",
        "2023-2024 block 4",
        ["Prof. dr. E. Bakker", "Dr. J. Smit"],
    ),
]
NFU_COURSES = [
    ("FIN 3010", "Fixed Income Securities", "A", "3.0", "Fall 2023", ["Professor Maria Lopez"]),
    ("FIN 3200", "Portfolio Theory", "A-", "3.0", "Fall 2023", ["Dr. Samuel Whitmore"]),
    ("FIN 3300", "Financial Modeling in Python", "A", "3.0", "Fall 2023", ["Professor Maria Lopez"]),
    ("ECON 3410", "Game Theory", "B+", "3.0", "Fall 2023", ["Prof. Daniel O'Connor"]),
    ("ECON 3420", "Industrial Organization", "A-", "3.0", "Fall 2023", ["Prof. Daniel O'Connor"]),
    ("STAT 3100", "Time Series Analysis", "A", "4.0", "Fall 2023", ["Dr. Priya Raman"]),
    ("ACCT 3050", "Valuation", "B+", "3.0", "Fall 2023", ["Professor Henrik Lindqvist"]),
    ("COMM 2100", "Business Writing", "Pass", "1.0", "Fall 2023", ["Dr. Grace Kim"]),
    ("STAT 3200", "Machine Learning for Finance", "A-", "3.0", "Fall 2023", ["Dr. Priya Raman"]),
    (
        "FIN 3900",
        "Quantitative Trading Seminar",
        "A",
        "3.0",
        "Fall 2023",
        ["Prof. Daniel O'Connor", "Dr. Priya Raman"],
    ),
]

# Identifiers as printed (every one must be gone from the output and from data_dir) and look-alikes to keep.
IDENTIFIERS = [
    "512345",  # Student number: (inline, English)
    "12 March 2001",  # Date of birth: (inline, English month)
    "123456782",  # Burgerservicenummer (BSN): (inline, Dutch; passes the 11-check)
    "S2034567",  # Student ID / value on the next line
    "598431",  # Studentnummer table: label row, value row
    "12-03-2001",  # Geboortedatum table: label row, value row
    "12 maart 2001",  # Geboortedatum: / value on the next line (Dutch month)
    "111222333",  # standalone 9 digits that pass the BSN 11-check
    "N00412345",  # Student ID: (inline, US)
    "03/12/2001",  # Birth date: (inline, US)
]
KEEP = ["123456789", "2023-004512", "50952", "FEB12003", "FIN 3010", "2021-2022", "Fall 2023"]
ROW_SWAP = {"code": "FEB12002", "wrong_grade": "9,5"}  # planted: Marketing given Corporate Finance's grade


def header_lines() -> list[tuple[str, list[str] | None]]:
    """(paragraph text, None) or ("", table rows) in document order, for the ESE part."""
    return [
        (f"{ESE.upper()}", None),
        ("Official transcript of records / Cijferlijst", None),
        ("Name: Noor Example", None),
        ("Student number: 512345", None),
        ("Date of birth: 12 March 2001", None),
        ("Burgerservicenummer (BSN): 123456782", None),
        ("Student ID", None),
        ("S2034567", None),
        (
            "",
            [
                "Studentnummer\tGeboortedatum\tOpleiding",
                "598431\t12-03-2001\tBSc Economie en Bedrijfseconomie",
            ],
        ),
        ("Programme: BSc Economics and Business Economics (programme code 50952)", None),
        ("Geboortedatum:", None),
        ("12 maart 2001", None),
        ("Diploma register 2023-004512 - Document number 123456789", None),
    ]


def course_rows(courses: list[tuple]) -> list[str]:
    head = "Code\tCourse\tGrade\tCredits\tPeriod\tLecturer(s)"
    return [head] + ["\t".join([c, n, g, cr, t, "; ".join(lec)]) for c, n, g, cr, t, lec in courses]


def nfu_lines() -> list[tuple[str, list[str] | None]]:
    return [
        (f"{NFU.upper()} - Exchange semester record", None),
        ("Student ID: N00412345", None),
        ("Birth date: 03/12/2001", None),
    ]


def footer_lines() -> list[str]:
    return ["Ref. 111222333", "Issued on 30 June 2024. This record is invented for testing."]


def text_version() -> str:
    lines: list[str] = []
    for text, table in header_lines():
        lines += table if table else [text]
    lines += ["", f"Courses at {ESE}", *course_rows(ESE_COURSES), ""]
    for text, _ in nfu_lines():
        lines.append(text)
    lines += ["", f"Courses at {NFU}", *course_rows(NFU_COURSES), "", *footer_lines()]
    return "\n".join(lines) + "\n"


def write_docx(path: Path) -> None:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.size = Pt(9)

    def table(rows: list[str]) -> None:
        cells = [r.split("\t") for r in rows]
        t = doc.add_table(rows=len(cells), cols=len(cells[0]))
        for i, row in enumerate(cells):
            for j, value in enumerate(row):
                t.cell(i, j).text = value

    for text, rows in header_lines():
        table(rows) if rows else doc.add_paragraph(text)
    doc.add_paragraph(f"Courses at {ESE}")
    table(course_rows(ESE_COURSES))
    for text, _ in nfu_lines():
        doc.add_paragraph(text)
    doc.add_paragraph(f"Courses at {NFU}")
    table(course_rows(NFU_COURSES))
    for line in footer_lines():
        doc.add_paragraph(line)
    doc.core_properties.author = "Application Hub test fixture"
    doc.save(str(path))


WIDTHS = [22, 62, 14, 14, 34, 128]  # mm, landscape A4 (277 mm usable)


def write_pdf(path: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)

    def line(text: str) -> None:
        pdf.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")

    def table(rows: list[str], widths: list[int]) -> None:
        for row in rows:
            for width, value in zip(widths, row.split("\t"), strict=False):
                pdf.cell(width, 6, value, border=1)
            pdf.ln(6)

    for text, rows in header_lines():
        table(rows, [40, 40, 90]) if rows else line(text)
    line(f"Courses at {ESE}")
    table(course_rows(ESE_COURSES), WIDTHS)
    for text, _ in nfu_lines():
        line(text)
    line(f"Courses at {NFU}")
    table(course_rows(NFU_COURSES), WIDTHS)
    for text in footer_lines():
        line(text)
    pdf.set_creator("Application Hub test fixture")
    pdf.output(str(path))


def write_scanned_pdf(path: Path) -> None:
    """Two pages with drawings only: what a scan without OCR looks like to a text extractor."""
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(2):
        pdf.add_page()
        for y in range(20, 260, 8):
            pdf.rect(15, y, 180, 5, style="F")
    pdf.output(str(path))


def expected() -> dict:
    def row(c: tuple, institution: str) -> dict:
        code, name, grade, credits, term, lecturers = c
        return {
            "code": code,
            "name": name,
            "institution": institution,
            "grade": grade,
            "credits": float(credits.replace(",", ".")),
            "term": term,
            "lecturers": lecturers,
        }

    courses = [row(c, ESE) for c in ESE_COURSES] + [row(c, NFU) for c in NFU_COURSES]
    return {
        "courses": courses,
        "identifiers": IDENTIFIERS,
        "keep": KEEP,
        "swap": ROW_SWAP,
        # Every lecturer once (grouped by surname + initials, docs/02 §9), with all their course codes.
        "lecturers": {
            "van den Berg A": ["FEB11001", "FEB11005", "FEB12004"],
            "Smit J": ["FEB11001", "FEB11007", "FEB13003"],
            "ter Horst B": ["FEB11002", "FEB11006", "FEB12006"],
            "Müller K": ["FEB11003", "FEB12001"],
            "de Vries J": ["FEB11004"],
            "in 't Veld C": ["FEB11008"],
            "Jansen P": ["FEB12001", "FEB12002"],
            "Ødegaard L": ["FEB12003", "FEB12006", "FEB12007"],
            "Bakker E": ["FEB12005", "FEB13001", "FEB13002", "FEB13003"],
            "Lopez M": ["FIN 3010", "FIN 3300"],
            "Whitmore S": ["FIN 3200"],
            "O'Connor D": ["ECON 3410", "ECON 3420", "FIN 3900"],
            "Raman P": ["STAT 3100", "STAT 3200", "FIN 3900"],
            "Lindqvist H": ["ACCT 3050"],
            "Kim G": ["COMM 2100"],
        },
    }


def main() -> None:
    assert len(ESE_COURSES) + len(NFU_COURSES) == 28
    (HERE / "transcript.txt").write_text(text_version(), encoding="utf-8", newline="\n")
    write_docx(HERE / "transcript.docx")
    write_pdf(HERE / "transcript.pdf")
    write_scanned_pdf(HERE / "scanned.pdf")
    (HERE / "expected.json").write_text(
        json.dumps(expected(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print("wrote transcript.txt, transcript.docx, transcript.pdf, scanned.pdf, expected.json")


if __name__ == "__main__":
    main()
