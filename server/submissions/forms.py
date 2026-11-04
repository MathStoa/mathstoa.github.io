import ipaddress
import re
from itertools import zip_longest
from urllib.parse import unquote, urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.html import format_html
from django.utils import timezone

from .models import CATEGORIES, MAX_SECONDARY_CATEGORIES, Submission

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

        own_rows = set(values("own-orcid"))
        return [{"name": person, "orcid": orcid, "email": email, "use_own_orcid": str(number) in own_rows}
                for number, (person, orcid, email) in enumerate(zip_longest(values("name"), values("orcid"), values("email"), fillvalue=""), 1)]

    def value_omitted_from_data(self, data, files, name):
        # Removing every row posts no inputs, which must clear the list rather than keep the old one.
        return False


class ContributorsField(forms.Field):
    widget = ContributorInputs
    default_error_messages = {"required": "Add at least one contributor."}
    connected_orcid = None
    expected_own_orcids = None

    def to_python(self, rows):
        contributors = []
        seen_orcids = set()
        for number, row in enumerate(rows, 1):
            name, orcid, email = (str(row.get(part) or "").strip() for part in ("name", "orcid", "email"))
            own = row.get("use_own_orcid") is True
            provenance = None
            if own:
                if not self.connected_orcid:
                    raise ValidationError(f"Contributor {number}: connect your ORCID account before choosing 'Use my connected ORCID'.")
                orcid = self.connected_orcid["url"]
                expected = (self.expected_own_orcids or {}).get(number)
                if expected and expected != orcid:
                    raise ValidationError(f"Contributor {number}: your connected ORCID changed. Edit and save this contributor before submitting.")
                provenance = {"source": "authenticated-account-selection", "provider": "orcid",
                              "identifier": orcid, "collected_at": timezone.now().isoformat(),
                              "authenticated_at": self.connected_orcid["authenticated_at"]}
            if not name and not orcid and not email and not own:
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
            if not own and self.connected_orcid and orcid == self.connected_orcid["url"]:
                raise ValidationError(f"Contributor {number}: select 'Use my connected ORCID' to collect your own iD from your authenticated account.")
            # The field is only required when submitting for moderation, so drafts can hold contributors without one.
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
            contributors.append({"name": name, "orcid": orcid, "email": email,
                                 **({"orcid_authentication": provenance} if provenance else {})})
        if len(contributors) > 100:
            raise ValidationError("Please list at most 100 contributors.")
        return contributors


