import ipaddress
import re
from urllib.parse import unquote, urlsplit

from django import forms
from django.core.exceptions import ValidationError

from .models import Submission


def public_https_url(value):
    if not value:
        return value
    decoded = unquote(value)
    if any(c.isspace() or ord(c) < 32 for c in decoded) or any(c in decoded for c in '\\<>"'):
        raise ValidationError("Use a public HTTPS link without spaces, credentials or unusual characters.")
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or "." not in url.hostname or url.username or url.password or url.port not in (None, 443) or url.hostname.endswith("."):
            raise ValueError
        try:
            if not ipaddress.ip_address(url.hostname).is_global:
                raise ValueError
        except ValueError as error:
            if re.fullmatch(r"[0-9.]+", url.hostname) or ":" in url.hostname:
                raise ValueError from error
        if url.hostname.endswith((".localhost", ".local", ".internal")):
            raise ValueError
    except ValueError:
        raise ValidationError("Use a public HTTPS link without a password or a custom port.") from None
    return value


def normalize_orcid(value):
    value = value.strip().removeprefix("https://orcid.org/")
    if not value:
        return ""
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value):
        raise ValidationError("An ORCID must look like 0000-0002-1825-0097, or its https://orcid.org/ link.")
    digits = value.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    if digits[-1] != ("X" if check == 10 else str(check)):
        raise ValidationError("This ORCID has an invalid check digit. Please check the identifier.")
    return "https://orcid.org/" + value


def format_collaborators(collaborators):
    return "\n".join(" | ".join(author.get(key, "") for key in ("name", "role", "orcid", "affiliation")).rstrip(" |") for author in collaborators)


class SubmissionForm(forms.ModelForm):
    collaborators_text = forms.CharField(
        label="Contributors and affiliations", required=False, max_length=30000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One person per line: Name | Role | ORCID (optional) | Affiliation (optional). Include yourself. These details will be public after approval; they do not grant editing access. An entered ORCID is supplied attribution, not an authenticated identity.",
    )
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    permission_confirmed = forms.BooleanField(required=False, label="I maintain this project or have permission to submit it and its contributor details.")
    license_confirmed = forms.BooleanField(required=False, label="I have permission to publish this original abstract under CC BY 4.0. The linked project keeps its own licence.")

    class Meta:
        model = Submission
        fields = ["title", "repository_url", "reading_url", "abstract", "category", "kind", "stage", "collaboration", "contribution_note", "license_note", "permission_confirmed", "license_confirmed"]
        labels = {"repository_url": "Public repository", "reading_url": "Project website", "abstract": "Short abstract", "category": "Main subject", "kind": "Kind of project", "stage": "Research stage", "collaboration": "Contribution preferences", "contribution_note": "Contribution and credit policy", "license_note": "Repository licence or reuse terms"}
        help_texts = {"title": "Required to save a draft. You can complete the other details before submitting.", "abstract": "80–1,500 characters when submitting for review.", "contribution_note": "Explain how to contribute and how contributions will be credited, or point readers to the project's policy.", "license_note": "Name the repository licence, or explain where readers can find its reuse terms."}
        widgets = {"abstract": forms.Textarea(attrs={"rows": 5}), "contribution_note": forms.Textarea(attrs={"rows": 3}), "license_note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, for_submission=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.for_submission = for_submission
        self.initial.setdefault("revision", self.instance.revision)
        self.initial.setdefault("collaborators_text", format_collaborators(self.instance.collaborators))
        if for_submission:
            for name in ("repository_url", "reading_url", "abstract", "category", "collaborators_text", "contribution_note", "license_note", "permission_confirmed", "license_confirmed"):
                self.fields[name].required = True

    def clean_repository_url(self):
        return public_https_url(self.cleaned_data["repository_url"])

    def clean_reading_url(self):
        return public_https_url(self.cleaned_data["reading_url"])

    def clean_abstract(self):
        abstract = self.cleaned_data["abstract"]
        if self.for_submission and len(abstract) < 80:
            raise ValidationError("Please provide an abstract of at least 80 characters.")
        return abstract

    def clean(self):
        cleaned = super().clean()
        for field, value in list(cleaned.items()):
            if isinstance(value, str) and any(ord(char) < 32 and char not in "\n\r\t" for char in value):
                self.add_error(field, "Please remove invalid control characters.")
        return cleaned

    def clean_collaborators_text(self):
        authors = []
        seen_orcids = set()
        for number, line in enumerate(self.cleaned_data["collaborators_text"].splitlines(), 1):
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) > 4 or not parts[0]:
                raise ValidationError(f"Line {number}: use Name | Role | ORCID | Affiliation.")
            parts += [""] * (4 - len(parts))
            name, role, orcid, affiliation = parts
            if any(len(value) > 200 for value in (name, role, affiliation)):
                raise ValidationError(f"Line {number}: names, roles and affiliations must each be at most 200 characters.")
            orcid = normalize_orcid(orcid)
            if orcid and orcid in seen_orcids:
                raise ValidationError(f"Line {number}: this ORCID is already listed.")
            if orcid:
                seen_orcids.add(orcid)
            authors.append({"name": name, "role": role or "Contributor", "orcid": orcid, "affiliation": affiliation})
        if len(authors) > 100:
            raise ValidationError("Please list at most 100 contributors.")
        self.parsed_collaborators = authors
        return self.cleaned_data["collaborators_text"]

    def save(self, commit=True):
        submission = super().save(commit=False)
        submission.collaborators = self.parsed_collaborators
        if commit:
            submission.save()
        return submission


def review_form(submission):
    data = {field: getattr(submission, field) for field in SubmissionForm.Meta.fields}
    data.update(revision=submission.revision, collaborators_text=format_collaborators(submission.collaborators))
    return SubmissionForm(data=data, instance=submission, for_submission=True)
