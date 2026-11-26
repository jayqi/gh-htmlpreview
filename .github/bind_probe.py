"""Temporary diagnostic: how binds on a port held by another listener behave on this OS."""

import socket
import sys

from gh_htmlpreview.server import _check_port_free, _prepare_socket

PROBES = [
    ("AF_INET 127.0.0.1", socket.AF_INET, "127.0.0.1", None),
    ("AF_INET 0.0.0.0", socket.AF_INET, "0.0.0.0", None),
    ("AF_INET6 :: default", socket.AF_INET6, "::", None),
    ("AF_INET6 :: dual-stack", socket.AF_INET6, "::", 0),
]
HOLDERS = [
    ("127.0.0.1", socket.AF_INET, "127.0.0.1", None),
    ("0.0.0.0", socket.AF_INET, "0.0.0.0", None),
    ("[::] dual-stack", socket.AF_INET6, "::", 0),
    ("[::] v6only", socket.AF_INET6, "::", 1),
]


def describe(e: OSError) -> str:
    return f"errno={e.errno} winerror={getattr(e, 'winerror', None)} {e.strerror}"


def try_bind(family: socket.AddressFamily, address: str, v6only: int | None, port: int) -> str:
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        _prepare_socket(sock)
        if v6only is not None:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
        try:
            sock.bind((address, port))
        except OSError as e:
            return f"FAIL {describe(e)}"
    return "OK"


print(f"platform={sys.platform} python={sys.version.split()[0]}")
with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as fresh:
    v6only = fresh.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
    print(f"default IPV6_V6ONLY={v6only}")

for label, family, address, holder_v6only in HOLDERS:
    print(f"\nHolder listening on {label}:")
    with socket.socket(family, socket.SOCK_STREAM) as holder:
        if holder_v6only is not None:
            holder.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, holder_v6only)
        holder.bind((address, 0))
        holder.listen()
        port = holder.getsockname()[1]
        for probe_label, probe_family, probe_address, probe_v6only in PROBES:
            result = try_bind(probe_family, probe_address, probe_v6only, port)
            print(f"  bind {probe_label} port {port} -> {result}")
        try:
            _check_port_free(port)
        except OSError as e:
            print(f"  _check_port_free -> raised {describe(e)}")
        else:
            print("  _check_port_free -> OK")
