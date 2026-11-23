from email.message import Message
import http.client
import io
import os
import subprocess
import urllib.error
from urllib.parse import unquote, urlsplit
import urllib.request

import pytest

from gh_htmlpreview import __version__
from gh_htmlpreview.exceptions import NotFound, UpstreamError
from gh_htmlpreview.github import Client, _SameHostAuthRedirectHandler, resolve_token

SHA = "3f2c1a9d0e8b7c6a5f4e3d2c1b0a9f8e7d6c5b4a"
TOKEN = "ghp_secret"


class FakeResponse:
    def __init__(self, body: bytes, content_type: str):
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self._body = io.BytesIO(body)
        self.closed = False

    def read(self) -> bytes:
        return self._body.read()

    def close(self) -> None:
        self.closed = True


def http_error(url: str, code: int, headers: dict[str, str] | None = None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(url, code, "error", message, io.BytesIO(b""))


class FakeGitHub:
    """Serves the commits API, the contents API and raw.githubusercontent.com for one repo."""

    def __init__(self, refs=None, files=None, private=False):
        self.refs = refs if refs is not None else {"main": SHA}
        self.files = files if files is not None else {}
        self.private = private
        self.requests: list[urllib.request.Request] = []
        self.responses: list[FakeResponse] = []
        self.error: Exception | None = None

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        url = request.full_url
        parts = urlsplit(url)
        authorized = request.get_header("Authorization") == f"Bearer {TOKEN}"
        if parts.netloc == "api.github.com":
            prefix = "/repos/octo/repo/"
            if not parts.path.startswith(prefix) or (self.private and not authorized):
                raise http_error(url, 404)
            kind, _, rest = parts.path.removeprefix(prefix).partition("/")
            if kind == "commits":
                ref = unquote(rest)
                if ref.startswith(("refs/heads/", "refs/tags/")):
                    ref = ref.split("/", 2)[2]
                if ref not in self.refs:
                    raise http_error(url, 422)
                return self._respond(self.refs[ref].encode(), "application/vnd.github.sha")
            if kind == "contents":
                assert parts.query == f"ref={SHA}"
                return self._file(url, unquote(rest), "application/vnd.github.raw+json")
        if parts.netloc == "raw.githubusercontent.com":
            prefix = f"/octo/repo/{SHA}/"
            if not parts.path.startswith(prefix) or self.private:
                raise http_error(url, 404)
            return self._file(url, unquote(parts.path.removeprefix(prefix)), "text/plain")
        raise AssertionError(f"Unexpected URL {url}")

    def _file(self, url: str, path: str, content_type: str) -> FakeResponse:
        if path in self.files:
            return self._respond(self.files[path], content_type)
        if any(p.startswith(f"{path}/") for p in self.files):
            if urlsplit(url).netloc == "raw.githubusercontent.com":
                raise http_error(url, 404)
            return self._respond(b"[]", "application/json")
        raise http_error(url, 404)

    def _respond(self, body: bytes, content_type: str) -> FakeResponse:
        response = FakeResponse(body, content_type)
        self.responses.append(response)
        return response

    def urls(self) -> list[str]:
        return [r.full_url for r in self.requests]


def headers(request: urllib.request.Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.header_items()}


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
        ({"GH_TOKEN": "a", "GITHUB_TOKEN": "b"}, "a"),
        ({"GITHUB_TOKEN": "b"}, "b"),
        ({"GH_TOKEN": "", "GITHUB_TOKEN": " b "}, "b"),
    ],
)
def test_resolve_token_from_env(env, expected):
    run = fake_run()
    assert resolve_token(env, run) == expected
    assert run.calls == []


def test_resolve_token_from_gh():
    run = fake_run()
    assert resolve_token({"GH_TOKEN": "  "}, run) == TOKEN
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
    assert resolve_token({}) == "from-gh"


# Ref resolution


@pytest.mark.parametrize("token", [TOKEN, None])
def test_resolve(token):
    github = FakeGitHub()
    client = Client(token, urlopen=github)
    assert client.resolve("octo", "repo", ["main", "docs", "index.html"]) == (
        SHA,
        "docs/index.html",
    )
    assert github.urls() == ["https://api.github.com/repos/octo/repo/commits/main"]
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
    client = Client(TOKEN, urlopen=github)
    segments = ["feature", "new", "ui", "docs", "index.html"]
    assert client.resolve("octo", "repo", segments) == (SHA, "docs/index.html")
    assert github.urls() == [
        "https://api.github.com/repos/octo/repo/commits/feature",
        "https://api.github.com/repos/octo/repo/commits/feature/new",
        "https://api.github.com/repos/octo/repo/commits/feature/new/ui",
    ]


