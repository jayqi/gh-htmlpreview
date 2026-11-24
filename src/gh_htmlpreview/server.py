from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import posixpath
import socket
import socketserver
import sys
import threading
import time
from urllib.parse import quote, unquote

from gh_htmlpreview import __version__
from gh_htmlpreview.exceptions import NotFound, UpstreamError

Source = Callable[[str], bytes]
"""Returns the contents of a file by its repository path. Raises NotFound or UpstreamError."""

HOST = "127.0.0.1"

_ADDRESS_IN_USE = tuple(
    code
    for name in ("EADDRINUSE", "WSAEADDRINUSE")
    if (code := getattr(errno, name, None)) is not None
)

_CONTROL_CHARS = {c: f"\\x{c:02x}" for c in (*range(0x20), *range(0x7F, 0xA0))} | {0x5C: "\\\\"}

MIME_OVERRIDES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
}


def content_type(path: str) -> str:
    ext = posixpath.splitext(path)[1].lower()
    if ext in MIME_OVERRIDES:
        mime_type = MIME_OVERRIDES[ext]
    else:
        mime_type, encoding = mimetypes.guess_type(path, strict=False)
        # A compressed file, such as data.json.gz, would need a Content-Encoding.
        if mime_type is None or encoding is not None:
            return "application/octet-stream"
    if mime_type.startswith("text/"):
        return f"{mime_type}; charset=utf-8"
    return mime_type


def normalize_path(request_path: str) -> str | None:
    """Map a request path to a repository path. Returns None if it escapes the root."""
    path = unquote(request_path.split("?", 1)[0].split("#", 1)[0])
    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not segments:
                return None
            segments.pop()
        else:
            segments.append(segment)
    if path.endswith("/"):
        segments.append("index.html")
    return "/".join(segments)


def _prepare_socket(sock: socket.socket) -> None:
    # On Windows, SO_REUSEADDR allows binding a port that another socket is listening on.
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def _check_port_free(port: int) -> None:
    """Raise the bind error if another socket holds the port on an address that receives
    127.0.0.1 traffic. Each probe binds a throwaway socket and closes it without listening.
    """
    for family, address in (
        (socket.AF_INET, HOST),
        (socket.AF_INET, "0.0.0.0"),
        (socket.AF_INET6, "::"),
    ):
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue  # Address family not supported
        with sock:
            _prepare_socket(sock)
            try:
                sock.bind((address, port))
            except OSError as e:
                if e.errno in _ADDRESS_IN_USE:
                    raise


class _NotFoundEntry:
    pass


class _Cache:
    """Caches a source's files and misses. Upstream errors aren't cached."""

    def __init__(self, source: Source, seed: Mapping[str, bytes]):
        self._source = source
        self._entries: dict[str, bytes | _NotFoundEntry] = dict(seed)
        self._lock = threading.Lock()

    def get(self, path: str) -> bytes:
        with self._lock:
            entry = self._entries.get(path)
        if entry is None:
            try:
                entry = self._source(path)
            except NotFound:
                entry = _NotFoundEntry()
            with self._lock:
                self._entries[path] = entry
        if isinstance(entry, _NotFoundEntry):
            raise NotFound(path)
        return entry


