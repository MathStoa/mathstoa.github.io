"""Small, read-only GitHub client for repository authorization checks.

Only public github.com repositories are supported. Submitting-user access tokens
are accepted per call and never retained on the client. An optional deployment
credential for public API reads is kept privately on the client to increase the
shared API quota. An explicit user token always takes precedence. Credentials
are never included in error messages, and credential-bearing frames are marked
sensitive for Django's production error reports.
An OAuth app needs no repository-write scope for public repository metadata;
if GitHub omits permission information, verification fails closed. The UI can
then offer the verification-file method.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.views.decorators.debug import sensitive_variables


API_ORIGIN = "https://api.github.com"
API_VERSION = "2026-03-10"
VERIFICATION_FILENAME = ".mathstoa-verification"
MAX_FILE_BYTES = 8192
MAX_RESPONSE_BYTES = 1024 * 1024
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class GitHubError(Exception):
    """A safe, user-facing error; no API body or credential is retained."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class InvalidRepositoryURL(GitHubError):
    def __init__(self):
        super().__init__(
            "invalid_repository_url",
            "Enter a public repository URL like https://github.com/owner/repository.",
        )


@dataclass(frozen=True)
class RepositoryLocator:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"


@dataclass(frozen=True)
class Repository:
    repository_id: int
    owner_id: int
    owner_type: str
    owner: str
    name: str
    default_branch: str
    is_fork: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"


@dataclass(frozen=True)
class FileEvidence:
    repository: Repository
    commit_sha: str
    blob_sha: str
    content: str


@dataclass(frozen=True)
class PermissionEvidence:
    repository: Repository
    github_user_id: int
    github_login: str
    role: str


def parse_repository_url(url: str) -> RepositoryLocator:
    """Accept repository roots only; never turn submitted URLs into requests."""
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 for c in url):
        raise InvalidRepositoryURL()
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "github.com"
            or parsed.query
            or parsed.fragment
            or "?" in url
            or "#" in url
        ):
            raise InvalidRepositoryURL()
        pieces = parsed.path.removesuffix("/").split("/")
        if len(pieces) != 3 or pieces[0]:
            raise InvalidRepositoryURL()
        owner, name = pieces[1:]
        if name.endswith(".git"):
            name = name[:-4]
        if not _OWNER.fullmatch(owner) or not _NAME.fullmatch(name) or name in {".", ".."}:
            raise InvalidRepositoryURL()
    except (ValueError, UnicodeError):
        raise InvalidRepositoryURL() from None
    return RepositoryLocator(owner, name)


def _invalid_response() -> GitHubError:
    return GitHubError("invalid_response", "GitHub returned an unexpected response. Please try again.")


