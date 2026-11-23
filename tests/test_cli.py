import argparse
from collections import Counter
import re
import runpy
import shlex
import socket
import sys
import threading
import urllib.request

import pytest

from gh_htmlpreview import __version__, cli, github
from gh_htmlpreview.cli import main, parse_duration
from gh_htmlpreview.exceptions import NotFound, UpstreamError
from gh_htmlpreview.server import PreviewServer

URL = "https://github.com/octocat/example/blob/main/index.html"
SHA = "3f2c1a9d0e8b7c6a5f4e3d2c1b0a9f8e7d6c5b4a"
FILES = {
    "docs/report.html": b"<html>report</html>",
    "docs/style.css": b"body {}",
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("90s", 90),
        ("10m", 600),
        ("1h", 3600),
        ("600", 600),
        ("1.5m", 90),
        ("0.5", 0.5),
        ("10M", 600),
        (" 10m ", 600),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["", "m", "10d", "10 m", "1h30m", "-5", "1e3", "ten"])
def test_parse_duration_invalid(value):
    with pytest.raises(argparse.ArgumentTypeError, match="Unable to parse"):
        parse_duration(value)


@pytest.mark.parametrize("value", ["0", "0s", "0.0m"])
def test_parse_duration_not_positive(value):
    with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
        parse_duration(value)


def test_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_help_uses_prog(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"], prog="gh htmlpreview")
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: gh htmlpreview ")


def test_help_default_prog(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: gh-htmlpreview ")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [URL, "--timeout", "0"],
        [URL, "--timeout", "soon"],
        [URL, "--port", "65536"],
        [URL, "--port", "-1"],
        [URL, "--port", "http"],
        [URL, "--time", "10m"],
    ],
)
def test_invalid_arguments_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    assert exc_info.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_unsupported_url(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["https://github.com/octo/repo/tree/main/docs"], prog="gh htmlpreview")
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: gh htmlpreview ")
    assert "error: argument URL: Unsupported URL: " in err
    assert "Accepted forms:\n  https://github.com/{owner}/{repo}/blob/{ref}/{path}\n" in err


