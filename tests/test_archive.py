"""Publication checks for imported metadata and the announcements feed."""
from copy import deepcopy
import ast
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.feed import ATOM, render_feed
from scripts.validate import ArchiveError, load_archive, validate_archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "projects": [{
            "id": "test-project", "title": "Example project", "summary": "An example summary.",
            "categories": [{"code": "math.LO", "label": "Logic"}],
            "repositoryUrl": "https://github.com/example/project",
            "doi": "https://doi.org/10.5281/zenodo.1234567",
            "readingUrl": "https://example.org/notes",
            "credits": [{"name": "Example contributors"}],
            "added": "2026-10-31", "example": True,
        }]}

    def test_repository_archive_is_valid(self):
        load_archive()

    def test_legacy_submission_routes_offer_the_same_versioned_explicit_grant(self):
        root = Path(__file__).resolve().parents[1]
        declarations = {}
        for node in ast.parse((root / "server/submissions/publication.py").read_text()).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"DECLARATION", "DECLARATION_VERSION"}:
                declarations[node.targets[0].id] = ast.literal_eval(node.value)
        for path in ("docs/submit.html", "docs/assets/submit.js", ".github/ISSUE_TEMPLATE/submit-project.yml"):
            with self.subTest(path=path):
                text = (root / path).read_text()
                self.assertIn(declarations["DECLARATION"], text)
                self.assertIn("Declaration version " + declarations["DECLARATION_VERSION"], text)
                self.assertIn("https://creativecommons.org/licenses/by/4.0/", text)

    def test_examples_do_not_announce_participation(self):
        root = ET.fromstring(render_feed(self.data))
        self.assertEqual(root.findall(f"{{{ATOM}}}entry"), [])
        self.assertIsNotNone(root.find(f"{{{ATOM}}}updated"))

    def test_duplicate_ids_cannot_create_ambiguous_project_links(self):
        self.data["projects"].append(deepcopy(self.data["projects"][0]))
        with self.assertRaisesRegex(ArchiveError, "duplicate ID"):
            validate_archive(self.data)

    def test_hostile_and_unexpected_url_schemes_are_rejected(self):
        for url in (
            "javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
            "//github.com/project", "http://github.com/project", "https://user:password@github.com/repo",
            "https://github.com/%0Ainjected", "https://github.com\\@evil.example/repo",
            "https://localhost/repo", "https://github.com:1234/repo",
        ):
            with self.subTest(url=url):
                data = deepcopy(self.data)
                data["projects"][0]["repositoryUrl"] = url
                with self.assertRaises(ArchiveError):
                    validate_archive(data)

    def test_string_false_cannot_accidentally_publish_an_example(self):
        self.data["projects"][0]["example"] = "false"
        with self.assertRaisesRegex(ArchiveError, "must be true or false"):
            validate_archive(self.data)

    def test_bad_date_and_path_traversal_slug_are_rejected(self):
        for field, value in (("added", "2026-02-30"), ("id", "../../index")):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data["projects"][0][field] = value
                with self.assertRaises(ArchiveError):
                    validate_archive(data)

    def test_missing_attribution_and_removed_fields_fail(self):
        for field, value in (("credits", []), ("stage", "Exploration"), ("licenseNote", "MIT"), ("description", "A longer text"), ("highlights", ["A bullet"])):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data["projects"][0][field] = value
                with self.assertRaises(ArchiveError):
                    validate_archive(data)

    def test_feed_publishes_only_approved_records_and_escapes_text(self):
        project = self.data["projects"][0]
        project["example"] = False
        project["title"] = 'A <script>alert("test")</script> & B'
        project["summary"] = "a < b & b > c"
        project["summaryLicense"] = "https://creativecommons.org/licenses/by/4.0/"
        project["summaryAttribution"] = ["Ada Example", "Ben Example"]
        xml = render_feed(self.data)
        root = ET.fromstring(xml)
        entries = root.findall(f"{{{ATOM}}}entry")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find(f"{{{ATOM}}}title").text, project["title"])
        self.assertNotIn("<script>", xml)
        self.assertIn("project.html?id=test-project", xml)
        self.assertEqual(entries[0].find(f"{{{ATOM}}}summary").text, project["summary"])
        self.assertEqual([author.find(f"{{{ATOM}}}name").text for author in entries[0].findall(f"{{{ATOM}}}author")], project["summaryAttribution"])
        self.assertIn("CC BY 4.0", entries[0].find(f"{{{ATOM}}}rights").text)
        self.assertEqual(entries[0].find(f"{{{ATOM}}}link[@rel='license']").attrib["href"], project["summaryLicense"])

    def test_legacy_feed_does_not_invent_licence_or_republish_unrecorded_abstract(self):
        self.data["projects"][0]["example"] = False
        xml = render_feed(self.data)
        entry = ET.fromstring(xml).find(f"{{{ATOM}}}entry")
        self.assertNotIn(self.data["projects"][0]["summary"], xml)
        self.assertIsNone(entry.find(f"{{{ATOM}}}link[@rel='license']"))

    def test_abstract_licence_requires_attribution_and_cannot_cover_external_work(self):
        project = self.data["projects"][0]
        project["summaryLicense"] = "https://creativecommons.org/licenses/by/4.0/"
        with self.assertRaises(ArchiveError):
            validate_archive(self.data)
        project["summaryAttribution"] = ["Example contributors"]
        validate_archive(self.data)
        project["summaryLicense"] = "https://example.org/unverified-license"
        with self.assertRaises(ArchiveError):
            validate_archive(self.data)

    def test_feed_retains_identity_when_its_host_changes(self):
        self.data["projects"][0]["example"] = False
        root = ET.fromstring(render_feed(self.data))
        self.assertEqual(root.find(f"{{{ATOM}}}id").text, "https://rosati-tom.github.io/mathstoa/")
        links = {link.attrib["rel"]: link.attrib["href"] for link in root.findall(f"{{{ATOM}}}link")}
        self.assertEqual(links["self"], "https://math-stoa.org/feed.xml")
        self.assertEqual(links["alternate"], "https://math-stoa.org/")
        entry = root.find(f"{{{ATOM}}}entry")
        self.assertEqual(entry.find(f"{{{ATOM}}}link").attrib["href"], "https://math-stoa.org/project.html?id=test-project")
        with patch("scripts.feed.SITE_URL", "https://research.example.org/mathstoa/"):
            moved = ET.fromstring(render_feed(self.data))
        self.assertEqual(moved.find(f"{{{ATOM}}}id").text, root.find(f"{{{ATOM}}}id").text)
        moved_links = {link.attrib["rel"]: link.attrib["href"] for link in moved.findall(f"{{{ATOM}}}link")}
        self.assertEqual(moved_links["self"], "https://research.example.org/mathstoa/feed.xml")
        moved_entry = moved.find(f"{{{ATOM}}}entry")
        self.assertEqual(moved_entry.find(f"{{{ATOM}}}link").attrib["href"], "https://research.example.org/mathstoa/project.html?id=test-project")

    def test_empty_archive_has_valid_empty_feed(self):
        root = ET.fromstring(render_feed({"version": 1, "projects": []}))
        self.assertEqual(root.findall(f"{{{ATOM}}}entry"), [])

    def test_doi_and_website_are_optional_but_checked(self):
        project = self.data["projects"][0]
        del project["doi"], project["readingUrl"]
        validate_archive(self.data)
        for doi in ("10.5281/zenodo.1234567", "https://doi.org/10.5281/<b>", "https://example.org/10.5281/zenodo.1", ""):
            with self.subTest(doi=doi):
                project["doi"] = doi
                with self.assertRaises(ArchiveError):
                    validate_archive(self.data)

    def test_optional_public_contributor_identifiers(self):
        credit = self.data["projects"][0]["credits"][0]
        credit["orcid"] = "https://orcid.org/0000-0002-1825-0097"
        credit["orcidAuthenticated"] = False
        validate_archive(self.data)
        credit["orcidAuthenticated"] = "true"
        with self.assertRaisesRegex(ArchiveError, "must be a boolean"):
            validate_archive(self.data)
        credit["orcidAuthenticated"] = False
        for key, value in (("email", "private@example.org"), ("affiliation", "Example University"), ("role", "Author")):
            with self.subTest(key=key):
                credit[key] = value
                with self.assertRaises(ArchiveError):
                    validate_archive(self.data)
                del credit[key]
        credit["orcid"] = "https://orcid.org/0000-0002-1825-0098"
        with self.assertRaisesRegex(ArchiveError, "check digit"):
            validate_archive(self.data)


if __name__ == "__main__":
    unittest.main()
