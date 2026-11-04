"""The declaration accepted for each newly submitted revision."""
from datetime import datetime
from django.utils import timezone

ABSTRACT_LICENSE = "https://creativecommons.org/licenses/by/4.0/"
DECLARATION_VERSION = "2026-11-04.1"
DECLARATION = (
    "I maintain this project or have permission to submit it and its contributor details. "
    "I have the necessary rights and grant the public permission to reuse this abstract "
    "under Creative Commons Attribution 4.0 International (CC BY 4.0), with attribution "
    "to the named project contributors. I understand that this licence is irrevocable "
    "and allows commercial reuse and adaptations. Linked repositories retain their own terms."
)


def accepted_declaration():
    return {
        "version": DECLARATION_VERSION,
        "text": DECLARATION,
        "license": ABSTRACT_LICENSE,
        "accepted_at": timezone.now().isoformat(),
    }


def authenticated_orcid(contributor):
    """Only our stored OAuth-account selection evidence can authenticate an iD."""
    proof = contributor.get("orcid_authentication")
    if not isinstance(proof, dict):
        return False
    if proof.get("source") != "authenticated-account-selection" or proof.get("provider") != "orcid" or proof.get("identifier") != contributor.get("orcid"):
        return False
    try:
        return all(datetime.fromisoformat(proof[field]).tzinfo is not None for field in ("authenticated_at", "collected_at"))
    except (KeyError, ValueError, TypeError):
        return False