def test_python_m(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["__main__.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("gh_htmlpreview", run_name="__main__")
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: python -m gh_htmlpreview ")


# Running the preview


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, token, resolve_error=None, fetch_error=None):
        self.token = token
        self.resolve_error = resolve_error
        self.fetch_error = fetch_error
        self.fetches: Counter[str] = Counter()
        FakeClient.instances.append(self)

    def resolve(self, owner, repo, segments):
        assert (owner, repo) == ("octo", "repo")
        if self.resolve_error is not None:
            raise self.resolve_error
        assert segments[0] == "main"
        return SHA, "/".join(segments[1:])

    def fetch(self, owner, repo, sha, path):
        assert (owner, repo, sha) == ("octo", "repo", SHA)
        self.fetches[path] += 1
        if self.fetch_error is not None:
            raise self.fetch_error
        if path not in FILES:
            raise NotFound(f"{path} not found in octo/repo at {SHA[:7]}.")
        return FILES[path]


@pytest.fixture
def fake_github(monkeypatch):
    """Replace GitHub access with FakeClient. Returns a dict of FakeClient options."""
    options = {}
    FakeClient.instances = []
    monkeypatch.setattr(github, "resolve_token", lambda: "token")
    monkeypatch.setattr(github, "Client", lambda token: FakeClient(token, **options))
    return options


@pytest.fixture
def browser(monkeypatch):
    """Replace opening the browser with requests for some paths. Returns the responses."""
    visits = {"paths": [""], "opened": [], "responses": {}}

    def open_browser(url):
        visits["opened"].append(url)

        def visit():
            for path in visits["paths"]:
                with urllib.request.urlopen(url + path, timeout=5) as response:
                    visits["responses"][path] = response.read()

        threading.Thread(target=visit, daemon=True).start()

    monkeypatch.setattr(cli, "open_browser", open_browser)
    return visits


TARGET_URL = "https://github.com/octo/repo/blob/main/docs/report.html"


def test_preview(fake_github, browser, capsys):
    browser["paths"] = ["", "/../style.css"]
    main([TARGET_URL, "-t", "1"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    match = re.fullmatch(
        r"Serving octo/repo@3f2c1a9:docs/report.html at "
        r"(http://127\.0\.0\.1:\d+/docs/report\.html)",
        lines[0],
    )
    assert match is not None
    assert lines[1:] == [
        "Shutting down after 0:00:01 without requests. Press Ctrl-C to stop.",
        "No requests for 0:00:01. Stopped.",
    ]
    assert browser["opened"] == [match.group(1)]
    assert browser["responses"] == {
        "": FILES["docs/report.html"],
        "/../style.css": FILES["docs/style.css"],
    }
    (client,) = FakeClient.instances
    assert client.token == "token"
    assert client.fetches == Counter({"docs/report.html": 1, "docs/style.css": 1})


def test_no_browser(fake_github, browser, capsys):
    main([TARGET_URL, "-t", "0.2", "--no-browser"])
    assert browser["opened"] == []
    assert capsys.readouterr().out.startswith("Serving octo/repo@3f2c1a9:docs/report.html at ")


def test_fixed_port(fake_github, browser, capsys):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    main([TARGET_URL, "-t", "0.2", "-p", str(port)])
    assert browser["opened"] == [f"http://127.0.0.1:{port}/docs/report.html"]


def test_port_in_use(fake_github, browser, capsys):
    with PreviewServer(lambda path: b"", "a.html") as other:
        with pytest.raises(SystemExit) as exc_info:
            main([TARGET_URL, "-p", str(other.port)])
    assert exc_info.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"gh-htmlpreview: error: Couldn't bind to 127.0.0.1:{other.port}: ")
    assert browser["opened"] == []


@pytest.mark.parametrize(
    ("option", "error"),
    [
        ("resolve_error", NotFound("Repository octo/repo not found. Run gh auth login.")),
        ("resolve_error", UpstreamError("GitHub rate limit exceeded.")),
        ("fetch_error", NotFound("docs/report.html not found in octo/repo at 3f2c1a9.")),
        ("fetch_error", UpstreamError("GitHub returned HTTP 500 for https://x")),
    ],
)
def test_github_errors(fake_github, browser, capsys, option, error):
    fake_github[option] = error
    with pytest.raises(SystemExit) as exc_info:
        main([TARGET_URL], prog="gh htmlpreview")
    assert exc_info.value.code == 1
    assert capsys.readouterr() == ("", f"gh htmlpreview: error: {error}\n")
    assert browser["opened"] == []


def test_interrupted_while_resolving(fake_github, browser, capsys):
    fake_github["resolve_error"] = KeyboardInterrupt()
    with pytest.raises(SystemExit) as exc_info:
        main([TARGET_URL])
    assert exc_info.value.code == 130
    assert browser["opened"] == []


def test_interrupted_while_serving(fake_github, browser, capsys, monkeypatch):
    def interrupt(self, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(PreviewServer, "serve_until_idle", interrupt)
    main([TARGET_URL, "--no-browser"])
    assert capsys.readouterr().out.splitlines()[-1] == " Stopped."


# Opening the browser


@pytest.fixture
def desktop(monkeypatch):
    """Clear the environment variables that skip opening the browser."""
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")


def test_browser_launcher(fake_github, desktop, capsys, monkeypatch):
    visit = "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=5).read()"
    monkeypatch.setenv("GH_BROWSER", shlex.join([sys.executable, "-I", "-c", visit]))
    main([TARGET_URL, "-t", "1"])
    out, err = capsys.readouterr()
    assert out.splitlines()[-1] == "No requests for 0:00:01. Stopped."
    assert '"GET /docs/report.html HTTP/1.1" 200 -' in err


def test_browser_launch_failure(fake_github, desktop, capsys, monkeypatch):
    monkeypatch.setenv("GH_BROWSER", "/nonexistent/browser")
    main([TARGET_URL, "-t", "0.2"])
    out, err = capsys.readouterr()
    assert out.splitlines()[-1] == "No requests for 0:00:00.200000. Stopped."
    assert err.startswith("Couldn't run browser command '/nonexistent/browser': ")


def test_browser_skipped(fake_github, capsys, monkeypatch):
    monkeypatch.setenv("SSH_TTY", "/dev/pts/0")
    monkeypatch.setenv("GH_BROWSER", "/nonexistent/browser")
    main([TARGET_URL, "-t", "0.2"])
    out, err = capsys.readouterr()
    assert out.splitlines()[2:] == [
        "Not opening a browser in an SSH session.",
        "No requests for 0:00:00.200000. Stopped.",
    ]
    assert err == ""
