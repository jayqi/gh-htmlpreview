import http.client
import io
import os
import subprocess

import pytest

from gh_htmlpreview import __version__
from gh_htmlpreview.exceptions import NotFound, UpstreamError
from gh_htmlpreview.github import Client, Token, resolve_token
from tests.fake_github import SHA, TOKEN, FakeGitHub, Transport, response

AUTH = Token(TOKEN, "GH_TOKEN")
COMMITS_URL = "https://api.github.com/repos/octo/repo/commits"


def headers(request) -> dict[str, str]:
    """A request's headers, except Host, which urllib adds."""
    return {k.lower(): v for k, v in request.header_items() if k.lower() != "host"}


# Token resolution


def fake_run(returncode=0, stdout=f"{TOKEN}\n", error: Exception | None = None):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if error is not None:
            raise error
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"GH_TOKEN": "a", "GITHUB_TOKEN": "b"}, Token("a", "GH_TOKEN")),
        ({"GITHUB_TOKEN": "b"}, Token("b", "GITHUB_TOKEN")),
        ({"GH_TOKEN": "", "GITHUB_TOKEN": " b "}, Token("b", "GITHUB_TOKEN")),
    ],
)
def test_resolve_token_from_env(env, expected):
    run = fake_run()
    assert resolve_token(env, run) == expected
    assert run.calls == []


def test_resolve_token_from_gh():
    run = fake_run()
    assert resolve_token({"GH_TOKEN": "  "}, run) == Token(TOKEN, "gh auth")
    assert run.calls == [["gh", "auth", "token", "--hostname", "github.com"]]


@pytest.mark.parametrize(
    "run",
    [
        fake_run(returncode=1, stdout=""),
        fake_run(stdout="\n"),
        fake_run(error=FileNotFoundError("gh")),
        fake_run(error=subprocess.TimeoutExpired("gh", 10)),
    ],
    ids=["gh fails", "gh prints nothing", "gh missing", "gh times out"],
)
def test_resolve_token_none(run):
    assert resolve_token({}, run) is None


def test_resolve_token_from_gh_on_path(tmp_path, monkeypatch):
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\n[ "$*" = "auth token --hostname github.com" ] && echo from-gh\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    assert resolve_token({}) == Token("from-gh", "gh auth")


# Ref resolution


@pytest.mark.parametrize("token", [AUTH, None])
def test_resolve(token):
    github = FakeGitHub()
    client = Client(token, github)
    assert client.resolve("octo", "repo", ["main", "docs", "index.html"]) == (
        SHA,
        "docs/index.html",
    )
    assert github.urls() == [f"{COMMITS_URL}/main"]
    (request,) = github.requests
    expected = {
        "accept": "application/vnd.github.sha",
        "user-agent": f"gh-htmlpreview/{__version__}",
        "x-github-api-version": "2022-11-28",
    }
    if token is not None:
        expected["authorization"] = f"Bearer {TOKEN}"
    assert headers(request) == expected
    assert all(r.closed for r in github.responses)


def test_resolve_branch_with_slashes():
    github = FakeGitHub(refs={"feature/new/ui": SHA})
    client = Client(AUTH, github)
    segments = ["feature", "new", "ui", "docs", "index.html"]
    assert client.resolve("octo", "repo", segments) == (SHA, "docs/index.html")
    assert github.urls() == [
        f"{COMMITS_URL}/feature",
        f"{COMMITS_URL}/feature/new",
        f"{COMMITS_URL}/feature/new/ui",
    ]


def test_resolve_shortest_match_first():
    github = FakeGitHub(refs={"v1": SHA, "v1/docs": "0" * 40})
    client = Client(AUTH, github)
    assert client.resolve("octo", "repo", ["v1", "docs", "a.html"]) == (SHA, "docs/a.html")


@pytest.mark.parametrize("kind", ["heads", "tags"])
def test_resolve_qualified_ref(kind):
    github = FakeGitHub(refs={"feature/x": SHA})
    client = Client(AUTH, github)
    segments = ["refs", kind, "feature", "x", "a.html"]
    assert client.resolve("octo", "repo", segments) == (SHA, "a.html")
    assert github.urls() == [
        f"{COMMITS_URL}/refs/{kind}/feature",
        f"{COMMITS_URL}/refs/{kind}/feature/x",
    ]


def test_resolve_full_sha():
    github = FakeGitHub()
    client = Client(AUTH, github)
    assert client.resolve("octo", "repo", [SHA, "a.html"]) == (SHA, "a.html")
    assert github.urls() == [f"{COMMITS_URL}/{SHA}"]


def test_resolve_branch_starting_with_sha_prefix():
    github = FakeGitHub(refs={"main": "2024" + "e" * 36, "2024/report": SHA})
    client = Client(AUTH, github)
    assert client.resolve("octo", "repo", ["2024", "report", "a.html"]) == (SHA, "a.html")


