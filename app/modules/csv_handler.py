import csv
import io
import re
from dataclasses import dataclass, field

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_ROWS = 2000
REQUIRED_HEADERS = {"name", "company"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CSVValidationError(Exception):
    """Raised for file-level problems that reject the whole upload."""


@dataclass
class RowError:
    row: int
    field: str
    message: str

    def as_dict(self):
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass
class ParsedCSV:
    valid_rows: list
    row_errors: list = field(default_factory=list)
    total_rows: int = 0


def normalize_website(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0]
    if value.startswith("www."):
        value = value[len("www."):]
    return value


def validate_and_parse_csv(contents: bytes) -> ParsedCSV:
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise CSVValidationError(
            f"File too large ({len(contents)} bytes). Max allowed is {MAX_FILE_SIZE_BYTES} bytes."
        )

    try:
        decoded = contents.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CSVValidationError(f"File is not valid UTF-8: {e}")

    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:
        raise CSVValidationError("CSV file has no header row.")

    headers = {(h or "").strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_HEADERS - headers
    if missing:
        raise CSVValidationError(f"Missing required column(s): {', '.join(sorted(missing))}")

    rows = list(reader)
    if len(rows) > MAX_ROWS:
        raise CSVValidationError(f"Too many rows ({len(rows)}). Max allowed is {MAX_ROWS}.")

    valid_rows = []
    row_errors = []

    for idx, raw_row in enumerate(rows, start=2):  # header is row 1
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        errors = []

        name = row.get("name", "")
        company = row.get("company", "")
        email = row.get("email", "")
        website = row.get("website", "")

        if not name:
            errors.append(("name", "Name is required."))
        if not company:
            errors.append(("company", "Company is required."))
        if email and not EMAIL_RE.match(email):
            errors.append(("email", f"'{email}' is not a valid email address."))

        if errors:
            for field_name, message in errors:
                row_errors.append(RowError(row=idx, field=field_name, message=message))
            continue

        valid_rows.append(
            {
                "row": idx,
                "name": name,
                "company": company,
                "industry": row.get("industry", ""),
                "country": row.get("country", ""),
                "email": email or None,
                "website": normalize_website(website) if website else None,
            }
        )

    return ParsedCSV(valid_rows=valid_rows, row_errors=row_errors, total_rows=len(rows))


def load_leads(filepath: str, filters: dict) -> list:
    """Legacy loader used by the standalone main.py CLI script. Not used by the API."""
    leads = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            match = True
            for col, val in filters.items():
                if col in row and val.lower() not in row[col].lower():
                    match = False
                    break
            if match:
                leads.append(dict(row))

    print(f"Најдени {len(leads)} лидови после филтрирање")
    return leads
