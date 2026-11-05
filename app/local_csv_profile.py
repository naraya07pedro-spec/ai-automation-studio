"""Owner-selected, read-only bounded CSV profiling for reviewed task plans.

No file paths, formulas, SQL, pandas, arbitrary queries, network calls or model
inference. Raw records remain local in the *consented* plaintext plan ledger.
The published profile never echoes individual cell contents.
"""
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from hashlib import sha256
from io import StringIO
import re

MAX_CSV_BYTES = 6000
MAX_CSV_ROWS = 100
MAX_CSV_COLUMNS = 8
MAX_CELL_CHARS = 120
CSV_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.()-]{0,74}\.csv\Z", re.IGNORECASE)
COLUMN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")
NUMBER = re.compile(r"-?[0-9]{1,12}(?:\.[0-9]{1,6})?\Z")


class CSVProfileError(ValueError):
    pass


def parse_selected_csv(filename, content):
    """Validate a single frozen UTF-8 CSV; strict shape, no inferred dialect."""
    if (type(filename) is not str or CSV_NAME.fullmatch(filename) is None
            or type(content) is not str):
        raise CSVProfileError("invalid_plan_csv")
    raw = content.encode("utf-8")
    if not 1 <= len(raw) <= MAX_CSV_BYTES:
        raise CSVProfileError("invalid_plan_csv")
    if (content.startswith("\ufeff")
            or any(ord(c) < 32 and c not in "\r\n\t" for c in content)
            or "\x7f" in content):
        raise CSVProfileError("invalid_plan_csv")
    try:
        rows = list(csv.reader(StringIO(content, newline=""), strict=True))
    except (csv.Error, ValueError):
        raise CSVProfileError("invalid_plan_csv") from None
    if not 2 <= len(rows) <= MAX_CSV_ROWS + 1:
        raise CSVProfileError("invalid_plan_csv")
    headers = rows[0]
    if (not 1 <= len(headers) <= MAX_CSV_COLUMNS
            or any(type(h) is not str or COLUMN_NAME.fullmatch(h) is None
                   for h in headers)
            or len({h.casefold() for h in headers}) != len(headers)):
        raise CSVProfileError("invalid_plan_csv")
    data = rows[1:]
    if any(len(row) != len(headers)
           or any(len(cell) > MAX_CELL_CHARS or "\n" in cell
                  or "\r" in cell or "\t" in cell for cell in row)
           for row in data):
        raise CSVProfileError("invalid_plan_csv")
    return {"filename": filename, "content": content,
            "sha256": sha256(raw).hexdigest(), "headers": headers,
            "rows": data}


def csv_source_metadata(parsed):
    if parsed is None:
        return None
    return {"filename": parsed["filename"],
            "source_bytes": len(parsed["content"].encode("utf-8")),
            "sha256": parsed["sha256"], "row_count": len(parsed["rows"]),
            "columns": list(parsed["headers"]),
            "stored_local_plaintext": True,
            "scope": "OWNER_SELECTED_CSV_SNAPSHOT"}


def profile_selected_csv(parsed, columns):
    """Deterministic bounded column statistics, no disclosure of raw values."""
    if (type(parsed) is not dict or type(columns) is not list
            or not 1 <= len(columns) <= MAX_CSV_COLUMNS
            or any(type(c) is not str for c in columns)
            or len({c.casefold() for c in columns}) != len(columns)
            or any(c not in parsed["headers"] for c in columns)):
        raise CSVProfileError("invalid_csv_columns")
    indexes = {v: i for i, v in enumerate(parsed["headers"])}
    summary = []
    for name in columns:
        values = [row[indexes[name]] for row in parsed["rows"]]
        present = [v.strip() for v in values if v.strip()]
        numeric = bool(present) and all(NUMBER.fullmatch(v) for v in present)
        result = {
            "column": name, "nonempty_count": len(present),
            "missing_count": len(values) - len(present),
            "distinct_count": len(set(present)),
            "type": "numeric" if numeric else "text_or_empty",
            "numeric_summary": None,
        }
        if numeric:
            try:
                nums = [Decimal(v) for v in present]
                mean = (sum(nums) / len(nums)).quantize(
                    Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
            except (InvalidOperation, OverflowError):
                raise CSVProfileError("invalid_csv_numeric") from None
            result["numeric_summary"] = {
                "min": format(min(nums), "f"),
                "max": format(max(nums), "f"),
                "sum": format(sum(nums), "f"),
                "mean_6dp": format(mean, "f"),
            }
        summary.append(result)
    return {
        "source": "OWNER_SELECTED_CSV_SNAPSHOT",
        "status": "CSV_PROFILE_READ_ONLY",
        "filename": parsed["filename"],
        "sha256": parsed["sha256"],
        "row_count": len(parsed["rows"]),
        "column_stats": summary,
        "method": "DETERMINISTIC_BOUNDED_CSV_PROFILE",
        "model_calls": 0, "external_write_count": 0,
        "limitations": (
            "Exact frozen owner-selected CSV only; descriptive statistics, "
            "not factual accuracy, causal analysis, model interpretation or "
            "validation of missing-data semantics. Raw cell values omitted.")
    }