def test_resolve_short_sha():
    github = FakeGitHub()
    client = Client(AUTH, github)
    with pytest.raises(NotFound, match="No branch, tag or commit in octo/repo matches"):
        client.resolve("octo", "repo", [SHA[:7], "a.html"])


def test_resolve_quotes_segments():
    github = FakeGitHub(refs={"a b#c?": SHA})
    client = Client(AUTH, github)
    assert client.resolve("octo", "repo", ["a b#c?", "my file.html"]) == (SHA, "my file.html")
    assert github.urls() == [f"{COMMITS_URL}/a%20b%23c%3F"]


def test_resolve_no_matching_ref():
    github = FakeGitHub(refs={})
    client = Client(AUTH, github)
    with pytest.raises(NotFound, match="No branch, tag or commit in octo/repo matches"):
        client.resolve("octo", "repo", ["a", "b", "c.html"])
    assert len(github.requests) == 2


def test_resolve_repository_not_found_stops():
    github = FakeGitHub(private=True)
    client = Client(Token("token-without-access", "GITHUB_TOKEN"), github)
    with pytest.raises(NotFound) as exc_info:
        client.resolve("octo", "repo", ["feature", "x", "a.html"])
    assert str(exc_info.value) == (
        "Repository octo/repo not found, or the token from GITHUB_TOKEN can't access it."
    )
    assert len(github.requests) == 1


def test_resolve_repository_not_found_unauthenticated_hint():
    github = FakeGitHub(private=True)
    client = Client(None, github)
    with pytest.raises(NotFound) as exc_info:
        client.resolve("octo", "repo", ["main", "a.html"])
    assert str(exc_info.value) == (
        "Repository octo/repo not found. "
        "If it's a private repository, run 'gh auth login' or set GH_TOKEN."
    )


def test_resolve_private_repository_authenticated():
    github = FakeGitHub(private=True)
    client = Client(AUTH, github)
    assert client.resolve("octo", "repo", ["main", "a.html"]) == (SHA, "a.html")


SAML_MESSAGE = "Resource protected by organization SAML enforcement."


@pytest.mark.parametrize(
    ("code", "error_headers", "body", "token", "message"),
    [
        (500, {}, b"", AUTH, f"GitHub returned HTTP 500 for {COMMITS_URL}/main"),
        (
            403,
            {},
            f'{{"message": "{SAML_MESSAGE}"}}'.encode(),
            AUTH,
            f"GitHub returned HTTP 403 for {COMMITS_URL}/main: {SAML_MESSAGE}",
        ),
        (403, {}, b"<html>", AUTH, f"GitHub returned HTTP 403 for {COMMITS_URL}/main"),
        (
            401,
            {},
            b'{"message": "Bad credentials"}',
            AUTH,
            "GitHub rejected the token from GH_TOKEN: Bad credentials",
        ),
        (403, {"x-ratelimit-remaining": "0"}, b"", AUTH, "GitHub rate limit exceeded."),
        (429, {"retry-after": "60"}, b"", AUTH, "GitHub rate limit exceeded."),
        (
            403,
            {"x-ratelimit-remaining": "0"},
            b"",
            None,
            "GitHub rate limit exceeded. Run 'gh auth login' or set GH_TOKEN for a higher limit.",
        ),
    ],
    ids=["500", "403 json", "403 not json", "401", "rate limit", "retry after", "no token"],
)
def test_resolve_upstream_error(code, error_headers, body, token, message):
    transport = Transport(lambda r: response(r.full_url, code, body, error_headers))
    client = Client(token, transport)
    with pytest.raises(UpstreamError) as exc_info:
        client.resolve("octo", "repo", ["main", "docs", "a.html"])
    assert str(exc_info.value) == message
    assert len(transport.requests) == 1


@pytest.mark.parametrize("body", [b"", b"<html>", b"3F2C1A9D0E8B7C6A5F4E3D2C1B0A9F8E7D6C5B4A"])
def test_resolve_unexpected_response(body):
    github = FakeGitHub(refs={"main": body.decode()})
    client = Client(AUTH, github)
    with pytest.raises(UpstreamError, match="Unexpected response"):
        client.resolve("octo", "repo", ["main", "a.html"])


@pytest.mark.parametrize("error", [ConnectionResetError("reset"), http.client.BadStatusLine("x")])
def test_resolve_connection_error(error):
    github = FakeGitHub()
    github.error = error
    client = Client(AUTH, github)
    with pytest.raises(UpstreamError, match="Couldn't connect to api.github.com"):
        client.resolve("octo", "repo", ["main", "a.html"])


# Fetching


def test_fetch_authenticated():
    github = FakeGitHub(files={"docs/my report.html": b"<html>"})
    client = Client(AUTH, github)
    assert client.fetch("octo", "repo", SHA, "docs/my report.html") == b"<html>"
    assert github.urls() == [
        f"https://api.github.com/repos/octo/repo/contents/docs/my%20report.html?ref={SHA}"
    ]
    assert headers(github.requests[0]) == {
        "accept": "application/vnd.github.raw+json",
        "authorization": f"Bearer {TOKEN}",
        "user-agent": f"gh-htmlpreview/{__version__}",
        "x-github-api-version": "2022-11-28",
    }
    assert all(r.closed for r in github.responses)


