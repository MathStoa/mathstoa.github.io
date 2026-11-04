"""Verify vendored renderer hashes; --online also checks OSV advisories.

The existing weekly dependency workflow runs this with --online. Network failures
fail the check, rather than silently treating an unavailable advisory feed as safe.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

VENDOR = Path(__file__).resolve().parents[1] / 'docs/assets/vendor/katex-0.19.1'


def verify_files(directory=VENDOR):
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError('Vendor file outside its package')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Vendor checksum mismatch: {name}')
    return manifest


def audit_advisories(manifest):
    payload = {'package': {'name': manifest['package'], 'ecosystem': 'npm'}, 'version': manifest['version']}
    request = Request('https://api.osv.dev/v1/query', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    advisories = [item['id'] for item in result.get('vulns', [])]
    if advisories:
        raise ValueError('Vendored renderer advisories: ' + ', '.join(advisories))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true')
    args = parser.parse_args()
    manifest = verify_files()
    print(f"Verified {manifest['package']} {manifest['version']} vendor hashes")
    if args.online:
        audit_advisories(manifest)
        print('No known OSV advisories for vendored renderer')


if __name__ == '__main__':
    main()
