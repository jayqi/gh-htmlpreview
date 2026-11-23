import argparse
import runpy
import sys

import pytest

from gh_htmlpreview import __version__
from gh_htmlpreview.cli import main, parse_duration

URL = "https://github.com/octocat/example/blob/main/index.html"


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


def test_not_implemented(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([URL, "--timeout", "90s", "--port", "8000", "--no-browser"])
    assert exc_info.value.code == 1
    assert "not implemented yet" in capsys.readouterr().err


def test_python_m(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["__main__.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("gh_htmlpreview", run_name="__main__")
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: python -m gh_htmlpreview ")