def test_resolve_shortest_match_first():
    github = FakeGitHub(refs={"v1": SHA, "v1/docs": "0" * 40})
    client = Client(TOKEN, urlopen=github)
    assert client.resolve("octo", "repo", ["v1", "docs", "a.html"]) == (SHA, "docs/a.html")


@pytest.mark.parametrize("kind", ["heads", "tags"])
def test_resolve_qualified_ref(kind):
    github = FakeGitHub(refs={"feature/x": SHA})
    client = Client(TOKEN, urlopen=github)
    segments = ["refs", kind, "feature", "x", "a.html"]
    assert client.resolve("octo", "repo", segments) == (SHA, "a.html")
    assert github.urls() == [
        f"https://api.github.com/repos/octo/repo/commits/refs/{kind}/feature",
        f"https://api.github.com/repos/octo/repo/commits/refs/{kind}/feature/x",
    ]


def test_resolve_quotes_segments():
    github = FakeGitHub(refs={"a b#c?": SHA})
    client = Client(TOKEN, urlopen=github)
    assert client.resolve("octo", "repo", ["a b#c?", "my file.html"]) == (SHA, "my file.html")
    assert github.urls() == ["https://api.github.com/repos/octo/repo/commits/a%20b%23c%3F"]


def test_resolve_no_matching_ref():
    github = FakeGitHub(refs={})
    client = Client(TOKEN, urlopen=github)
    with pytest.raises(NotFound, match="No branch, tag or commit in octo/repo matches"):
        client.resolve("octo", "repo", ["a", "b", "c.html"])
    assert len(github.requests) == 2


def test_resolve_repository_not_found_stops():
    github = FakeGitHub(private=True)
    client = Client("token-without-access", urlopen=github)
    with pytest.raises(NotFound) as exc_info:
        client.resolve("octo", "repo", ["feature", "x", "a.html"])
    assert str(exc_info.value) == "Repository octo/repo not found."
    assert len(github.requests) == 1


def test_resolve_repository_not_found_unauthenticated_hint():
    github = FakeGitHub(private=True)
    client = Client(None, urlopen=github)
    with pytest.raises(NotFound) as exc_info:
        client.resolve("octo", "repo", ["main", "a.html"])
    assert str(exc_info.value) == (
        "Repository octo/repo not found. "
        "If it's a private repository, run 'gh auth login' or set GH_TOKEN."
    )


def test_resolve_private_repository_authenticated():
    github = FakeGitHub(private=True)
    client = Client(TOKEN, urlopen=github)
    assert client.resolve("octo", "repo", ["main", "a.html"]) == (SHA, "a.html")


@pytest.mark.parametrize(
    ("code", "error_headers", "token", "message"),
    [
        (500, {}, TOKEN, "GitHub returned HTTP 500 for https://api.github.com/"),
        (403, {}, TOKEN, "GitHub returned HTTP 403 for https://api.github.com/"),
        (403, {"x-ratelimit-remaining": "0"}, TOKEN, "^GitHub rate limit exceeded.$"),
        (429, {"retry-after": "60"}, TOKEN, "^GitHub rate limit exceeded.$"),
        (403, {"x-ratelimit-remaining": "0"}, None, "rate limit exceeded. Run 'gh auth login'"),
    ],
)
def test_resolve_upstream_error(code, error_headers, token, message):
    github = FakeGitHub()
    github.error = http_error("https://api.github.com/x", code, error_headers)
    client = Client(token, urlopen=github)
    with pytest.raises(UpstreamError, match=message):
        client.resolve("octo", "repo", ["main", "a.html"])
    assert len(github.requests) == 1


@pytest.mark.parametrize("body", [b"", b"<html>", b"3F2C1A9D0E8B7C6A5F4E3D2C1B0A9F8E7D6C5B4A"])
def test_resolve_unexpected_response(body):
    github = FakeGitHub(refs={"main": body.decode()})
    client = Client(TOKEN, urlopen=github)
    with pytest.raises(UpstreamError, match="Unexpected response"):
        client.resolve("octo", "repo", ["main", "a.html"])


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("no route"),
        TimeoutError("timed out"),
        ConnectionResetError(),
        http.client.BadStatusLine("garbage"),
        http.client.LineTooLong("header line"),
    ],
)
def test_resolve_connection_error(error):
    github = FakeGitHub()
    github.error = error
    client = Client(TOKEN, urlopen=github)
    with pytest.raises(UpstreamError, match="Couldn't connect to api.github.com"):
        client.resolve("octo", "repo", ["main", "a.html"])


# Fetching


def test_fetch_authenticated():
    github = FakeGitHub(files={"docs/my report.html": b"<html>"})
    client = Client(TOKEN, urlopen=github)
    assert client.fetch("octo", "repo", SHA, "docs/my report.html") == b"<html>"
    (request,) = github.requests
    assert request.full_url == (
        f"https://api.github.com/repos/octo/repo/contents/docs/my%20report.html?ref={SHA}"
    )
    assert headers(request) == {
        "accept": "application/vnd.github.raw+json",
        "authorization": f"Bearer {TOKEN}",
        "user-agent": f"gh-htmlpreview/{__version__}",
        "x-github-api-version": "2022-11-28",
    }
    assert all(r.closed for r in github.responses)


