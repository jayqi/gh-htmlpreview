from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
import http.client
import socket
import threading
import time

import pytest

from gh_htmlpreview.exceptions import NotFound, UpstreamError
from gh_htmlpreview.server import PreviewServer, content_type, normalize_path

TARGET = "docs/report.html"
FILES = {
    "docs/report.html": b"<html>report</html>",
    "docs/style.css": b"body {}",
    "docs/sub/index.html": b"<html>sub</html>",
    "index.html": b"<html>root</html>",
    "assets/app.js": b"console.log(1)",
    "img/logo.png": b"\x89PNG",
    "my file.html": b"<html>spaces</html>",
}


class DictSource:
    def __init__(self, files: dict[str, bytes], errors: dict[str, Exception] | None = None):
        self.files = files
        self.errors = errors or {}
        self.calls: Counter[str] = Counter()

    def __call__(self, path: str) -> bytes:
        self.calls[path] += 1
        if path in self.errors:
            raise self.errors[path]
        if path not in self.files:
            raise NotFound(path)
        return self.files[path]


@contextmanager
def running(server: PreviewServer) -> Iterator[PreviewServer]:
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def source():
    return DictSource(dict(FILES), errors={"broken.html": UpstreamError("HTTP 500 from GitHub")})


@pytest.fixture
def server(source):
    with running(PreviewServer(source, TARGET)) as server:
        yield server


def request(
    server: PreviewServer,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    host: str | None = "default",
    timeout: float = 10,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", f"127.0.0.1:{server.port}" if host == "default" else host)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        conn.endheaders()
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


def test_target_file(server):
    status, headers, body = request(server, "/docs/report.html")
    assert status == 200
    assert body == FILES["docs/report.html"]
    assert headers["content-type"] == "text/html"
    assert headers["content-length"] == str(len(body))


def test_url(server):
    assert server.url == f"http://127.0.0.1:{server.port}/docs/report.html"


def test_url_quotes_path(source):
    server = PreviewServer(source, "my file.html")
    server.server_close()
    assert server.url == f"http://127.0.0.1:{server.port}/my%20file.html"


def test_head(server):
    status, headers, body = request(server, "/docs/report.html", method="HEAD")
    assert status == 200
    assert body == b""
    assert headers["content-length"] == str(len(FILES["docs/report.html"]))


def test_root_redirects_to_target(server):
    for path in ["/", "/?x=1"]:
        status, headers, _ = request(server, path)
        assert status == 302
        assert headers["location"] == "/docs/report.html"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/docs/style.css", "docs/style.css"),
        ("/assets/app.js", "assets/app.js"),
        ("/docs/../img/logo.png", "img/logo.png"),
        ("/docs/./style.css", "docs/style.css"),
        ("/docs//style.css", "docs/style.css"),
        ("/docs/style.css?v=123", "docs/style.css"),
        ("/docs/style.css#top", "docs/style.css"),
        ("/my%20file.html", "my file.html"),
        ("/docs/sub/", "docs/sub/index.html"),
        ("//index.html", "index.html"),
    ],
)
def test_asset_paths(server, path, expected):
    status, _, body = request(server, path)
    assert status == 200
    assert body == FILES[expected]


@pytest.mark.parametrize(
    "path",
    [
        "/../index.html",
        "/docs/../../index.html",
        "/%2e%2e/index.html",
        "/docs/%2E%2E/%2e%2e/index.html",
        "/..%2findex.html",
        "/.",
    ],
)
def test_escaping_root(server, source, path):
    status, _, _ = request(server, path)
    assert status == 404
    assert source.calls == Counter()


@pytest.mark.parametrize("path", ["/docs", "/docs/", "/missing.html", "/img/"])
def test_not_found(server, path):
    status, headers, body = request(server, path)
    assert status == 404
    assert headers["content-type"] == "text/plain; charset=utf-8"
    assert body == b"Not found.\n"


