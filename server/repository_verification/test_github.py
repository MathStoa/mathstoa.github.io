import base64
import hashlib
import io
import json
import unittest
from urllib.error import HTTPError, URLError

from .github import (
    API_ORIGIN,
    GitHubClient,
    GitHubError,
    InvalidRepositoryURL,
    MAX_FILE_BYTES,
    MAX_RESPONSE_BYTES,
    Repository,
    _NoRedirect,
    parse_repository_url,
)


COMMIT = "a" * 40
TREE = "b" * 40
REPOSITORY = Repository(123, 42, "User", "researcher", "proofs", "main")


def repository_data(**changes):
    data = {
        "id": 123,
        "owner": {"id": 42, "login": "researcher", "type": "User"},
        "name": "proofs",
        "full_name": "researcher/proofs",
        "private": False,
        "visibility": "public",
        "disabled": False,
        "fork": False,
        "default_branch": "main",
    }
    data.update(changes)
    return data


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if not self.responses:
            raise AssertionError("Unexpected extra GitHub request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, bytes):
            response = json.dumps(response).encode()
        return Response(response)


def file_responses(content=b"mathstoa-verification:example\n", branch="main"):
    blob_sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    return [
        repository_data(default_branch=branch),
        {"ref": f"refs/heads/{branch}", "object": {"type": "commit", "sha": COMMIT}},
        {"sha": COMMIT, "tree": {"sha": TREE}},
        {"sha": TREE, "truncated": False, "tree": [
            {"path": ".mathstoa-verification", "type": "blob", "mode": "100644", "sha": blob_sha, "size": len(content)}
        ]},
        {"sha": blob_sha, "encoding": "base64", "content": base64.b64encode(content).decode(), "size": len(content)},
        repository_data(default_branch=branch),
    ]


class URLTests(unittest.TestCase):
    def test_repository_roots_and_clone_suffix(self):
        for url in ["https://github.com/researcher/proofs", "https://github.com/researcher/proofs.git", "https://github.com/researcher/proofs/"]:
            with self.subTest(url=url):
                locator = parse_repository_url(url)
                self.assertEqual(locator.full_name, "researcher/proofs")
                self.assertEqual(locator.url, REPOSITORY.url)

    def test_rejects_unsafe_or_non_root_urls(self):
        invalid = [
            "http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com.evil.test/a/b",
            "https://github.com:443/a/b", "https://alice@github.com/a/b", "https://github.com@evil.test/a/b",
            "https://github.com/a/b?ref=main", "https://github.com/a/b?", "https://github.com/a/b#", "https://github.com/a/b#readme",
            "https://github.com/a/b/tree/main", "https://github.com/a/b/pull/1", "https://github.com/a/b//",
            "https://github.com/a/..", "https://github.com/a/.", "https://github.com/a/%2e%2e", "https://github.com/a%2fb/c",
            "https://github.com/a/.git", "https://github.com//b", "https://github.com/a/", "https://github.com/a",
            "https://github.com/a/b\\evil", "https://github.com/a/b\n", " https://github.com/a/b", "https://github.com/a/привет",
            "https://[github.com/a/b", "file:///etc/passwd", "ssh://git@github.com/a/b", "//github.com/a/b", None, "",
        ]
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(InvalidRepositoryURL):
                    parse_repository_url(url)


class ErrorAssertionMixin:
    def assert_error(self, code, callback):
        with self.assertRaises(GitHubError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class ClientTests(ErrorAssertionMixin, unittest.TestCase):
    def test_resolves_public_repository_without_token(self):
        opener = Opener(repository_data())
        result = GitHubClient(opener=opener).resolve_repository(REPOSITORY.url)
        self.assertEqual(result, REPOSITORY)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, API_ORIGIN + "/repos/researcher/proofs")
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(timeout, 10)

    def test_case_variations_do_not_change_repository_identity(self):
        opener = Opener(repository_data())
        result = GitHubClient(opener=opener).resolve_repository("https://github.com/Researcher/Proofs")
        self.assertEqual(result, REPOSITORY)

    def test_invalid_repository_url_never_reaches_network(self):
        opener = Opener()
        self.assert_error("invalid_repository_url", lambda: GitHubClient(opener=opener).resolve_repository("https://127.0.0.1/a/b"))
        self.assertEqual(opener.calls, [])

    def test_rejects_private_disabled_or_unexpected_repository_metadata(self):
        cases = [
            ({"private": True}, "public_repository_required"),
            ({"visibility": "internal"}, "public_repository_required"),
            ({"disabled": True}, "repository_disabled"),
            ({"id": True}, "invalid_response"),
            ({"id": 0}, "invalid_response"),
            ({"default_branch": None}, "empty_repository"),
            ({"default_branch": ""}, "empty_repository"),
            ({"full_name": "elsewhere/proofs"}, "repository_changed"),
            ({"owner": {"id": 42, "login": "researcher", "type": "Bot"}}, "invalid_response"),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes):
                client = GitHubClient(opener=Opener(repository_data(**changes)))
                self.assert_error(code, lambda: client.resolve_repository(REPOSITORY.url))

    def test_fork_identity_is_preserved(self):
        result = GitHubClient(opener=Opener(repository_data(fork=True))).resolve_repository(REPOSITORY.url)
        self.assertTrue(result.is_fork)
        self.assertEqual(result.repository_id, 123)

    def test_transport_and_status_errors_do_not_leak_bodies_or_tokens(self):
        for status, code in [(301, "repository_changed"), (302, "repository_changed"), (307, "repository_changed"), (308, "repository_changed"), (401, "authentication_required"), (403, "access_unavailable"), (404, "not_found"), (409, "empty_repository"), (429, "access_unavailable"), (500, "github_unavailable")]:
            with self.subTest(status=status):
                error = HTTPError(API_ORIGIN, status, "secret-token", {}, io.BytesIO(b"secret-body"))
                client = GitHubClient(opener=Opener(error))
                caught = self.assert_error(code, lambda: client.resolve_repository(REPOSITORY.url, "secret-token"))
                self.assertNotIn("secret", str(caught))
        for error in [URLError("secret-token"), TimeoutError("secret-token"), OSError("secret-token")]:
            caught = self.assert_error("github_unavailable", lambda: GitHubClient(opener=Opener(error)).resolve_repository(REPOSITORY.url))
            self.assertNotIn("secret", str(caught))

    def test_redirect_handler_refuses_new_destinations(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_bounded_and_validated_responses(self):
        for body, code in [(b"x" * (MAX_RESPONSE_BYTES + 1), "response_too_large"), (b"not json", "invalid_response"), (b"\xff", "invalid_response"), ([], "invalid_response"), (None, "invalid_response")]:
            with self.subTest(code=code):
                self.assert_error(code, lambda: GitHubClient(opener=Opener(body)).resolve_repository(REPOSITORY.url))

    def test_tokens_are_not_retained_and_invalid_headers_are_rejected(self):
        client = GitHubClient(opener=Opener(repository_data()))
        client.resolve_repository(REPOSITORY.url, "example-token")
        self.assertNotIn("example-token", repr(client.__dict__))
        # The test opener stores requests; the production opener does not.
        self.assertNotIn("access_token", client.__dict__)
        for token in ["", "bad\ntoken", "bad\rtoken", "bad token", "é", "a" * 8193]:
            with self.subTest(token_length=len(token)):
                self.assert_error("authentication_required", lambda: GitHubClient(opener=Opener()).resolve_repository(REPOSITORY.url, token))

    def test_public_read_credential_is_optional_and_not_in_client_repr(self):
        opener = Opener(repository_data())
        client = GitHubClient(opener=opener, public_token="deployment-read-token")
        client.resolve_repository(REPOSITORY.url)
        self.assertEqual(opener.calls[0][0].headers["Authorization"], "Bearer deployment-read-token")
        self.assertNotIn("deployment-read-token", repr(client))
        self.assertEqual(opener.calls[0][0].full_url, API_ORIGIN + "/repos/researcher/proofs")
        opener = Opener(repository_data())
        GitHubClient(opener=opener, public_token="").resolve_repository(REPOSITORY.url)
        self.assertNotIn("Authorization", opener.calls[0][0].headers)

    def test_explicit_user_credential_overrides_public_read_credential(self):
        opener = Opener({"id": 42, "login": "researcher", "type": "User"}, repository_data())
        client = GitHubClient(opener=opener, public_token="deployment-read-token")
        evidence = client.verify_user_access(REPOSITORY, "submitting-user-token")
        self.assertEqual(evidence.role, "owner")
        self.assertTrue(all(request.headers["Authorization"] == "Bearer submitting-user-token" for request, _ in opener.calls))
        self.assertNotIn("submitting-user-token", repr(client.__dict__))

    def test_invalid_user_credential_never_falls_back_to_public_credential(self):
        opener = Opener()
        client = GitHubClient(opener=opener, public_token="deployment-read-token")
        self.assert_error("authentication_required", lambda: client.verify_user_access(REPOSITORY, ""))
        self.assert_error("authentication_required", lambda: client.resolve_repository(REPOSITORY.url, "bad\ntoken"))
        self.assertEqual(opener.calls, [])

    def test_public_credential_is_not_exposed_by_api_errors(self):
        error = HTTPError(API_ORIGIN, 403, "deployment-secret-token", {}, io.BytesIO(b"deployment-secret-token"))
        client = GitHubClient(opener=Opener(error), public_token="deployment-secret-token")
        caught = self.assert_error("access_unavailable", lambda: client.resolve_repository(REPOSITORY.url))
        self.assertNotIn("deployment-secret-token", str(caught))


class FileTests(ErrorAssertionMixin, unittest.TestCase):
    def test_public_credential_applies_to_all_pinned_file_requests(self):
        opener = Opener(*file_responses())
        result = GitHubClient(opener=opener, public_token="deployment-read-token").read_verification_file(REPOSITORY)
        self.assertEqual(result.commit_sha, COMMIT)
        self.assertTrue(all(request.headers["Authorization"] == "Bearer deployment-read-token" for request, _ in opener.calls))
        self.assertTrue(all(request.full_url.startswith(API_ORIGIN + "/") for request, _ in opener.calls))

    def test_reads_exact_default_branch_commit_and_regular_blob(self):
        opener = Opener(*file_responses())
        result = GitHubClient(opener=opener).read_verification_file(REPOSITORY)
        self.assertEqual(result.content, "mathstoa-verification:example\n")
        self.assertEqual(result.commit_sha, COMMIT)
        self.assertEqual(result.repository, REPOSITORY)
        urls = [request.full_url for request, _ in opener.calls]
        self.assertIn(f"{API_ORIGIN}/repos/researcher/proofs/git/commits/{COMMIT}", urls)
        self.assertIn(f"{API_ORIGIN}/repos/researcher/proofs/git/trees/{TREE}", urls)
        self.assertTrue(all("Authorization" not in request.headers for request, _ in opener.calls))

    def test_default_branch_with_slash_is_escaped(self):
        opener = Opener(*file_responses(branch="release/current"))
        GitHubClient(opener=opener).read_verification_file(REPOSITORY)
        self.assertTrue(opener.calls[1][0].full_url.endswith("/git/ref/heads/release%2Fcurrent"))

    def test_rejects_symlink_submodule_and_directory(self):
        for mode, obj_type in [("120000", "blob"), ("160000", "commit"), ("040000", "tree")]:
            responses = file_responses()
            responses[3]["tree"][0].update(mode=mode, type=obj_type)
            opener = Opener(*responses)
            self.assert_error("not_regular_file", lambda: GitHubClient(opener=opener).read_verification_file(REPOSITORY))
            self.assertEqual(len(opener.calls), 4)

    def test_rejects_missing_file_and_unmerged_pull_request_refs(self):
        responses = file_responses()
        responses[3]["tree"] = []
        self.assert_error("file_not_found", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))
        responses = file_responses()
        responses[1]["ref"] = "refs/pull/1/head"
        self.assert_error("invalid_response", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))

    def test_file_limit_is_checked_before_download(self):
        responses = file_responses()
        responses[3]["tree"][0]["size"] = MAX_FILE_BYTES + 1
        opener = Opener(*responses)
        self.assert_error("file_too_large", lambda: GitHubClient(opener=opener).read_verification_file(REPOSITORY))
        self.assertEqual(len(opener.calls), 4)

    def test_rejects_truncated_or_mismatched_objects(self):
        changes = [
            (1, {"object": {"type": "tag", "sha": COMMIT}}),
            (2, {"sha": "c" * 40}),
            (3, {"truncated": True}),
            (3, {"sha": "c" * 40}),
            (4, {"encoding": "none"}),
            (4, {"sha": "c" * 40}),
            (4, {"size": 1000}),
        ]
        for index, change in changes:
            with self.subTest(index=index, change=change):
                responses = file_responses()
                responses[index].update(change)
                self.assert_error("invalid_response", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))

    def test_rejects_invalid_base64_utf8_and_blob_hash(self):
        responses = file_responses()
        responses[4]["content"] = "!!bad!!"
        self.assert_error("invalid_file", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))
        self.assert_error("invalid_file", lambda: GitHubClient(opener=Opener(*file_responses(b"\xff"))).read_verification_file(REPOSITORY))
        responses = file_responses()
        responses[4]["content"] = base64.b64encode(b"x" * responses[4]["size"]).decode()
        self.assert_error("invalid_response", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))

    def test_rejects_repository_transfer_before_or_during_file_check(self):
        for index in [0, 5]:
            responses = file_responses()
            responses[index]["owner"]["id"] = 43
            self.assert_error("repository_changed", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))
        responses = file_responses()
        responses[5]["id"] = 999
        self.assert_error("repository_changed", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))

    def test_rejects_default_branch_change_during_check(self):
        responses = file_responses()
        responses[5]["default_branch"] = "other"
        self.assert_error("repository_changed", lambda: GitHubClient(opener=Opener(*responses)).read_verification_file(REPOSITORY))


