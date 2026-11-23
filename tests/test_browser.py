import subprocess
import sys
import time
import webbrowser

import pytest

from gh_htmlpreview.browser import LaunchError, open_url, resolve_launcher, skip_reason

URL = "http://127.0.0.1:8000/index.html"


@pytest.mark.parametrize(
    ("env", "platform", "expected"),
    [
        ({}, "darwin", None),
        ({}, "win32", None),
        ({"DISPLAY": ":0"}, "linux", None),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", None),
        ({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, "darwin", "in an SSH session"),
        ({"SSH_TTY": "/dev/pts/0", "DISPLAY": ":0"}, "linux", "in an SSH session"),
        ({}, "linux", "neither DISPLAY nor WAYLAND_DISPLAY is set"),
        ({"DISPLAY": "", "WAYLAND_DISPLAY": ""}, "linux", "neither DISPLAY nor WAYLAND_DISPLAY"),
    ],
)
def test_skip_reason(env, platform, expected):
    reason = skip_reason(env, platform)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None
        assert reason.startswith("Not opening a browser ")
        assert expected in reason


def fake_run(stdout="", returncode=0, error=None):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if error is not None:
            raise error
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


GH_CONFIG = ["gh", "config", "get", "browser"]


@pytest.mark.parametrize(
    ("env", "gh_browser", "expected", "gh_called"),
    [
        ({"GH_BROWSER": "a", "BROWSER": "c"}, "b", "a", False),
        ({"GH_BROWSER": " ", "BROWSER": "c"}, "b\n", "b", True),
        ({"BROWSER": "c"}, "\n", "c", True),
        ({"BROWSER": " c --new "}, "", "c --new", True),
        ({"BROWSER": ""}, "", None, True),
        ({}, "", None, True),
    ],
)
def test_resolve_launcher(env, gh_browser, expected, gh_called):
    run = fake_run(stdout=gh_browser)
    assert resolve_launcher(env, run) == expected
    assert run.calls == ([GH_CONFIG] if gh_called else [])


@pytest.mark.parametrize(
    "run",
    [
        fake_run(returncode=1, stdout="b"),
        fake_run(error=FileNotFoundError("gh")),
        fake_run(error=subprocess.TimeoutExpired("gh", 10)),
    ],
    ids=["gh fails", "gh missing", "gh times out"],
)
def test_resolve_launcher_gh_unavailable(run):
    assert resolve_launcher({"BROWSER": "c"}, run) == "c"


class FakePopen:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self

    def wait(self):
        return 0


class FakeDefault:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.parametrize(
    ("launcher", "expected_args"),
    [
        ("firefox", ["firefox", URL]),
        ("open -a 'Google Chrome'", ["open", "-a", "Google Chrome", URL]),
        (
            '"/path with spaces/browser" --new-window',
            ["/path with spaces/browser", "--new-window", URL],
        ),
    ],
)
def test_open_url_launcher(launcher, expected_args):
    popen, default = FakePopen(), FakeDefault()
    open_url(URL, {"GH_BROWSER": launcher}, fake_run(), popen, default)
    assert popen.calls == [
        (expected_args, {"stdin": subprocess.DEVNULL, "start_new_session": True})
    ]
    assert default.calls == []


def test_open_url_default():
    popen, default = FakePopen(), FakeDefault()
    open_url(URL, {}, fake_run(), popen, default)
    assert popen.calls == []
    assert default.calls == [URL]


@pytest.mark.parametrize(
    ("env", "popen", "default", "message"),
    [
        (
            {"GH_BROWSER": "firefox 'unclosed"},
            FakePopen(),
            FakeDefault(),
            "Couldn't parse browser command \"firefox 'unclosed\": No closing quotation.",
        ),
        (
            {"BROWSER": "nobrowser --new"},
            FakePopen(FileNotFoundError(2, "No such file or directory")),
            FakeDefault(),
            "Couldn't run browser command 'nobrowser --new': No such file or directory.",
        ),
        ({}, FakePopen(), FakeDefault(result=False), "No default browser found."),
        (
            {},
            FakePopen(),
            FakeDefault(error=webbrowser.Error("could not locate runnable browser")),
            "Couldn't open the default browser: could not locate runnable browser.",
        ),
    ],
    ids=["unparseable", "not found", "no default", "default error"],
)
def test_open_url_errors(env, popen, default, message):
    with pytest.raises(LaunchError) as exc_info:
        open_url(URL, env, fake_run(), popen, default)
    assert str(exc_info.value) == message


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as the launcher")
def test_open_url_runs_launcher(tmp_path):
    """A real launcher gets the URL as its last argument and runs without being waited on."""
    out = tmp_path / "args"
    script = tmp_path / "browser.sh"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{out}.tmp" && mv "{out}.tmp" "{out}"\n')
    script.chmod(0o755)
    open_url(URL, {"GH_BROWSER": f"{script} --flag"}, fake_run())
    for _ in range(500):
        if out.exists():
            break
        time.sleep(0.01)
    assert out.read_text() == f"--flag\n{URL}\n"