def test_log_escapes_control_characters(source, capsys):
    source.errors["\x1b[31mred.html"] = UpstreamError("failed")
    with running(PreviewServer(source, TARGET)) as server:
        assert request(server, "/%1b[31mred.html")[0] == 502
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "Error fetching \\x1b[31mred.html: failed" in err


def test_upstream_error(server, capsys):
    status, headers, body = request(server, "/broken.html")
    assert status == 502
    assert headers["content-type"] == "text/plain; charset=utf-8"
    assert body == b"Error fetching broken.html from GitHub.\n"
    assert "Error fetching broken.html: HTTP 500 from GitHub" in capsys.readouterr().err


@pytest.mark.parametrize(
    "path", ["/docs/report.html", "/", "/missing.html", "/broken.html", "/../x"]
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_common_headers(server, path, method):
    _, headers, _ = request(server, path, method=method)
    assert headers["cache-control"] == "no-store"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["content-security-policy"] == "frame-ancestors 'self'"
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "host",
    [
        "localhost:{port}",
        "LOCALHOST:{port}",
        "evil.example:{port}",
        "evil.example",
        "127.0.0.1",
        "localhost",
        "127.0.0.1:{other}",
        "127.0.0.1.evil.example:{port}",
        "[::1]:{port}",
        "",
        None,
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_forbidden_hosts(server, source, host, method):
    host = host if host is None else host.format(port=server.port, other=server.port + 1)
    status, _, body = request(server, "/docs/style.css", method=method, host=host)
    assert status == 403
    assert body == (b"" if method == "HEAD" else b"Forbidden host.\n")
    assert source.calls == Counter()


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_allowed_fetch_sites(server, site):
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    status, _, _ = request(server, "/docs/report.html", headers=headers)
    assert status == 200


@pytest.mark.parametrize("site", ["same-site", "cross-site", "unknown"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_forbidden_fetch_sites(server, source, site, method):
    status, _, body = request(
        server, "/docs/style.css", method=method, headers={"Sec-Fetch-Site": site}
    )
    assert status == 403
    assert body == (b"" if method == "HEAD" else b"Requests from other sites aren't allowed.\n")
    assert source.calls == Counter()


def test_service_worker_blocked(server, source, capsys):
    status, _, body = request(server, "/docs/style.css", headers={"Service-Worker": "script"})
    assert status == 403
    assert body == b"Service workers aren't allowed.\n"
    assert source.calls == Counter()
    assert "Blocked service worker registration: /docs/style.css" in capsys.readouterr().err


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_method_not_allowed(server, method):
    status, headers, _ = request(server, "/docs/report.html", method=method)
    assert status == 405
    assert headers["allow"] == "GET, HEAD"


def test_absolute_form_request_rejected(server, source):
    status, _, _ = request(server, f"http://127.0.0.1:{server.port}/docs/style.css")
    assert status == 400
    assert source.calls == Counter()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.html", "text/html"),
        ("a.HTM", "text/html"),
        ("a.js", "text/javascript"),
        ("a.mjs", "text/javascript"),
        ("a.css", "text/css"),
        ("a.json", "application/json"),
        ("a.svg", "image/svg+xml"),
        ("a.wasm", "application/wasm"),
        ("a.png", "image/png"),
        ("a.unknownext", "application/octet-stream"),
        ("Makefile", "application/octet-stream"),
    ],
)
def test_content_type(path, expected):
    assert content_type(path) == expected


@pytest.mark.parametrize("path", ["/a.css", "/a.svg", "/a.wasm"])
def test_content_type_header(source, path):
    source.files[path[1:]] = b""
    with running(PreviewServer(source, TARGET)) as server:
        _, headers, _ = request(server, path)
    assert headers["content-type"] == content_type(path)


@pytest.mark.parametrize(
    ("request_path", "expected"),
    [
        ("/a/b.html", "a/b.html"),
        ("/a/../b.html", "b.html"),
        ("/a/", "a/index.html"),
        ("/", "index.html"),
        ("/..", None),
        ("/a/../..", None),
        ("/a/%2e%2e/%2e%2e/b", None),
        ("/a%2Fb.html", "a/b.html"),
    ],
)
def test_normalize_path(request_path, expected):
    assert normalize_path(request_path) == expected


# Cache


def test_cache_hit(server, source):
    for _ in range(3):
        assert request(server, "/docs/style.css")[0] == 200
    assert source.calls == Counter({"docs/style.css": 1})


def test_cache_miss_cached(server, source):
    for _ in range(3):
        assert request(server, "/favicon.ico")[0] == 404
    assert source.calls == Counter({"favicon.ico": 1})


def test_upstream_error_not_cached(server, source):
    for _ in range(2):
        assert request(server, "/broken.html")[0] == 502
    assert source.calls == Counter({"broken.html": 2})


def test_seeded_target_not_fetched(source):
    seed = {TARGET: b"<html>seeded</html>"}
    with running(PreviewServer(source, TARGET, seed=seed)) as server:
        status, _, body = request(server, "/docs/report.html")
    assert (status, body) == (200, b"<html>seeded</html>")
    assert source.calls == Counter()


# Binding


def test_binds_localhost_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_fixed_port(source):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with running(PreviewServer(source, TARGET, port=port)) as server:
        assert server.port == port
        assert request(server, "/docs/report.html")[0] == 200


def test_fixed_port_in_use(server, source):
    with pytest.raises(OSError):
        PreviewServer(source, TARGET, port=server.port)


# Idle shutdown


def test_idle_shutdown(source):
    server = PreviewServer(source, TARGET)
    start = time.monotonic()
    server.serve_until_idle(0.3)
    elapsed = time.monotonic() - start
    server.server_close()
    assert 0.3 <= elapsed < 2


def test_requests_delay_idle_shutdown(source):
    server = PreviewServer(source, TARGET)
    shutdown = threading.Event()

    def serve():
        server.serve_until_idle(0.5)
        shutdown.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    # Each request restarts the idle time, so the server outlives the timeout.
    for _ in range(4):
        time.sleep(0.25)
        assert request(server, "/docs/report.html")[0] == 200
    assert not shutdown.is_set()
    assert shutdown.wait(5)
    server.server_close()


def test_request_in_progress_delays_idle_shutdown():
    release = threading.Event()
    started = threading.Event()

    def slow_source(path: str) -> bytes:
        started.set()
        release.wait(10)
        return b"slow"

    server = PreviewServer(slow_source, TARGET)
    shutdown = threading.Event()

    def serve():
        server.serve_until_idle(0.3)
        shutdown.set()

    serve_thread = threading.Thread(target=serve, daemon=True)
    serve_thread.start()
    results = []
    client = threading.Thread(target=lambda: results.append(request(server, "/slow.html")))
    client.start()
    assert started.wait(5)
    time.sleep(0.8)
    assert not shutdown.is_set()
    release.set()
    client.join(5)
    assert results[0][2] == b"slow"
    assert shutdown.wait(5)
    server.server_close()


def test_refused_requests_dont_delay_idle_shutdown(source):
    server = PreviewServer(source, TARGET)
    shutdown = threading.Event()

    def serve():
        server.serve_until_idle(0.5)
        shutdown.set()

    thread = threading.Thread(target=serve, daemon=True)
    start = time.monotonic()
    thread.start()
    while not shutdown.wait(0.1) and time.monotonic() - start < 5:
        try:
            request(server, "/docs/report.html", host="evil.example", timeout=0.5)
        except OSError:
            pass
    assert shutdown.is_set()
    assert time.monotonic() - start < 2
    server.server_close()


def test_keyboard_interrupt_shuts_down(source, monkeypatch):
    server = PreviewServer(source, TARGET)

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        server.serve_until_idle(10)
    server.server_close()
