#!/usr/bin/env python3
"""Generate an Atom feed for approved, non-example mathStoa listings."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

try:
    from .validate import ARCHIVE, ROOT, load_archive, validate_archive
except ImportError:
    from validate import ARCHIVE, ROOT, load_archive, validate_archive

SITE_URL = "https://math-stoa.org/"
# Atom identity is permanent even when the feed moves to another host.
FEED_ID = "https://rosati-tom.github.io/mathstoa/"
FEED_PATH = ROOT / "docs" / "feed.xml"
LAUNCH_DATE = "2026-10-31"
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM)


def element(parent: ET.Element, name: str, text: str | None = None, **attributes: str) -> ET.Element:
    child = ET.SubElement(parent, f"{{{ATOM}}}{name}", attributes)
    child.text = text
    return child


def render_feed(data: dict) -> str:
    validate_archive(data)
    projects = sorted(
        (project for project in data["projects"] if not project["example"]),
        key=lambda project: (project["added"], project["id"]),
        reverse=True,
    )
    feed = ET.Element(f"{{{ATOM}}}feed")
    element(feed, "title", "mathΣτοά announcements")
    element(feed, "subtitle", "Approved mathematical project listings. Editorial examples are excluded.")
    element(feed, "id", FEED_ID)
    element(feed, "link", href=f"{SITE_URL}feed.xml", rel="self", type="application/atom+xml")
    element(feed, "link", href=SITE_URL, rel="alternate", type="text/html")
    latest = projects[0]["added"] if projects else LAUNCH_DATE
    element(feed, "updated", f"{latest}T00:00:00Z")
    author = element(feed, "author")
    element(author, "name", "mathΣτοά editors")
    for project in projects:
        entry = element(feed, "entry")
        link = f"{SITE_URL}project.html?id={project['id']}"
        element(entry, "id", link)
        element(entry, "title", project["title"])
        element(entry, "link", href=link, rel="alternate", type="text/html")
        element(entry, "published", f"{project['added']}T00:00:00Z")
        element(entry, "updated", f"{project['added']}T00:00:00Z")
        # Only republish abstract text when the portable record contains its
        # licence and attribution. Historical entries still announce a link.
        if project.get("summaryLicense"):
            element(entry, "summary", project["summary"], type="text")
            element(entry, "rights", "Abstract: CC BY 4.0. Attribution: " + "; ".join(project["summaryAttribution"]) + ".")
            element(entry, "link", href=project["summaryLicense"], rel="license")
            for name in project["summaryAttribution"]:
                element(element(entry, "author"), "name", name)
        else:
            element(entry, "summary", "View the project listing.", type="text")
        for category in project["categories"]:
            element(entry, "category", term=category["code"], label=category["label"])
    ET.indent(feed, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="unicode") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed feed needs regenerating")
    args = parser.parse_args()
    content = render_feed(load_archive(ARCHIVE))
    if args.check:
        if not FEED_PATH.exists() or FEED_PATH.read_text(encoding="utf-8") != content:
            print("Feed is stale. Run python3 scripts/feed.py and commit docs/feed.xml.", file=sys.stderr)
            return 1
        print("Feed matches the archive.")
        return 0
    FEED_PATH.write_text(content, encoding="utf-8")
    print(f"Generated {FEED_PATH.relative_to(ROOT)} (editorial examples excluded).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