class PermissionTests(ErrorAssertionMixin, unittest.TestCase):
    def test_personal_owner_is_matched_by_immutable_id(self):
        opener = Opener({"id": 42, "login": "renamed-user", "type": "User"}, repository_data())
        result = GitHubClient(opener=opener).verify_user_access(REPOSITORY, "user-token")
        self.assertEqual((result.github_user_id, result.role), (42, "owner"))
        self.assertTrue(all(request.headers["Authorization"] == "Bearer user-token" for request, _ in opener.calls))
        self.assertEqual(opener.calls[0][0].full_url, API_ORIGIN + "/user")

    def test_matching_login_without_matching_id_is_rejected(self):
        opener = Opener({"id": 999, "login": "researcher", "type": "User"}, repository_data(permissions={"admin": True}))
        self.assert_error("insufficient_permission", lambda: GitHubClient(opener=opener).verify_user_access(REPOSITORY, "user-token"))

    def test_org_admin_and_maintain_roles_are_accepted(self):
        repo = Repository(123, 42, "Organization", "researcher", "proofs", "main")
        for role in ["admin", "maintain"]:
            data = repository_data(owner={"id": 42, "login": "researcher", "type": "Organization"}, permissions={role: True, "push": True})
            opener = Opener({"id": 999, "login": "collaborator", "type": "User"}, data)
            result = GitHubClient(opener=opener).verify_user_access(repo, "user-token")
            self.assertEqual(result.role, role)

    def test_write_membership_missing_and_truthy_permission_values_are_rejected(self):
        repo = Repository(123, 42, "Organization", "researcher", "proofs", "main")
        for permissions in [{"push": True}, {"pull": True}, {}, None, {"admin": "true"}, {"maintain": 1}]:
            data = repository_data(owner={"id": 42, "login": "researcher", "type": "Organization"}, permissions=permissions)
            opener = Opener({"id": 999, "login": "collaborator", "type": "User"}, data)
            self.assert_error("insufficient_permission", lambda: GitHubClient(opener=opener).verify_user_access(repo, "user-token"))

    def test_application_tokens_and_bot_identity_are_rejected(self):
        self.assert_error("authentication_required", lambda: GitHubClient(opener=Opener()).verify_user_access(REPOSITORY, ""))
        self.assert_error("user_account_required", lambda: GitHubClient(opener=Opener({"id": 42, "login": "bot", "type": "Bot"})).verify_user_access(REPOSITORY, "token"))

    def test_transfer_invalidates_connected_github_check(self):
        opener = Opener({"id": 42, "login": "researcher", "type": "User"}, repository_data(id=456))
        self.assert_error("repository_changed", lambda: GitHubClient(opener=opener).verify_user_access(REPOSITORY, "token"))


if __name__ == "__main__":
    unittest.main()
