import argparse
from collections.abc import Sequence
from datetime import timedelta
from functools import partial
import re
import sys
from typing import NoReturn

from gh_htmlpreview import __version__, browser, github, urls
from gh_htmlpreview.exceptions import NotFound, UpstreamError
from gh_htmlpreview.server import HOST, PreviewServer

_NUMBER = r"\d+(?:\.\d+)?"
_DURATION_PART = re.compile(rf"({_NUMBER})([smh])")
_DURATION_PATTERN = re.compile(rf"(?:{_NUMBER}[smh])+|{_NUMBER}")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(value: str) -> float:
    """Parse a duration like '90s', '10m', '1h30m' or '600' into seconds."""
    text = value.strip()
    if _DURATION_PATTERN.fullmatch(text) is None:
        raise argparse.ArgumentTypeError(
            f"Unable to parse {value!r} as a duration. Use numbers with units s, m or h, such "
            "as '90s', '10m' or '1h30m', or a number of seconds without a unit."
        )
    parts = _DURATION_PART.findall(text)
    if parts:
        seconds = sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)
    else:
        seconds = float(text)
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"Duration must be positive, got {value!r}.")
    try:
        timedelta(seconds=seconds)
    except OverflowError:
        raise argparse.ArgumentTypeError(f"Duration is too long, got {value!r}.") from None
    return seconds


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Port must be an integer, got {value!r}.") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"Port must be between 0 and 65535, got {port}.")
    return port


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        allow_abbrev=False,
        description="Preview an HTML file from a GitHub repository in your browser.",
    )
    parser.add_argument("url", metavar="URL", help="GitHub URL of the file to preview.")
    parser.add_argument(
        "-t",
        "--timeout",
        default="30m",
        type=parse_duration,
        metavar="DURATION",
        help=(
            "Time to keep the server alive after the most recent request, such as '90s', '10m' "
            "or '1h30m'. A number without a unit is seconds. (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "-p",
        "--port",
        default=0,
        type=_port,
        help="Port to use. (default: 0, chosen by the operating system)",
    )
    parser.add_argument(
        "-n",
        "--no-browser",
        action="store_true",
        help="Print the URL without opening a browser.",
    )
    parser.add_argument(
        "--version", action="version", version=__version__, help="Show version and exit."
    )
    return parser


def open_browser(url: str) -> None:
    """Open the URL with the configured browser command, or the default browser unless
    there's a reason not to. Failures are reported only.
    """
    launcher = browser.resolve_launcher()
    if launcher is None and (reason := browser.skip_reason(url)) is not None:
        print(reason, flush=True)
        return
    try:
        browser.open_url(url, launcher)
    except browser.LaunchError as e:
        print(e, file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None, prog: str = "gh-htmlpreview") -> None:
    """Run the gh-htmlpreview command-line program."""
    parser = _parser(prog)
    args = parser.parse_args(argv)
    try:
        file_url = urls.parse(args.url)
    except ValueError as e:
        forms = "\n".join(f"  {form}" for form in urls.ACCEPTED_FORMS)
        parser.error(f"argument URL: {e}\nAccepted forms:\n{forms}")

    def fail(message: str) -> NoReturn:
        print(f"{prog}: error: {message}", file=sys.stderr)
        sys.exit(1)

    owner, repo = file_url.owner, file_url.repo
    try:
        client = github.Client(github.resolve_token())
        sha, path = client.resolve(owner, repo, file_url.segments)
        target = client.fetch(owner, repo, sha, path)
    except (NotFound, UpstreamError) as e:
        fail(str(e))
    except KeyboardInterrupt:
        sys.exit(130)

    repo_commit = f"{owner}/{repo}@{sha[:7]}"
    try:
        server = PreviewServer(
            partial(client.fetch, owner, repo, sha),
            path,
            repo_commit,
            port=args.port,
            seed={path: target},
        )
    except OSError as e:
        fail(f"Couldn't bind to {HOST}:{args.port}: {e.strerror or e}")

    timeout = timedelta(seconds=args.timeout)
    with server:
        print(f"Serving {repo_commit}:{path} at {server.url}")
        print(f"Shutting down after {timeout} without requests. Press Ctrl-C to stop.", flush=True)
        try:
            if not args.no_browser:
                open_browser(server.url)
            server.serve_until_idle(args.timeout)
            print(f"No requests for {timeout}. Stopped.")
        except KeyboardInterrupt:
            print(" Stopped.")
