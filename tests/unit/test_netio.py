"""Sandboxed outbound fetching (module: netio)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from reggraph.netio import FetchResult, SafeFetcher, UpstreamError, https_transport, validate_url

HOSTS = {"www.ecb.europa.eu", "eur-lex.europa.eu"}
OK = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
XML = ("application/xml",)


def fetcher(addresses: list[str] | None = None, result: FetchResult | None = None) -> SafeFetcher:
    seen: list[tuple[str, str, str]] = []

    def transport(host, ip, port, path, accept, timeout, max_bytes):  # type: ignore[no-untyped-def]
        seen.append((host, ip, path))
        return result or FetchResult(200, "application/xml", b"<a/>")

    f = SafeFetcher(HOSTS, resolver=lambda h: addresses or ["93.184.216.34"], transport=transport)
    f.seen = seen  # type: ignore[attr-defined]
    return f


def test_good_request_is_pinned_to_the_checked_address() -> None:
    f = fetcher(["93.184.216.34"])
    assert f.get(OK + "?x=1", XML).body == b"<a/>"
    assert f.seen == [
        ("www.ecb.europa.eu", "93.184.216.34", "/stats/eurofxref/eurofxref-daily.xml?x=1")
    ]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.ecb.europa.eu/x",  # not https
        "ftp://www.ecb.europa.eu/x",
        "https://evil.example/x",  # not on the list
        "https://www.ecb.europa.eu.evil.example/x",  # look-alike suffix
        "https://evilwww.ecb.europa.eu/x",
        "https://user:pw@www.ecb.europa.eu/x",  # credentials
        "https://www.ecb.europa.eu@evil.example/x",  # userinfo trick
        "https://www.ecb.europa.eu:8443/x",  # other port
        "https://www.ecb.europa.eu:99999/x",
        "https://www.ecb.europa.eu/x y",  # space
        "https://www.ecb.europa.eu/x\r\nHost: evil",  # header splitting
        "https://www.ecb.europa.eu/x\tz",
        "https://www.ecb.europa.eu\\@evil.example/",  # backslash confusion
        "https://www.ecb.europa.eu/x#frag",
        "https://wwŵ.ecb.europa.eu/x",  # non-ASCII look-alike
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "//www.ecb.europa.eu/x",
        "",
        "https://www.ecb.europa.eu/" + "a" * 3000,
    ],
)
def test_bad_urls_are_refused(url: str) -> None:
    with pytest.raises(UpstreamError):
        validate_url(url, HOSTS)
    with pytest.raises(UpstreamError):
        fetcher().get(url, XML)


def test_host_match_is_case_insensitive_but_exact() -> None:
    assert validate_url("https://WWW.ECB.EUROPA.EU/x", HOSTS)[0] == "www.ecb.europa.eu"
    assert validate_url("https://www.ecb.europa.eu:443/x", HOSTS)[1] == "/x"
    assert validate_url("https://www.ecb.europa.eu", HOSTS)[1] == "/"


@pytest.mark.parametrize(
    "bad_ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.9.9",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.1.1.1",
        "not-an-ip",
    ],
)
def test_non_public_addresses_are_refused(bad_ip: str) -> None:
    with pytest.raises(UpstreamError, match="non-public"):
        fetcher([bad_ip]).get(OK, XML)


def test_one_bad_address_among_good_ones_refuses_the_host() -> None:
    with pytest.raises(UpstreamError):
        fetcher(["93.184.216.34", "10.0.0.1"]).get(OK, XML)


def test_dns_failure_and_empty_answer() -> None:
    def broken(_: str) -> list[str]:
        raise OSError("no dns")

    with pytest.raises(UpstreamError, match="resolution"):
        SafeFetcher(HOSTS, resolver=broken).get(OK, XML)
    with pytest.raises(UpstreamError):
        SafeFetcher(HOSTS, resolver=lambda h: []).get(OK, XML)


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (FetchResult(301, "text/html", b""), "redirect"),
        (FetchResult(302, "application/xml", b"<a/>"), "redirect"),
        (FetchResult(404, "application/xml", b""), "status"),
        (FetchResult(500, "application/xml", b""), "status"),
        (FetchResult(200, "text/html", b"<html>"), "content type"),
        (FetchResult(200, "application/xml", b"x" * 100), "too large"),
    ],
)
def test_bad_answers_are_refused(result: FetchResult, message: str) -> None:
    f = SafeFetcher(
        HOSTS,
        resolver=lambda h: ["93.184.216.34"],
        transport=lambda *a: result,
        max_bytes=50,
    )
    with pytest.raises(UpstreamError, match=message):
        f.get(OK, XML)


@pytest.mark.parametrize("exc", [OSError("reset"), TimeoutError(), ssl.SSLError("bad cert")])
def test_transport_errors_become_generic_errors(exc: Exception) -> None:
    def boom(*_a: object) -> FetchResult:
        raise exc

    with pytest.raises(UpstreamError, match="transfer failed"):
        SafeFetcher(HOSTS, resolver=lambda h: ["93.184.216.34"], transport=boom).get(OK, XML)


def test_empty_accept_list_is_refused() -> None:
    with pytest.raises(UpstreamError):
        fetcher().get(OK, ())


# ----------------------------------------------------------------- the real transport
@pytest.fixture(scope="module")
def tls_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[int, ssl.SSLContext]]:
    """A local HTTPS server with a throw-away certificate for the name ``feed.test``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "feed.test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("feed.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    folder: Path = tmp_path_factory.mktemp("tls")
    cert_file, key_file = folder / "c.pem", folder / "k.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<ok/>" if self.path == "/small" else b"x" * 200_000
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_file, key_file)
    server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_ctx = ssl.create_default_context(cafile=str(cert_file))
    yield server.server_address[1], client_ctx
    server.shutdown()


def test_real_transport_reads_over_verified_tls(tls_server: tuple[int, ssl.SSLContext]) -> None:
    port, ctx = tls_server
    got = https_transport("feed.test", "127.0.0.1", port, "/small", XML, 5, 10_000, context=ctx)
    assert (got.status, got.content_type, got.body) == (200, "application/xml", b"<ok/>")


def test_real_transport_enforces_the_size_limit(tls_server: tuple[int, ssl.SSLContext]) -> None:
    port, ctx = tls_server
    with pytest.raises(UpstreamError, match="too large"):
        https_transport("feed.test", "127.0.0.1", port, "/big", XML, 5, 10_000, context=ctx)


def test_real_transport_checks_the_certificate_name(
    tls_server: tuple[int, ssl.SSLContext],
) -> None:
    port, ctx = tls_server
    with pytest.raises(ssl.SSLCertVerificationError):
        https_transport("other.test", "127.0.0.1", port, "/small", XML, 5, 10_000, context=ctx)


def test_real_transport_rejects_an_untrusted_certificate(
    tls_server: tuple[int, ssl.SSLContext],
) -> None:
    port, _ = tls_server
    with pytest.raises(ssl.SSLCertVerificationError):
        https_transport("feed.test", "127.0.0.1", port, "/small", XML, 5, 10_000)


def test_loopback_literal_is_not_public() -> None:
    assert not ipaddress.ip_address("127.0.0.1").is_global
