"""Dependency provenance and fail-closed advisory monitoring."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.audit_vendor import audit_advisories, verify_files


class VendorTests(unittest.TestCase):
    def test_checked_in_renderer_matches_manifest(self):
        self.assertEqual(verify_files()['version'], '0.19.1')

    def test_modified_vendor_file_fails_integrity_check(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'renderer.js').write_text('changed code')
            (directory / 'manifest.json').write_text(json.dumps({'files': {'renderer.js': 'incorrect'}}))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                verify_files(directory)

    def test_known_advisory_fails_the_check(self):
        with patch('scripts.audit_vendor.urlopen', return_value=BytesIO(b'{"vulns":[{"id":"TEST-advisory"}]}')):
            with self.assertRaisesRegex(ValueError, 'TEST-advisory'):
                audit_advisories({'package': 'katex', 'version': '0.19.1'})

    def test_unavailable_advisory_feed_is_not_reported_as_safe(self):
        with patch('scripts.audit_vendor.urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                audit_advisories({'package': 'katex', 'version': '0.19.1'})