class _Handler(BaseHTTPRequestHandler):
    server_version = f"gh-htmlpreview/{__version__}"
    sys_version = ""
    timeout = 60

    def do_GET(self):
        if self._check_request():
            with self._server()._track_request():
                self._serve(head=False)

    def do_HEAD(self):
        if self._check_request(head=True):
            with self._server()._track_request():
                self._serve(head=True)

    def _method_not_allowed(self):
        if self._check_request():
            self._send_text(405, "Method not allowed.", extra_headers={"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format, *args):
        # Same as BaseHTTPRequestHandler on Python 3.12+, which escapes control characters
        message = (format % args).translate(_CONTROL_CHARS)
        sys.stderr.write(
            f"{self.address_string()} - - [{self.log_date_time_string()}] {message}\n"
        )

    def _server(self) -> "PreviewServer":
        assert isinstance(self.server, PreviewServer)
        return self.server

    def _check_request(self, head: bool = False) -> bool:
        """Send an error and return False if the request must be refused."""
        port = self._server().port
        # Clients leave out the default port.
        allowed_hosts = (f"{HOST}:{port}", HOST) if port == 80 else (f"{HOST}:{port}",)
        if self.headers.get("Host") not in allowed_hosts:
            self._send_text(403, f"Forbidden host. Open http://{HOST}:{port}/ instead.", head=head)
            return False
        # Browsers send "none" for navigations the user started, such as opening the URL.
        if self.headers.get("Sec-Fetch-Site", "none") not in ("same-origin", "none"):
            self._send_text(403, "Requests from other sites aren't allowed.", head=head)
            return False
        if self.headers.get("Service-Worker") == "script":
            self.log_message("Blocked service worker registration: %s", self.path)
            self._send_text(403, "Service workers aren't allowed.", head=head)
            return False
        if not self.path.startswith("/"):
            self._send_text(400, "Bad request.", head=head)
            return False
        return True

    def _serve(self, head: bool) -> None:
        server = self._server()
        if self.path.split("?", 1)[0] == "/":
            self.send_response(302)
            self.send_header("Location", f"/{quote(server.target_path)}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = normalize_path(self.path)
        if not path:
            self._send_text(404, "Not found.", head=head)
            return
        try:
            body = server.cache.get(path)
        except NotFound:
            self._send_text(404, "Not found.", head=head)
            return
        except UpstreamError as e:
            self.log_message("Error fetching %s: %s", path, e)
            self._send_text(502, f"Error fetching {path} from GitHub.", head=head)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type(path))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_text(
        self,
        code: int,
        text: str,
        head: bool = False,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        body = f"{text}\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)


class PreviewServer(ThreadingHTTPServer):
    """Serves a repository's files from a source on 127.0.0.1, and shuts down when idle.

    Args:
        source: Returns a file's contents by repository path.
        target_path: Repository path of the file being previewed. `/` redirects to it.
        port: Port to bind to. 0 uses a port chosen by the operating system.
        seed: Files already fetched, by repository path.
    """

    allow_reuse_address = False  # _prepare_socket sets this per platform

    def __init__(
        self,
        source: Source,
        target_path: str,
        port: int = 0,
        seed: Mapping[str, bytes] | None = None,
    ):
        self.target_path = target_path
        self.cache = _Cache(source, seed or {})
        self._lock = threading.Lock()
        self._in_flight = 0
        self._last_activity = time.monotonic()
        super().__init__((HOST, port), _Handler)

    def server_bind(self):
        _prepare_socket(self.socket)
        # Probes run before the real bind. On Linux, they'd conflict with this socket after it.
        if self.server_address[1] != 0:
            _check_port_free(int(self.server_address[1]))
        # Skips HTTPServer.server_bind, whose reverse DNS lookup for server_name can hang.
        socketserver.TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.port

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        """URL of the file being previewed."""
        return f"http://{HOST}:{self.port}/{quote(self.target_path)}"

    @contextmanager
    def _track_request(self) -> Generator[None, None, None]:
        """Mark a request as in progress. The server doesn't count as idle while any request
        is in progress, and the idle time restarts when the last one finishes.
        """
        with self._lock:
            self._in_flight += 1
            self._last_activity = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1
                self._last_activity = time.monotonic()

    def _idle_seconds(self) -> float:
        with self._lock:
            if self._in_flight:
                return 0.0
            return time.monotonic() - self._last_activity

    def serve_until_idle(self, idle_timeout: float) -> None:
        """Serve requests until no request has been in progress for `idle_timeout` seconds.
        The server is shut down on return, including when interrupted by an exception such
        as `KeyboardInterrupt`.
        """
        with self._lock:
            self._last_activity = time.monotonic()
        threading.Thread(target=self.serve_forever, args=(0.1,), daemon=True).start()
        try:
            # Short sleeps keep KeyboardInterrupt responsive on all platforms.
            while (idle := self._idle_seconds()) < idle_timeout:
                time.sleep(min(0.5, idle_timeout - idle))
        finally:
            self.shutdown()