def test_fetch_unauthenticated():
    github = FakeGitHub(files={"docs/my report.html": b"<html>"})
    client = Client(None, github)
    assert client.fetch("octo", "repo", SHA, "docs/my report.html") == b"<html>"
    assert github.urls() == [
        f"https://raw.githubusercontent.com/octo/repo/{SHA}/docs/my%20report.html"
    ]
    assert headers(github.requests[0]) == {
        "accept": "*/*",
        "user-agent": f"gh-htmlpreview/{__version__}",
    }


def test_fetch_json_file_authenticated():
    github = FakeGitHub(files={"data.json": b"[]"})
    client = Client(AUTH, github)
    assert client.fetch("octo", "repo", SHA, "data.json") == b"[]"


@pytest.mark.parametrize("token", [AUTH, None])
def test_fetch_not_found(token):
    github = FakeGitHub(files={"a.html": b""})
    client = Client(token, github)
    with pytest.raises(NotFound, match="^b.html not found in octo/repo at 3f2c1a9.$"):
        client.fetch("octo", "repo", SHA, "b.html")


def test_fetch_directory_authenticated():
    github = FakeGitHub(files={"docs/a.html": b""})
    client = Client(AUTH, github)
    with pytest.raises(NotFound, match="^docs is a directory in octo/repo, not a file.$"):
        client.fetch("octo", "repo", SHA, "docs")
    assert all(r.closed for r in github.responses)


def test_fetch_directory_unauthenticated():
    github = FakeGitHub(files={"docs/a.html": b""})
    client = Client(None, github)
    with pytest.raises(NotFound, match="^docs not found"):
        client.fetch("octo", "repo", SHA, "docs")


@pytest.mark.parametrize("path", ["", "/", "../x.html", "docs/./a.html", "a/.."])
def test_fetch_invalid_path(path):
    github = FakeGitHub()
    client = Client(AUTH, github)
    with pytest.raises(NotFound) as exc_info:
        client.fetch("octo", "repo", SHA, path)
    assert str(exc_info.value) == f"Invalid file path {path!r}."
    assert github.requests == []


@pytest.mark.parametrize("token", [AUTH, None])
def test_fetch_upstream_error(token):
    transport = Transport(lambda r: response(r.full_url, 502))
    client = Client(token, transport)
    with pytest.raises(UpstreamError, match="GitHub returned HTTP 502"):
        client.fetch("octo", "repo", SHA, "a.html")


@pytest.mark.parametrize(
    "error", [ConnectionResetError("reset"), http.client.IncompleteRead(b"partial", 10)]
)
def test_fetch_read_error(error):
    class BrokenBody(io.BytesIO):
        def read(self, *args):
            raise error

    transport = Transport(lambda r: response(r.full_url, body=BrokenBody()))
    client = Client(AUTH, transport)
    with pytest.raises(UpstreamError, match="Couldn't read response"):
        client.fetch("octo", "repo", SHA, "a.html")
    assert transport.responses[0].closed


# Redirects


@pytest.mark.parametrize(
    ("location", "keeps_auth"),
    [
        ("https://api.github.com/repositories/1/commits/main", True),
        ("https://example.com/commits/main", False),
        ("http://api.github.com/repositories/1/commits/main", False),
        ("https://api.github.com.evil.example/commits/main", False),
    ],
)
def test_redirect_authorization(location, keeps_auth):
    def respond(request):
        if request.full_url == location:
            return response(location, body=SHA.encode())
        return response(request.full_url, 301, headers={"Location": location})

    transport = Transport(respond)
    client = Client(AUTH, transport)
    assert client.resolve("octo", "repo", ["main", "a.html"]) == (SHA, "a.html")
    assert transport.urls() == [f"{COMMITS_URL}/main", location]
    expected = f"Bearer {TOKEN}" if keeps_auth else None
    assert transport.requests[1].get_header("Authorization") == expected


# Live GitHub


@pytest.mark.skipif(
    not os.environ.get("GH_HTMLPREVIEW_LIVE_TESTS"),
    reason="set GH_HTMLPREVIEW_LIVE_TESTS=1 to run tests against GitHub",
)
@pytest.mark.parametrize("authenticated", [True, False], ids=["authenticated", "unauthenticated"])
def test_live(authenticated):
    token = resolve_token() if authenticated else None
    if authenticated and token is None:
        pytest.skip("no GitHub token available")
    client = Client(token)
    sha, path = client.resolve("octocat", "Hello-World", ["master", "README"])
    assert sha == "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
    assert path == "README"
    assert client.fetch("octocat", "Hello-World", sha, path) == b"Hello World!\n"
    with pytest.raises(NotFound):
        client.fetch("octocat", "Hello-World", sha, "does-not-exist.html")
