"""The report Document as a spreadsheet (decision R17): Excel, one sheet per section, and CSV.

Both read the same Document the screen draws, so the numbers are the same. Excel cells keep
numbers as numbers (the reader's Excel formats them); CSV is meant to be opened in a
spreadsheet by a person, so it follows the caller's language: in pt-BR, ``;`` between fields
and ``,`` decimals, which is what a Brazilian Excel expects.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from ..texts import PT_BR, _, current_locale

__all__ = ["file_name", "to_csv", "to_xlsx"]

_UNIT_SUFFIX = {"days": "d", "hours": "h", "percent": "%", "cards/week": "/sem"}


def _cell(value: Any, unit: str) -> Any:
    """A table value as a spreadsheet cell: card as ``KEY — title``, instants trimmed."""
    if value is None:
        return ""
    if unit == "card" and isinstance(value, dict):
        return f"{value.get('key') or ''} — {value.get('title') or ''}".strip(" —")
    if unit == "datetime" and isinstance(value, str):
        return value.replace("T", " ").removesuffix("Z")[:16]
    if isinstance(value, bool):
        return _("yes") if value else _("no")
    return value


def _rows(block: dict[str, Any]) -> list[list[Any]]:
    """A block as rows: a heading row first, then the values."""
    kind = block.get("type")
    if kind == "kpis":
        out: list[list[Any]] = [
            [_("Indicator"), _("Value"), _("Unit"), _("Previous period"), _("Change (%)")]
        ]
        for item in block["items"]:
            out.append(
                [
                    item["label"],
                    _cell(item.get("value"), item["unit"]),
                    _UNIT_SUFFIX.get(item["unit"], ""),
                    _cell(item.get("previous"), item["unit"]),
                    _cell(item.get("trend_pct"), "percent"),
                ]
            )
        return out
    if kind == "table":
        columns = block["columns"]
        out = [[c["label"] for c in columns]]
        for row in block["rows"]:
            out.append([_cell(row.get(c["key"]), c["unit"]) for c in columns])
        if not block["rows"]:
            out.append([block.get("empty") or ""])
        return out
    if kind == "bars":
        out = [[block.get("title") or "", _("Value")]]
        for item in block["items"]:
            out.append([item.get("label"), _cell(item.get("value"), block["unit"])])
        if not block["items"]:
            out.append([block.get("empty") or ""])
        return out
    if kind == "note":
        return [[block.get("text") or ""]]
    return []


def _header(document: dict[str, Any]) -> list[list[Any]]:
    period, previous = document["period"], document["previous"]
    generated = str(document["generated_at"]).replace("T", " ").removesuffix("Z")
    lines = [[document["title"]]]
    if document.get("subtitle"):
        lines.append([document["subtitle"]])
    lines.append([f"{_('Period')}: {period['label']} ({period['from']} — {period['to']})"])
    lines.append([f"{_('Previous period')}: {previous['label']}"])
    lines.append([f"{_('Generated at')}: {generated} UTC"])
    return lines


def _sheet_title(text: str, used: set[str]) -> str:
    """Excel sheet names: at most 31 characters, none of ``[]:*?/\\``, unique."""
    base = re.sub(r"[\[\]:*?/\\]", " ", text).strip()[:31] or "Sheet"
    title, n = base, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title, n = base[: 31 - len(suffix)] + suffix, n + 1
    used.add(title.lower())
    return title


def to_xlsx(document: dict[str, Any]) -> bytes:
    book = Workbook()
    used: set[str] = set()
    bold = Font(bold=True)
    first = True
    for sec in document["sections"]:
        sheet = book.active if first else book.create_sheet()
        first = False
        sheet.title = _sheet_title(sec["title"], used)
        for line in _header(document):
            sheet.append(line)
        sheet.cell(row=1, column=1).font = Font(bold=True, size=14)
        sheet.append([])
        sheet.append([sec["title"]])
        sheet.cell(row=sheet.max_row, column=1).font = Font(bold=True, size=12)
        if sec.get("description"):
            sheet.append([sec["description"]])
        widths: dict[int, int] = {}
        for block in sec["blocks"]:
            rows = _rows(block)
            if not rows:
                continue
            sheet.append([])
            if block.get("type") == "table" and block.get("title"):
                sheet.append([block["title"]])
                sheet.cell(row=sheet.max_row, column=1).font = bold
            for index, row in enumerate(rows):
                sheet.append(row)
                if index == 0 and block.get("type") != "note":
                    for col in range(1, len(row) + 1):
                        sheet.cell(row=sheet.max_row, column=col).font = bold
                if block.get("type") != "note":
                    for col, value in enumerate(row, start=1):
                        widths[col] = max(widths.get(col, 8), min(60, len(str(value)) + 2))
        for col, width in widths.items():
            sheet.column_dimensions[get_column_letter(col)].width = width
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=False)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _csv_value(value: Any, decimal_comma: bool) -> Any:
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
        return text.replace(".", ",") if decimal_comma else text
    return value


def to_csv(document: dict[str, Any]) -> bytes:
    portuguese = current_locale.get() == PT_BR
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";" if portuguese else ",", lineterminator="\r\n")
    for line in _header(document):
        writer.writerow(line)
    for sec in document["sections"]:
        writer.writerow([])
        writer.writerow([sec["title"]])
        for block in sec["blocks"]:
            rows = _rows(block)
            if not rows:
                continue
            if block.get("type") == "table" and block.get("title"):
                writer.writerow([block["title"]])
            for row in rows:
                writer.writerow([_csv_value(v, portuguese) for v in row])
            writer.writerow([])
    # BOM: Excel opens UTF-8 with accents only when the file says so.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def file_name(document: dict[str, Any], extension: str) -> str:
    """``rep-01-nagare-flow-2026-11-17.xlsx``: code, scope and the period's first day."""
    scope = document.get("subtitle") or document["template"]
    plain = unicodedata.normalize("NFKD", str(scope)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40] or "report"
    return f"{document['code'].lower()}-{slug}-{document['period']['from']}.{extension}"