class SubmissionForm(forms.ModelForm):
    collaborators = ContributorsField(
        label="Contributors", required=False,
        help_text=format_html('Add everyone who contributed to the project, including yourself, with their ORCID iD and institutional email. Write each name as first names, a comma, then the surname. Anyone can <a href="{}" target="_blank" rel="noopener noreferrer">register for an ORCID iD</a> free of charge. Email addresses are never published; a confirmation is queued for each contributor when the project is submitted; delivery is not guaranteed.', ORCID_REGISTER_URL),
    )
    secondary_categories = forms.MultipleChoiceField(
        label="Further subjects", required=False, choices=CATEGORIES,
        widget=forms.CheckboxSelectMultiple,
        help_text=f"If the project spans several areas, tick up to {MAX_SECONDARY_CATEGORIES} more.",
    )
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    permission_confirmed = forms.BooleanField(required=False, label="I maintain this project or have permission to submit it and its contributor details, and have the right to publish its abstract under CC BY 4.0.", help_text=format_html('Before submitting this saved draft, you will grant the public an irrevocable <a href="{}" target="_blank" rel="noopener noreferrer">CC BY 4.0 licence</a> to the abstract, with credit to the named project contributors. Linked repositories keep their own terms.', "https://creativecommons.org/licenses/by/4.0/"))
    # Everything else is required before a draft can be submitted for moderation.
    optional_fields = ("doi", "reading_url", "secondary_categories")

    class Meta:
        model = Submission
        fields = ["title", "repository_url", "doi", "reading_url", "abstract", "category", "secondary_categories", "collaborators", "permission_confirmed"]
        labels = {"repository_url": "Public repository", "reading_url": "Project website", "abstract": "Short abstract", "category": "Main subject"}
        help_texts = {
            "doi": format_html('If you have one, the DOI of an archived release of the repository, such as 10.5281/zenodo.1234567. <a href="{}" target="_blank" rel="noopener noreferrer">How to get a DOI for a GitHub repository</a>', DOI_GUIDE_URL),
            "reading_url": "A public website that explains the project’s aims and introduces the material in the repository.",
            "abstract": "80–1,500 characters when submitting. Use $x^2$ for inline math and $$x^2$$ for display math. Use \\$ for a literal dollar sign. HTML is displayed as text.",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Induced subgraphs of hypercubes and a proof of the Sensitivity Conjecture"}),
            "repository_url": forms.URLInput(attrs={"placeholder": "https://github.com/owner/sensitivity-conjecture"}),
            "doi": forms.TextInput(attrs={"placeholder": "10.5281/zenodo.1234567"}),
            "reading_url": forms.URLInput(attrs={"placeholder": "https://owner.github.io/sensitivity-conjecture/"}),
            "abstract": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, for_submission=False, user=None, expected_own_orcids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.for_submission = for_submission
        self.connected_orcid = None
        if user is not None and user.is_authenticated:
            from allauth.socialaccount.models import SocialAccount
            identity = SocialAccount.objects.filter(user=user, provider="orcid").first()
            if identity:
                try:
                    self.connected_orcid = {"url": normalize_orcid(identity.uid), "authenticated_at": identity.last_login.isoformat()}
                except (ValidationError, AttributeError):
                    pass
        self.fields["collaborators"].connected_orcid = self.connected_orcid
        self.fields["collaborators"].expected_own_orcids = expected_own_orcids
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

    def clean_secondary_categories(self):
        # Fields clean in Meta.fields order, so the main subject is already known; it is listed once.
        codes = [code for code in self.cleaned_data["secondary_categories"] if code != self.cleaned_data.get("category")]
        if len(codes) > MAX_SECONDARY_CATEGORIES:
            raise ValidationError(f"Please tick at most {MAX_SECONDARY_CATEGORIES} further subjects.")
        return codes

    def clean_abstract(self):
        abstract = self.cleaned_data["abstract"]
        if self.for_submission and len(abstract) < 80:
            raise ValidationError("Please provide an abstract of at least 80 characters.")
        return abstract

    def clean(self):
        cleaned = super().clean()
        # This draft flag records claimed permission. The explicit public licence
        # is accepted separately at the final submission step.
        self.instance.license_confirmed = bool(cleaned.get("permission_confirmed"))
        for field, value in list(cleaned.items()):
            if isinstance(value, str) and any(ord(char) < 32 and char not in "\n\r\t" for char in value):
                self.add_error(field, "Please remove invalid control characters.")
        return cleaned


def review_form(submission, *, user=None):
    data = {field: getattr(submission, field) for field in SubmissionForm.Meta.fields if field != "collaborators"}
    data.update({
        "revision": submission.revision,
        "collaborators-name": [person.get("name", "") for person in submission.collaborators],
        "collaborators-orcid": [person.get("orcid") or "" for person in submission.collaborators],
        "collaborators-email": [person.get("email") or "" for person in submission.collaborators],
        "collaborators-own-orcid": [str(number) for number, person in enumerate(submission.collaborators, 1) if user is not None and person.get("orcid_authentication")],
    })
    expected = {number: person.get("orcid") for number, person in enumerate(submission.collaborators, 1) if person.get("orcid_authentication")}
    return SubmissionForm(data=data, instance=submission, for_submission=True, user=user, expected_own_orcids=expected)
