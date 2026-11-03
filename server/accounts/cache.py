"""Keep rate-limit identifiers out of stored cache keys."""
import json

from django.utils.crypto import salted_hmac


def private_cache_key(key, key_prefix, version):
    # Include every namespace component without retaining IPs, email-derived
    # keys or account IDs. A keyed digest resists offline identifier guessing.
    material = json.dumps([str(key_prefix), str(version), str(key)], separators=(",", ":"))
    return "mathstoa:" + salted_hmac(
        "mathstoa.private-cache-key", material, algorithm="sha256",
    ).hexdigest()