def _integer(value: object, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise _invalid_response()
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise _invalid_response()
    return value


def _mapping(value: object) -> dict:
    if not isinstance(value, dict):
        raise _invalid_response()
    return value


def _same_repository(expected: Repository, actual: Repository) -> None:
    if (
        expected.repository_id != actual.repository_id
        or expected.owner_id != actual.owner_id
        or expected.owner_type != actual.owner_type
        or expected.full_name.casefold() != actual.full_name.casefold()
    ):
        raise GitHubError(
            "repository_changed",
            "This repository has moved or changed owner. Start a new verification.",
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    @sensitive_variables()
    def __init__(self, *, timeout: float = 10, opener=None, public_token: str | None = None):
        self.timeout = timeout
        self._opener = opener if opener is not None else build_opener(_NoRedirect())
        self._public_token = None if public_token == "" else public_token

    @sensitive_variables()
    def _get(self, path: str, *, access_token: str | None = None) -> dict:
        # All paths are assembled below from validated/escaped fields. No API
        # response URL (including download_url) is ever followed.
        if not path.startswith("/") or path.startswith("//"):
            raise _invalid_response()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "MathStoa-repository-verification",
        }
        credential = access_token if access_token is not None else self._public_token
        if credential is not None:
            if (
                not isinstance(credential, str)
                or not credential
                or len(credential) > 8192
                or not credential.isascii()
                or any(c.isspace() or ord(c) < 33 for c in credential)
            ):
                raise GitHubError("authentication_required", "Connect your GitHub account again.")
            headers["Authorization"] = f"Bearer {credential}"
        request = Request(API_ORIGIN + path, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise _invalid_response()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            if status in {301, 302, 303, 307, 308}:
                raise GitHubError(
                    "repository_changed", "This repository has moved. Use its current URL and start again."
                ) from None
            if status == 401:
                raise GitHubError("authentication_required", "Connect your GitHub account again.") from None
            if status in {403, 429}:
                raise GitHubError(
                    "access_unavailable",
                    "GitHub could not allow this check. Try again later or use the verification file.",
                ) from None
            if status == 404:
                raise GitHubError(
                    "not_found", "The public repository, branch, or verification file could not be found."
                ) from None
            if status == 409:
                raise GitHubError("empty_repository", "Add a commit to the repository before verifying it.") from None
            raise GitHubError("github_unavailable", "GitHub is unavailable. Please try again later.") from None
        except (URLError, TimeoutError, OSError):
            raise GitHubError("github_unavailable", "GitHub is unavailable. Please try again later.") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GitHubError("response_too_large", "GitHub returned too much data to verify safely.")
        try:
            return _mapping(json.loads(raw))
        except (ValueError, UnicodeError):
            raise _invalid_response() from None

    @staticmethod
    def _base(repository: Repository | RepositoryLocator) -> str:
        return f"/repos/{quote(repository.owner, safe='')}/{quote(repository.name, safe='')}"

    @staticmethod
    def _repository(data: dict, requested: RepositoryLocator) -> Repository:
        if data.get("private") is not False or data.get("visibility") != "public":
            raise GitHubError("public_repository_required", "Only public repositories can be submitted.")
        if data.get("disabled") is not False:
            raise GitHubError("repository_disabled", "This repository is not available for verification.")
        owner = _mapping(data.get("owner"))
        owner_type = owner.get("type")
        if owner_type not in {"User", "Organization"}:
            raise _invalid_response()
        try:
            canonical = parse_repository_url(f"https://github.com/{data.get('full_name')}")
        except InvalidRepositoryURL:
            raise _invalid_response() from None
        if canonical.full_name.casefold() != requested.full_name.casefold():
            raise GitHubError("repository_changed", "Use the repository's current URL and start again.")
        if owner.get("login") != canonical.owner or data.get("name") != canonical.name:
            raise _invalid_response()
        branch = data.get("default_branch")
        if not isinstance(branch, str) or not branch or len(branch) > 255 or any(ord(c) < 32 for c in branch):
            raise GitHubError("empty_repository", "Add a default branch before verifying this repository.")
        if type(data.get("fork")) is not bool:
            raise _invalid_response()
        return Repository(
            repository_id=_integer(data.get("id")),
            owner_id=_integer(owner.get("id")),
            owner_type=owner_type,
            owner=canonical.owner,
            name=canonical.name,
            default_branch=branch,
            is_fork=data["fork"],
        )

    @sensitive_variables()
    def resolve_repository(self, url: str, access_token: str | None = None) -> Repository:
        locator = parse_repository_url(url)
        data = self._get(self._base(locator), access_token=access_token)
        return self._repository(data, locator)

    def read_verification_file(self, repository: Repository) -> FileEvidence:
        # Refresh before checking the default branch; pin all following reads to
        # the observed commit, not a mutable branch name or a caller-supplied ref.
        current = self.resolve_repository(repository.url)
        _same_repository(repository, current)
        base = self._base(current)
        ref = self._get(f"{base}/git/ref/heads/{quote(current.default_branch, safe='')}")
        if ref.get("ref") != f"refs/heads/{current.default_branch}":
            raise _invalid_response()
        obj = _mapping(ref.get("object"))
        if obj.get("type") != "commit":
            raise _invalid_response()
        commit_sha = _sha(obj.get("sha"))
        commit = self._get(f"{base}/git/commits/{commit_sha}")
        if commit.get("sha") != commit_sha:
            raise _invalid_response()
        tree_sha = _sha(_mapping(commit.get("tree")).get("sha"))
        tree = self._get(f"{base}/git/trees/{tree_sha}")
        if tree.get("sha") != tree_sha or tree.get("truncated") is not False:
            raise _invalid_response()
        entries = tree.get("tree")
        if not isinstance(entries, list):
            raise _invalid_response()
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("path") == VERIFICATION_FILENAME]
        if not matches:
            raise GitHubError("file_not_found", "Add .mathstoa-verification to the repository's default branch first.")
        if len(matches) != 1:
            raise _invalid_response()
        entry = matches[0]
        # The Contents API can silently dereference an in-repository symlink.
        # Inspecting the Git tree mode makes that unacceptable case explicit.
        if entry.get("mode") not in {"100644", "100755"} or entry.get("type") != "blob":
            raise GitHubError("not_regular_file", "The verification file must be a regular file, not a link or directory.")
        size = _integer(entry.get("size"), minimum=0)
        if size > MAX_FILE_BYTES:
            raise GitHubError("file_too_large", "The verification file must be no larger than 8 KB.")
        blob_sha = _sha(entry.get("sha"))
        blob = self._get(f"{base}/git/blobs/{blob_sha}")
        if blob.get("sha") != blob_sha or blob.get("encoding") != "base64" or blob.get("size") != size:
            raise _invalid_response()
        encoded = blob.get("content")
        if not isinstance(encoded, str) or len(encoded) > 2 * MAX_FILE_BYTES:
            raise _invalid_response()
        try:
            raw = base64.b64decode("".join(encoded.split()), validate=True)
            if len(raw) != size or len(raw) > MAX_FILE_BYTES:
                raise _invalid_response()
            # Bind the returned data to the exact Git blob selected by the tree.
            git_blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
            if hashlib.sha1(git_blob, usedforsecurity=False).hexdigest() != blob_sha:
                raise _invalid_response()
            content = raw.decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error):
            raise GitHubError("invalid_file", "The verification file must contain plain UTF-8 text.") from None
        # Catch ownership transfers or replacement at the old URL during reads.
        final = self.resolve_repository(current.url)
        _same_repository(current, final)
        if final.default_branch != current.default_branch:
            raise GitHubError("repository_changed", "The default branch changed. Check the file and try again.")
        return FileEvidence(current, commit_sha, blob_sha, content)

    @sensitive_variables()
    def verify_user_access(self, repository: Repository, access_token: str) -> PermissionEvidence:
        if not access_token:
            raise GitHubError("authentication_required", "Connect your GitHub account to verify access.")
        user = self._get("/user", access_token=access_token)
        user_id = _integer(user.get("id"))
        login = user.get("login")
        if user.get("type") != "User" or not isinstance(login, str) or not _OWNER.fullmatch(login):
            raise GitHubError("user_account_required", "Connect a personal GitHub account to verify access.")
        locator = parse_repository_url(repository.url)
        data = self._get(self._base(locator), access_token=access_token)
        current = self._repository(data, locator)
        _same_repository(repository, current)
        role = None
        if current.owner_type == "User" and current.owner_id == user_id:
            role = "owner"
        elif current.owner_type == "Organization":
            permissions = data.get("permissions", {})
            if isinstance(permissions, dict):
                if permissions.get("admin") is True:
                    role = "admin"
                elif permissions.get("maintain") is True:
                    role = "maintain"
        if role is None:
            raise GitHubError(
                "insufficient_permission",
                "GitHub could not confirm that you own this repository or maintain/administer its organization repository. Use the verification file instead.",
            )
        return PermissionEvidence(current, user_id, login, role)
