#!/usr/bin/env python3
"""Validate the portable mathStoa archive without third-party dependencies."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs" / "data" / "projects.json"
FIELDS = {"id", "title", "summary", "categories", "repositoryUrl", "credits", "added", "example"}
# The DOI and project website are optional.
OPTIONAL_FIELDS = {"doi", "readingUrl"}
DOI_URL = re.compile(r"https://doi\.org/10\.\d{4,9}(?:\.\d+)*/[-._;()/:A-Za-z0-9]+")


class ArchiveError(ValueError):
    """An archive is unsuitable for publication."""


def fail(path: str, message: str) -> None:
    raise ArchiveError(f"{path}: {message}")


def text(value: object, path: str, limit: int = 10000) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(path, "must be a nonempty string")
    if len(value) > limit:
        fail(path, f"must be at most {limit} characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        fail(path, "contains invalid control characters")
    return value


def https_url(value: object, path: str) -> None:
    value = text(value, path, 2000)
    decoded = unquote(value)
    if any(char.isspace() or ord(char) < 32 for char in decoded) or any(char in decoded for char in "\\<>\""):
        fail(path, "must not contain whitespace, controls, backslashes, or markup")
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "https"
            and bool(url.hostname)
            and "." in url.hostname
            and url.username is None
            and url.password is None
            and url.port in (None, 443)
            and not url.hostname.endswith(".")
        )
    except ValueError:
        valid = False
    if not valid:
        fail(path, "must be a public HTTPS URL without credentials or a custom port")


def sequence(value: object, path: str, maximum: int, allow_empty: bool = False) -> list:
    if not isinstance(value, list) or len(value) > maximum or (not value and not allow_empty):
        fail(path, f"must be a {'possibly empty ' if allow_empty else 'nonempty '}list of at most {maximum} items")
    return value


def validate_archive(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {"version", "projects"}:
        fail("archive", "must contain exactly version and projects")
    if type(data["version"]) is not int or data["version"] != 1:
        fail("version", "unsupported schema version; expected 1")
    projects = sequence(data["projects"], "projects", 10000, allow_empty=True)
    ids = set()
    for index, project in enumerate(projects):
        path = f"projects[{index}]"
        if not isinstance(project, dict) or not FIELDS <= set(project) <= FIELDS | OPTIONAL_FIELDS:
            fail(path, f"must contain these fields: {', '.join(sorted(FIELDS))}, and may also contain {', '.join(sorted(OPTIONAL_FIELDS))}")
        slug = text(project["id"], f"{path}.id", 80)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            fail(f"{path}.id", "must be a lowercase URL-safe slug")
        if slug in ids:
            fail(f"{path}.id", f"duplicate ID: {slug}")
        ids.add(slug)
        for field in ("title", "summary"):
            text(project[field], f"{path}.{field}", 200 if field == "title" else 10000)
        if type(project["example"]) is not bool:
            fail(f"{path}.example", "must be true or false, never a string")
        for field in ("repositoryUrl", "readingUrl"):
            if field in project:
                https_url(project[field], f"{path}.{field}")
        if "doi" in project and not DOI_URL.fullmatch(text(project["doi"], f"{path}.doi", 300)):
            fail(f"{path}.doi", "must be a DOI link such as https://doi.org/10.5281/zenodo.1234567")
        added = text(project["added"], f"{path}.added", 10)
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", added):
                raise ValueError
            date.fromisoformat(added)
        except ValueError:
            fail(f"{path}.added", "must be a valid YYYY-MM-DD archive date")
        codes = set()
        for category in sequence(project["categories"], f"{path}.categories", 8):
            if not isinstance(category, dict) or set(category) != {"code", "label"}:
                fail(f"{path}.categories", "each category needs exactly code and label")
            code = text(category["code"], f"{path}.categories.code", 20)
            if not re.fullmatch(r"(?:math|cs)\.[A-Z]{2}", code) or code in codes:
                fail(f"{path}.categories.code", "must be a unique math.XX or cs.XX subject code")
            codes.add(code)
            text(category["label"], f"{path}.categories.label", 100)
        for credit in sequence(project["credits"], f"{path}.credits", 100):
            if not isinstance(credit, dict) or "name" not in credit or set(credit) - {"name", "orcid"}:
                fail(f"{path}.credits", "each credit needs a name, with an optional orcid")
            text(credit["name"], f"{path}.credits.name", 200)
            if "orcid" in credit:
                identifier = text(credit["orcid"], f"{path}.credits.orcid", 37)
                if not re.fullmatch(r"https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", identifier):
                    fail(f"{path}.credits.orcid", "must be a full ORCID HTTPS identifier")
                digits = identifier.rsplit("/", 1)[1].replace("-", "")
                total = 0
                for digit in digits[:-1]:
                    total = (total + int(digit)) * 2
                check = (12 - total % 11) % 11
                if digits[-1] != ("X" if check == 10 else str(check)):
                    fail(f"{path}.credits.orcid", "has an invalid check digit")
    return data


def load_archive(path: Path = ARCHIVE) -> dict:
    return validate_archive(json.loads(path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    try:
        data = load_archive(args.path)
    except (ArchiveError, OSError, json.JSONDecodeError) as error:
        print(f"Archive validation failed: {error}", file=sys.stderr)
        return 1
    count = len(data["projects"])
    examples = sum(project["example"] for project in data["projects"])
    print(f"Archive valid: {count} entries ({examples} examples, {count - examples} submitted projects).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
