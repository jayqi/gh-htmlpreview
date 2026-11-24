from email.message import Message
import io
from typing import BinaryIO
from urllib.parse import unquote, urlsplit
import urllib.request
import urllib.response

SHA = "3f2c1a9d0e8b7c6a5f4e3d2c1b0a9f8e7d6c5b4a"
TOKEN = "ghp_secret"


def response(
    url: str,
    code: int = 200,
    body: bytes | BinaryIO = b"",
    headers: dict[str, str] | None = None,
) -> urllib.response.addinfourl:
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    fp = io.BytesIO(body) if isinstance(body, bytes) else body
    result = urllib.response.addinfourl(fp, message, url, code)
    result.msg = ""  # Read by urllib's HTTPErrorProcessor
    return result


class Transport(urllib.request.BaseHandler):
    """Answers requests in place of urllib's HTTP handlers, and records them."""

    handler_order = 0  # Ahead of urllib's proxy and HTTP handlers

    def __init__(self, respond=None):
        self.requests: list[urllib.request.Request] = []
        self.responses: list[urllib.response.addinfourl] = []
        if respond is not None:
            self.respond = respond

    def respond(self, request: urllib.request.Request) -> urllib.response.addinfourl:
        raise NotImplementedError

    def https_open(self, request):
        self.requests.append(request)
        result = self.respond(request)
        self.responses.append(result)
        return result

    http_open = https_open

    def urls(self) -> list[str]:
        return [r.full_url for r in self.requests]


class FakeGitHub(Transport):
    """Serves the commits API, the contents API and raw.githubusercontent.com for octo/repo.

    Args:
        refs: Commit SHAs by branch or tag name.
        files: File contents by path, at SHA.
        private: Whether requests need TOKEN.
    """

    def __init__(self, refs=None, files=None, private=False):
        super().__init__()
        self.refs = refs if refs is not None else {"main": SHA}
        self.files = files if files is not None else {}
        self.private = private
        self.error: BaseException | None = None
        """Raised for every request, if set."""

    def respond(self, request):
        if self.error is not None:
            raise self.error
        url = request.full_url
        parts = urlsplit(url)
        authorized = request.get_header("Authorization") == f"Bearer {TOKEN}"
        if parts.netloc == "api.github.com":
            prefix = "/repos/octo/repo/"
            if not parts.path.startswith(prefix) or (self.private and not authorized):
                return response(url, 404)
            kind, _, rest = parts.path.removeprefix(prefix).partition("/")
            if kind == "commits":
                sha = self._commit(unquote(rest))
                if sha is None:
                    return response(url, 422)
                return response(url, body=sha.encode())
            if kind == "contents":
                assert parts.query == f"ref={SHA}"
                return self._file(url, unquote(rest), "application/vnd.github.raw+json")
        if parts.netloc == "raw.githubusercontent.com":
            prefix = f"/octo/repo/{SHA}/"
            if not parts.path.startswith(prefix) or self.private:
                return response(url, 404)
            return self._file(url, unquote(parts.path.removeprefix(prefix)), "text/plain")
        raise AssertionError(f"Unexpected URL {url}")

    def _commit(self, ref: str) -> str | None:
        if ref.startswith(("refs/heads/", "refs/tags/")):
            ref = ref.split("/", 2)[2]
        if ref in self.refs:
            return self.refs[ref]
        # Like GitHub, match a SHA prefix of 4 or more characters.
        if len(ref) >= 4:
            for sha in self.refs.values():
                if sha.startswith(ref.lower()):
                    return sha
        return None

    def _file(self, url: str, path: str, content_type: str):
        if path in self.files:
            return response(url, body=self.files[path], headers={"Content-Type": content_type})
        if any(p.startswith(f"{path}/") for p in self.files):
            if urlsplit(url).netloc == "raw.githubusercontent.com":
                return response(url, 404)
            return response(url, body=b"[]", headers={"Content-Type": "application/json"})
        return response(url, 404)
