from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

ACCEPTED_FORMS = (
    "https://github.com/{owner}/{repo}/blob/{ref}/{path}",
    "https://github.com/{owner}/{repo}/raw/{ref}/{path}",
    "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}",
)


@dataclass(frozen=True)
class FileURL:
    """A file in a GitHub repository. The ref and the file path are still combined in
    segments, because a ref can contain '/'."""

    owner: str
    repo: str
    segments: tuple[str, ...]


def parse(url: str) -> FileURL:
    """Parse a GitHub URL to a file. Raises ValueError for unsupported URLs."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if parts.scheme.lower() not in ("https", "http"):
        raise ValueError(f"Unsupported URL: {url!r}")
    segments = [s for s in unquote(parts.path).split("/") if s]
    if any(s in (".", "..") for s in segments):
        raise ValueError(f"Unsupported URL: {url!r}")

    if host == "github.com" and len(segments) >= 3 and segments[2] in ("blob", "raw"):
        owner, repo, _, *rest = segments
    elif host == "raw.githubusercontent.com" and len(segments) >= 2:
        owner, repo, *rest = segments
    else:
        raise ValueError(f"Unsupported URL: {url!r}")

    if len(rest) < 2:
        raise ValueError(f"URL doesn't include both a ref and a file path: {url!r}")
    return FileURL(owner=owner, repo=repo, segments=tuple(rest))
