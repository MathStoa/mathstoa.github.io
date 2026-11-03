import ipaddress
import re
from itertools import zip_longest
from urllib.parse import unquote, urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.html import format_html

from .models import Submission

DOI_GUIDE_URL = "https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content#issuing-a-persistent-identifier-for-your-repository-with-zenodo"
ORCID_REGISTER_URL = "https://orcid.org/register"


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


def normalize_doi(value):
    value = value.strip()
    if not value:
        return ""
    doi = re.sub(r"^(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.IGNORECASE)
    # Crossref's recommended pattern: it matches Zenodo and nearly all other DOIs, and is safe in a link.
    if len(doi) > 200 or not re.fullmatch(r"10\.\d{4,9}(?:\.\d+)*/[-._;()/:A-Za-z0-9]+", doi):
        raise ValidationError("A DOI must look like 10.5281/zenodo.1234567, or its https://doi.org/ link.")
    return "https://doi.org/" + doi


class ContributorInputs(forms.Widget):
    """Name, ORCID and email inputs per contributor; submissions/form.html renders the rows."""

    def value_from_datadict(self, data, files, name):
        getter = getattr(data, "getlist", data.get)

        def values(part):
            value = getter(f"{name}-{part}")
            return [value] if isinstance(value, str) else list(value or ())

        return [{"name": person, "orcid": orcid, "email": email} for person, orcid, email in zip_longest(values("name"), values("orcid"), values("email"), fillvalue="")]

    def value_omitted_from_data(self, data, files, name):
        # Removing every row posts no inputs, which must clear the list rather than keep the old one.
        return False


class ContributorsField(forms.Field):
    widget = ContributorInputs
    default_error_messages = {"required": "Add at least one contributor."}

    def to_python(self, rows):
        contributors = []
        seen_orcids = set()
        for number, row in enumerate(rows, 1):
            name, orcid, email = (str(row.get(part) or "").strip() for part in ("name", "orcid", "email"))
            if not name and not orcid and not email:
                continue
            if not name:
                raise ValidationError(f"Contributor {number}: add their first names and surname.")
            first, comma, surname = name.partition(",")
            if comma and "," not in surname:
                name = f"{first.strip()}, {surname.strip()}"
            if self.required and not (comma and first.strip() and surname.strip() and "," not in surname):
                raise ValidationError(f"Contributor {number}: write the name as first names, a comma, then the surname.")
            if len(name) > 200:
                raise ValidationError(f"Contributor {number}: the name must be at most 200 characters.")
            if any(ord(char) < 32 for char in name):
                raise ValidationError(f"Contributor {number}: please remove invalid control characters.")
            try:
                orcid = normalize_orcid(orcid)
            except ValidationError as error:
                raise ValidationError(f"Contributor {number}: {error.messages[0]}") from None
            # The field is only required when submitting for review, so drafts can hold contributors without one.
            if self.required and not orcid:
                raise ValidationError(f"Contributor {number}: add their ORCID iD.")
            if orcid and orcid in seen_orcids:
                raise ValidationError(f"Contributor {number}: this ORCID is already listed.")
            if orcid:
                seen_orcids.add(orcid)
            if email:
                try:
                    validate_email(email)
                except ValidationError:
                    raise ValidationError(f"Contributor {number}: enter a valid email address.") from None
                if len(email) > 254:
                    raise ValidationError(f"Contributor {number}: the email address must be at most 254 characters.")
            if self.required and not email:
                raise ValidationError(f"Contributor {number}: add their institutional email.")
            contributors.append({"name": name, "orcid": orcid, "email": email})
        if len(contributors) > 100:
            raise ValidationError("Please list at most 100 contributors.")
        return contributors


class SubmissionForm(forms.ModelForm):
    collaborators = ContributorsField(
        label="Contributors", required=False,
        help_text=format_html('Add everyone who contributed to the project, including yourself, with their ORCID iD and institutional email. Write each name as first names, a comma, then the surname. Anyone can <a href="{}" target="_blank" rel="noopener noreferrer">register for an ORCID iD</a> free of charge. Email addresses are never published; each contributor receives a confirmation when the project is submitted for review.', ORCID_REGISTER_URL),
    )
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    permission_confirmed = forms.BooleanField(required=False, label="I maintain this project or have permission to submit it and its contributor details, and have the right to publish its abstract under CC BY 4.0.")
    # Everything else is required before a draft can be submitted for review.
    optional_fields = ("doi", "reading_url")

    class Meta:
        model = Submission
        fields = ["title", "repository_url", "doi", "reading_url", "abstract", "category", "collaborators", "permission_confirmed"]
        labels = {"repository_url": "Public repository", "reading_url": "Project website", "abstract": "Short abstract", "category": "Main subject"}
        help_texts = {
            "doi": format_html('If you have one, the DOI of an archived release of the repository, such as 10.5281/zenodo.1234567. <a href="{}" target="_blank" rel="noopener noreferrer">How to get a DOI for a GitHub repository</a>', DOI_GUIDE_URL),
            "reading_url": "A public website that explains the project’s aims and introduces the material in the repository.",
            "abstract": "80–1,500 characters when submitting for review.",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Induced subgraphs of hypercubes and a proof of the Sensitivity Conjecture"}),
            "repository_url": forms.URLInput(attrs={"placeholder": "https://github.com/owner/sensitivity-conjecture"}),
            "doi": forms.TextInput(attrs={"placeholder": "10.5281/zenodo.1234567"}),
            "reading_url": forms.URLInput(attrs={"placeholder": "https://owner.github.io/sensitivity-conjecture/"}),
            "abstract": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, for_submission=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.for_submission = for_submission
        self.initial.setdefault("revision", self.instance.revision)
        if for_submission:
            for name, field in self.fields.items():
                field.required = name not in self.optional_fields

    def clean_repository_url(self):
        return public_https_url(self.cleaned_data["repository_url"])

    def clean_doi(self):
        return normalize_doi(self.cleaned_data["doi"])

    def clean_reading_url(self):
        return public_https_url(self.cleaned_data["reading_url"])

    def clean_abstract(self):
        abstract = self.cleaned_data["abstract"]
        if self.for_submission and len(abstract) < 80:
            raise ValidationError("Please provide an abstract of at least 80 characters.")
        return abstract

    def clean(self):
        cleaned = super().clean()
        # The one confirmation also grants the abstract licence; the model keeps both flags.
        self.instance.license_confirmed = bool(cleaned.get("permission_confirmed"))
        for field, value in list(cleaned.items()):
            if isinstance(value, str) and any(ord(char) < 32 and char not in "\n\r\t" for char in value):
                self.add_error(field, "Please remove invalid control characters.")
        return cleaned


def review_form(submission):
    data = {field: getattr(submission, field) for field in SubmissionForm.Meta.fields if field != "collaborators"}
    data.update({
        "revision": submission.revision,
        "collaborators-name": [person.get("name", "") for person in submission.collaborators],
        "collaborators-orcid": [person.get("orcid") or "" for person in submission.collaborators],
        "collaborators-email": [person.get("email") or "" for person in submission.collaborators],
    })
    return SubmissionForm(data=data, instance=submission, for_submission=True)