def test_fetch_unauthenticated():
    github = FakeGitHub(files={"docs/my report.html": b"<html>"})
    client = Client(None, urlopen=github)
    assert client.fetch("octo", "repo", SHA, "docs/my report.html") == b"<html>"
    (request,) = github.requests
    assert request.full_url == (
        f"https://raw.githubusercontent.com/octo/repo/{SHA}/docs/my%20report.html"
    )
    assert headers(request) == {"accept": "*/*", "user-agent": f"gh-htmlpreview/{__version__}"}


def test_fetch_json_file_authenticated():
    github = FakeGitHub(files={"data.json": b"[]"})
    client = Client(TOKEN, urlopen=github)
    assert client.fetch("octo", "repo", SHA, "data.json") == b"[]"


@pytest.mark.parametrize("token", [TOKEN, None])
def test_fetch_not_found(token):
    github = FakeGitHub(files={"a.html": b""})
    client = Client(token, urlopen=github)
    with pytest.raises(NotFound, match="^b.html not found in octo/repo at 3f2c1a9.$"):
        client.fetch("octo", "repo", SHA, "b.html")


def test_fetch_directory_authenticated():
    github = FakeGitHub(files={"docs/a.html": b""})
    client = Client(TOKEN, urlopen=github)
    with pytest.raises(NotFound, match="^docs is a directory in octo/repo, not a file.$"):
        client.fetch("octo", "repo", SHA, "docs")
    assert all(r.closed for r in github.responses)


def test_fetch_directory_unauthenticated():
    github = FakeGitHub(files={"docs/a.html": b""})
    client = Client(None, urlopen=github)
    with pytest.raises(NotFound, match="^docs not found"):
        client.fetch("octo", "repo", SHA, "docs")


@pytest.mark.parametrize("path", ["", "/", "//"])
def test_fetch_empty_path(path):
    github = FakeGitHub()
    client = Client(TOKEN, urlopen=github)
    with pytest.raises(NotFound):
        client.fetch("octo", "repo", SHA, path)
    assert github.requests == []


@pytest.mark.parametrize("token", [TOKEN, None])
def test_fetch_upstream_error(token):
    github = FakeGitHub()
    github.error = http_error("https://example/x", 502)
    client = Client(token, urlopen=github)
    with pytest.raises(UpstreamError, match="GitHub returned HTTP 502"):
        client.fetch("octo", "repo", SHA, "a.html")


@pytest.mark.parametrize(
    "error", [ConnectionResetError("reset"), http.client.IncompleteRead(b"partial", 10)]
)
def test_fetch_read_error(error):
    class BrokenResponse(FakeResponse):
        def read(self) -> bytes:
            raise error

    response = BrokenResponse(b"", "text/html")

    def urlopen(request, timeout):
        return response

    client = Client(TOKEN, urlopen=urlopen)
    with pytest.raises(UpstreamError, match="Couldn't read response"):
        client.fetch("octo", "repo", SHA, "a.html")
    assert response.closed


def test_token_only_sent_to_api():
    github = FakeGitHub(files={"a.html": b"<html>"})
    Client(None, urlopen=github).fetch("octo", "repo", SHA, "a.html")
    client = Client(TOKEN, urlopen=github)
    client.resolve("octo", "repo", ["main", "a.html"])
    client.fetch("octo", "repo", SHA, "a.html")
    for request in github.requests:
        if request.get_header("Authorization") is not None:
            assert urlsplit(request.full_url).netloc == "api.github.com"
    assert sum(r.get_header("Authorization") is not None for r in github.requests) == 2


# Redirects


@pytest.mark.parametrize(
    ("new_url", "keeps_auth"),
    [
        ("https://api.github.com/repositories/1/contents/a.html", True),
        ("https://raw.githubusercontent.com/octo/repo/main/a.html", False),
        ("http://api.github.com/repositories/1/contents/a.html", False),
        ("https://api.github.com.evil.example/a.html", False),
    ],
)
def test_redirect_authorization(new_url, keeps_auth):
    request = urllib.request.Request("https://api.github.com/repos/octo/repo/contents/a.html")
    request.add_unredirected_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("Accept", "application/vnd.github.raw+json")
    new = _SameHostAuthRedirectHandler().redirect_request(
        request, None, 301, "Moved", Message(), new_url
    )
    assert new is not None
    assert new.full_url == new_url
    assert new.get_header("Accept") == "application/vnd.github.raw+json"
    assert new.get_header("Authorization") == (f"Bearer {TOKEN}" if keeps_auth else None)


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
