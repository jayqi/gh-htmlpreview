import argparse
from functools import partial
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
from gh_htmlpreview.server import PreviewServer
from tests.fake_github import SHA, TOKEN, FakeGitHub

URL = "https://github.com/octocat/example/blob/main/index.html"
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
        ("1h30m", 5400),
        ("2m30s", 150),
        ("1h0.5m", 3630),
        (" 10m ", 600),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize(
    "value", ["", "m", "10d", "10 m", "10M", "1H", "1h 30m", "1h30", "-5", "1e3", "ten"]
)
def test_parse_duration_invalid(value):
    with pytest.raises(argparse.ArgumentTypeError, match="Unable to parse"):
        parse_duration(value)


@pytest.mark.parametrize("value", ["0", "0s", "0.0m"])
def test_parse_duration_not_positive(value):
    with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
        parse_duration(value)


@pytest.mark.parametrize("value", ["86400000000000", "24000000000h", "100000000000000", "9" * 400])
def test_parse_duration_too_long(value):
    with pytest.raises(argparse.ArgumentTypeError, match="Duration is too long"):
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


@pytest.fixture
def fake_github(monkeypatch):
    """Serve GitHub requests from a FakeGitHub with FILES, authenticated by GH_TOKEN."""
    fake = FakeGitHub(files=FILES)
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setattr(github, "Client", partial(github.Client, transport=fake))
    return fake


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
    assert fake_github.urls() == [
        "https://api.github.com/repos/octo/repo/commits/main",
        f"https://api.github.com/repos/octo/repo/contents/docs/report.html?ref={SHA}",
        f"https://api.github.com/repos/octo/repo/contents/docs/style.css?ref={SHA}",
    ]


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
    with PreviewServer(lambda path: b"", "a.html", "octo/repo@3f2c1a9") as other:
        with pytest.raises(SystemExit) as exc_info:
            main([TARGET_URL, "-p", str(other.port)])
    assert exc_info.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"gh-htmlpreview: error: Couldn't bind to 127.0.0.1:{other.port}: ")
    assert browser["opened"] == []


@pytest.mark.parametrize(
    ("url", "error", "message"),
    [
        (
            "https://github.com/octo/missing/blob/main/a.html",
            None,
            "Repository octo/missing not found, or the token from GH_TOKEN can't access it.",
        ),
        (
            "https://github.com/octo/repo/blob/main/docs/missing.html",
            None,
            "docs/missing.html not found in octo/repo at 3f2c1a9.",
        ),
        (TARGET_URL, ConnectionResetError("reset"), "Couldn't connect to api.github.com: reset"),
    ],
    ids=["missing repository", "missing file", "connection error"],
)
def test_github_errors(fake_github, browser, capsys, url, error, message):
    fake_github.error = error
    with pytest.raises(SystemExit) as exc_info:
        main([url], prog="gh htmlpreview")
    assert exc_info.value.code == 1
    assert capsys.readouterr() == ("", f"gh htmlpreview: error: {message}\n")
    assert browser["opened"] == []


def test_interrupted_while_resolving(fake_github, browser, capsys):
    fake_github.error = KeyboardInterrupt()
    with pytest.raises(SystemExit) as exc_info:
        main([TARGET_URL])
    assert exc_info.value.code == 130
    assert browser["opened"] == []


def test_interrupted_while_opening_browser(fake_github, capsys, monkeypatch):
    def interrupt(url):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "open_browser", interrupt)
    main([TARGET_URL])
    assert capsys.readouterr().out.splitlines()[-1] == " Stopped."


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


@pytest.mark.parametrize("ssh", [False, True], ids=["desktop", "ssh"])
def test_browser_launch_failure(fake_github, desktop, capsys, monkeypatch, ssh):
    """A configured browser command runs even in an SSH session."""
    if ssh:
        monkeypatch.setenv("SSH_TTY", "/dev/pts/0")
    monkeypatch.setenv("GH_BROWSER", "/nonexistent/browser")
    main([TARGET_URL, "-t", "0.2"])
    out, err = capsys.readouterr()
    assert out.splitlines()[-1] == "No requests for 0:00:00.200000. Stopped."
    assert err.startswith("Couldn't run browser command '/nonexistent/browser': ")


def test_default_browser_skipped(fake_github, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("SSH_TTY", "/dev/pts/0")
    monkeypatch.delenv("GH_BROWSER", raising=False)
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path))  # No browser in gh's config
    main([TARGET_URL, "-t", "0.2"])
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[2].startswith("Not opening a browser in an SSH session. ")
    assert lines[3:] == ["No requests for 0:00:00.200000. Stopped."]
    assert err == ""
