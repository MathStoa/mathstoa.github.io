"""Private admin presentation of a frozen revision, including older JSON shapes.

Never fill gaps from the mutable submission or account records. Keep all source
values in the JSON disclosure, even when a value cannot be presented as a field.
"""
import json

from django.core.exceptions import ValidationError

from .forms import normalize_orcid, public_https_url
from .models import CATEGORIES


def submitted_details(snapshot):
    def text(value):
        return {"text": value if isinstance(value, str) else "", "malformed": value is not None and not isinstance(value, str)}

    def link(value, *, orcid=False):
        field = text(value)
        if field["text"]:
            try:
                field["url"] = normalize_orcid(value) if orcid else public_https_url(value)
            except ValidationError:
                pass  # Keep unsafe or invalid older links as exact, escaped text.
        return field

    labels = dict(CATEGORIES)

    def category(value):
        field = text(value)
        field["label"] = labels.get(field["text"], "")
        return field

    context = {
        "snapshot_is_object": isinstance(snapshot, dict),
        "raw_json": json.dumps(snapshot, ensure_ascii=False, indent=2),
    }
    if not context["snapshot_is_object"]:
        return context
    context.update({
        "title": text(snapshot.get("title")),
        "abstract": text(snapshot.get("abstract")),
        "main_category": category(snapshot.get("category")),
        "project_links": [
            {"label": label, **link(snapshot.get(key))}
            for key, label in (("repository_url", "Repository"), ("doi", "DOI"), ("reading_url", "Project website"))
        ],
    })
    secondary = snapshot.get("secondary_categories", [])
    context["secondary_malformed"] = not isinstance(secondary, list)
    context["secondary_categories"] = [category(value) for value in secondary] if isinstance(secondary, list) else []
    contributors = snapshot.get("collaborators", [])
    context["contributors_malformed"] = not isinstance(contributors, list)
    context["contributors"] = [
        {"name": text(person.get("name")), "email": text(person.get("email")), "orcid": link(person.get("orcid"), orcid=True)}
        if isinstance(person, dict) else {"malformed": True}
        for person in contributors
    ] if isinstance(contributors, list) else []
    return context
