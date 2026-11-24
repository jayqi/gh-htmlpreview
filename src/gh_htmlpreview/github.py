from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
import http.client
import json
import os
import re
import subprocess
from typing import Protocol
import urllib.error
from urllib.parse import quote, urlsplit
import urllib.request

from gh_htmlpreview import __version__
from gh_htmlpreview.exceptions import NotFound, UpstreamError

API_URL = "https://api.github.com"
RAW_URL = "https://raw.githubusercontent.com"
TIMEOUT_SECONDS = 30

_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_QUALIFIED_REF_PREFIXES = (("refs", "heads"), ("refs", "tags"))
_AUTH_HINT = "If it's a private repository, run 'gh auth login' or set GH_TOKEN."


class Response(Protocol):
    @property
    def headers(self) -> Message: ...

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class _SameHostAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keeps the Authorization header on a redirect only if it stays on the same HTTPS host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        auth = req.get_header("Authorization")
        if new is not None and auth is not None:
            old_parts, new_parts = urlsplit(req.full_url), urlsplit(new.full_url)
            if new_parts.scheme == "https" and new_parts.netloc == old_parts.netloc:
                new.add_unredirected_header("Authorization", auth)
        return new


@dataclass(frozen=True)
class Token:
    value: str
    source: str
    """Where the token came from, for error messages."""


def resolve_token(
    env: Mapping[str, str] = os.environ,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Token | None:
    """Find a GitHub token from the environment or the GitHub CLI."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = env.get(name, "").strip()
        if value:
            return Token(value, name)
    try:
        result = run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Token(value, "gh auth") if value else None


def _quote_path(segments: Sequence[str]) -> str:
    return "/".join(quote(s, safe="") for s in segments)


def _error_detail(error: urllib.error.HTTPError) -> str | None:
    """The message from GitHub's JSON error body, if there is one."""
    try:
        body = json.loads(error.read(65536))
    except (OSError, http.client.HTTPException, ValueError):
        return None
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return body["message"].strip() or None
    return None


class Client:
    """Resolves refs and fetches files from GitHub.

    Args:
        token: Token to authenticate with. Without one, files come from raw.githubusercontent.com.
        transport: Handler that sends requests in place of urllib's HTTP handlers.
    """

    def __init__(self, token: Token | None, transport: urllib.request.BaseHandler | None = None):
        self.token = token
        handlers: list[urllib.request.BaseHandler] = [_SameHostAuthRedirectHandler()]
        if transport is not None:
            handlers.append(transport)
        self._opener = urllib.request.build_opener(*handlers)

    def resolve(self, owner: str, repo: str, segments: Sequence[str]) -> tuple[str, str]:
        """Split segments into a ref and a file path, and resolve the ref to a commit SHA.

        Returns the commit SHA and the file path.
        """
        start = 3 if tuple(segments[:2]) in _QUALIFIED_REF_PREFIXES else 1
        for end in range(start, len(segments)):
            ref = segments[:end]
            url = f"{API_URL}/repos/{_quote_path([owner, repo])}/commits/{_quote_path(ref)}"
            try:
                body = self._get(url, accept="application/vnd.github.sha")
            except urllib.error.HTTPError as e:
                if e.code == 422:
                    continue
                if e.code == 404:
                    if self.token is None:
                        message = f"Repository {owner}/{repo} not found. {_AUTH_HINT}"
                    else:
                        message = (
                            f"Repository {owner}/{repo} not found, or the token from "
                            f"{self.token.source} can't access it."
                        )
                    raise NotFound(message) from None
                raise UpstreamError(self._describe_http_error(e, url)) from None
            sha = body.decode("ascii", errors="replace").strip()
            if not _SHA_PATTERN.fullmatch(sha):
                raise UpstreamError(f"Unexpected response from {url}: {sha[:100]!r}")
            # The commits API also matches SHA prefixes. Only full SHAs are accepted as refs.
            if end == 1 and 4 <= len(ref[0]) < 40 and sha.startswith(ref[0].lower()):
                continue
            return sha, "/".join(segments[end:])
        raise NotFound(
            f"No branch, tag or commit in {owner}/{repo} matches the start of "
            f"{'/'.join(segments)!r}."
        )

    def fetch(self, owner: str, repo: str, sha: str, path: str) -> bytes:
        """Fetch a file's contents at a commit."""
        segments = [s for s in path.split("/") if s]
        if not segments or any(s in (".", "..") for s in segments):
            raise NotFound(f"Invalid file path {path!r}.")
        if self.token is not None:
            url = (
                f"{API_URL}/repos/{_quote_path([owner, repo])}/contents/{_quote_path(segments)}"
                f"?ref={quote(sha, safe='')}"
            )
            accept = "application/vnd.github.raw+json"
        else:
            url = f"{RAW_URL}/{_quote_path([owner, repo, sha, *segments])}"
            accept = "*/*"
        try:
            response = self._open(url, accept=accept)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(f"{path} not found in {owner}/{repo} at {sha[:7]}.") from None
            raise UpstreamError(self._describe_http_error(e, url)) from None
        try:
            # The contents API returns a JSON listing for a directory.
            if response.headers.get_content_type() == "application/json":
                raise NotFound(f"{path} is a directory in {owner}/{repo}, not a file.")
            return self._read(response, url)
        finally:
            response.close()

    def _get(self, url: str, accept: str) -> bytes:
        response = self._open(url, accept=accept)
        try:
            return self._read(response, url)
        finally:
            response.close()

    def _open(self, url: str, accept: str) -> Response:
        request = urllib.request.Request(url)
        request.add_header("Accept", accept)
        request.add_header("User-Agent", f"gh-htmlpreview/{__version__}")
        if url.startswith(f"{API_URL}/"):
            request.add_header("X-GitHub-Api-Version", "2022-11-28")
            if self.token is not None:
                request.add_unredirected_header("Authorization", f"Bearer {self.token.value}")
        try:
            return self._opener.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise UpstreamError(f"Couldn't connect to {urlsplit(url).netloc}: {e}") from None

    def _read(self, response: Response, url: str) -> bytes:
        try:
            return response.read()
        except (OSError, http.client.HTTPException) as e:
            raise UpstreamError(f"Couldn't read response from {url}: {e}") from None

    def _describe_http_error(self, error: urllib.error.HTTPError, url: str) -> str:
        headers = error.headers
        if error.code in (403, 429) and headers is not None:
            if headers.get("x-ratelimit-remaining") == "0" or headers.get("retry-after"):
                message = "GitHub rate limit exceeded."
                if self.token is None:
                    message += " Run 'gh auth login' or set GH_TOKEN for a higher limit."
                return message
        detail = _error_detail(error)
        if error.code == 401 and self.token is not None:
            message = f"GitHub rejected the token from {self.token.source}"
        else:
            message = f"GitHub returned HTTP {error.code} for {url}"
        return f"{message}: {detail}" if detail else message
