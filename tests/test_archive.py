"""Publication checks for imported metadata and the announcements feed."""
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET

from scripts.feed import ATOM, render_feed
from scripts.validate import ArchiveError, load_archive, validate_archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "projects": [{
            "id": "test-project", "title": "Example project", "summary": "An example summary.",
            "description": "An example project used only by the test suite.",
            "categories": [{"code": "math.LO", "label": "Logic"}],
            "kind": "Research", "stage": "Exploration", "collaboration": "See repository",
            "repositoryUrl": "https://github.com/example/project",
            "readingUrl": "https://example.org/notes",
            "credits": [{"name": "Example contributors", "role": "Authors"}],
            "added": "2026-10-31", "example": True, "highlights": ["An example question"],
            "contributionNote": "This is an example, not an invitation.",
            "licenseNote": "See the example repository.",
        }]}

    def test_repository_archive_is_valid(self):
        load_archive()

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

    def test_example_cannot_assert_collaboration_invitation(self):
        self.data["projects"][0]["collaboration"] = "Open to contributions"
        with self.assertRaisesRegex(ArchiveError, "must not assert"):
            validate_archive(self.data)

    def test_bad_date_and_path_traversal_slug_are_rejected(self):
        for field, value in (("added", "2026-02-30"), ("id", "../../index")):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data["projects"][0][field] = value
                with self.assertRaises(ArchiveError):
                    validate_archive(data)

    def test_invalid_enums_and_missing_attribution_fail(self):
        for field, value in (("stage", "Peer reviewed"), ("kind", "Unknown"), ("credits", [])):
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
        xml = render_feed(self.data)
        root = ET.fromstring(xml)
        entries = root.findall(f"{{{ATOM}}}entry")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find(f"{{{ATOM}}}title").text, project["title"])
        self.assertNotIn("<script>", xml)
        self.assertIn("project.html?id=test-project", xml)

    def test_empty_archive_has_valid_empty_feed(self):
        root = ET.fromstring(render_feed({"version": 1, "projects": []}))
        self.assertEqual(root.findall(f"{{{ATOM}}}entry"), [])


if __name__ == "__main__":
    unittest.main()
