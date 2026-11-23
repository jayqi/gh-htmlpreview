import argparse
from collections.abc import Sequence
import re
import sys

from gh_htmlpreview import __version__, urls

_DURATION_PATTERN = re.compile(r"(\d+(?:\.\d+)?)([smh]?)")
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration(value: str) -> float:
    """Parse a duration like '90s', '10m', '1h' or '600' into seconds."""
    match = _DURATION_PATTERN.fullmatch(value.strip().lower())
    if match is None:
        raise argparse.ArgumentTypeError(
            f"Unable to parse {value!r} as a duration. Use a number with an optional unit s, m "
            "or h, such as '90s', '10m' or '1h'."
        )
    seconds = float(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"Duration must be positive, got {value!r}.")
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
        default="10m",
        type=parse_duration,
        metavar="DURATION",
        help=(
            "Time to keep the server alive after the most recent request, such as '90s', '10m' "
            "or '1h'. A number without a unit is seconds. (default: %(default)s)"
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


def main(argv: Sequence[str] | None = None, prog: str = "gh-htmlpreview") -> None:
    """Run the gh-htmlpreview command-line program."""
    parser = _parser(prog)
    args = parser.parse_args(argv)
    try:
        urls.parse(args.url)
    except ValueError as e:
        forms = "\n".join(f"  {form}" for form in urls.ACCEPTED_FORMS)
        parser.error(f"argument URL: {e}\nAccepted forms:\n{forms}")
    print(f"{prog}: not implemented yet", file=sys.stderr)
    sys.exit(1)
