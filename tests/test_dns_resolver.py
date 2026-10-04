"""Unit tests for the DNS fallback helpers (no browser, no network)."""

import socket
import ssl
import urllib.error

import pytest

from crypto_tracker import config, dns_resolver


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """Record retry sleeps instead of actually waiting."""
    slept = []
    monkeypatch.setattr(dns_resolver.time, "sleep", lambda s: slept.append(s))
    return slept


def test_system_addresses_returns_unique_list(monkeypatch):
    def fake_getaddrinfo(host, port, proto=None):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        ]

    monkeypatch.setattr(dns_resolver.socket, "getaddrinfo", fake_getaddrinfo)
    assert dns_resolver.system_addresses("example.com") == ["1.2.3.4", "::1"]


def test_system_addresses_handles_lookup_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(dns_resolver.socket, "getaddrinfo", boom)
    assert dns_resolver.system_addresses("nope.invalid") == []


class FakeResponse:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_doh_addresses_extracts_a_records(monkeypatch):
    payload = (
        '{"Status":0,"Answer":[{"type":1,"data":"9.9.9.9"},'
        '{"type":5,"data":"cloudflare.com"}]}'
    )
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return FakeResponse(payload)

    monkeypatch.setattr(dns_resolver.urllib.request, "urlopen", fake_urlopen)
    addresses = dns_resolver.doh_addresses("coinmarketcap.com")

    assert addresses == ["9.9.9.9"]
    assert "name=coinmarketcap.com" in captured["url"]
    assert "type=A" in captured["url"]


def test_doh_addresses_falls_through_to_second_resolver(monkeypatch):
    def fake_urlopen(request, timeout=None):
        if "cloudflare" in request.full_url:
            raise urllib.error.URLError("blocked")
        return FakeResponse('{"Status":0,"Answer":[{"type":1,"data":"8.8.4.4"}]}')

    monkeypatch.setattr(dns_resolver.urllib.request, "urlopen", fake_urlopen)
    assert dns_resolver.doh_addresses("coinmarketcap.com") == ["8.8.4.4"]


def test_doh_addresses_returns_empty_when_all_fail(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("blocked")

    monkeypatch.setattr(dns_resolver.urllib.request, "urlopen", boom)
    assert dns_resolver.doh_addresses("coinmarketcap.com") == []


def test_doh_addresses_combines_all_resolvers_and_deduplicates(monkeypatch):
    """Every resolver is queried and all answers are pooled.

    Regression: doh_addresses() used to return as soon as the first resolver
    replied, so when all of that provider's addresses failed the TLS check the
    remaining providers were never consulted and the fallback gave up.
    """
    payloads = {
        "cloudflare": (
            '{"Status":0,"Answer":[{"type":1,"data":"1.1.1.1"},'
            '{"type":1,"data":"2.2.2.2"}]}'
        ),
        "google": (
            '{"Status":0,"Answer":[{"type":1,"data":"2.2.2.2"},'
            '{"type":1,"data":"3.3.3.3"}]}'
        ),
    }
    queried = []

    def fake_urlopen(request, timeout=None):
        queried.append(request.full_url)
        if "cloudflare" in request.full_url:
            return FakeResponse(payloads["cloudflare"])
        return FakeResponse(payloads["google"])

    monkeypatch.setattr(dns_resolver.urllib.request, "urlopen", fake_urlopen)

    addresses = dns_resolver.doh_addresses("coinmarketcap.com")

    assert len(queried) == len(config.DOH_RESOLVERS), (
        "every configured resolver must be queried, not just the first one"
    )
    # Both providers contribute, in resolver order, with 2.2.2.2 kept once.
    assert addresses == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert addresses.count("2.2.2.2") == 1


def test_doh_addresses_pools_answers_when_first_resolver_fails(monkeypatch):
    """A dead first resolver must not hide the second resolver's answers."""
    def fake_urlopen(request, timeout=None):
        if "cloudflare" in request.full_url:
            raise urllib.error.URLError("blocked")
        return FakeResponse(
            '{"Status":0,"Answer":[{"type":1,"data":"8.8.4.4"}]}'
        )

    monkeypatch.setattr(dns_resolver.urllib.request, "urlopen", fake_urlopen)

    assert dns_resolver.doh_addresses("coinmarketcap.com") == ["8.8.4.4"]


class FakeSocket:
    """Minimal stand-in for the socket returned by create_connection."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_verify_tls_accepts_valid_certificate(monkeypatch):
    class FakeTLS(FakeSocket):
        def getpeercert(self):
            return {"subject": ((("commonName", "coinmarketcap.com"),),)}

    monkeypatch.setattr(
        dns_resolver.socket, "create_connection",
        lambda addr, timeout=None: FakeSocket(),
    )
    monkeypatch.setattr(
        dns_resolver.ssl.SSLContext, "wrap_socket",
        lambda self, sock, server_hostname=None: FakeTLS(),
    )
    assert dns_resolver.verify_tls("coinmarketcap.com", "1.2.3.4") is True


def test_verify_tls_rejects_bad_certificate(monkeypatch):
    def boom(self, sock, server_hostname=None):
        raise ssl.SSLCertVerificationError("hostname mismatch")

    monkeypatch.setattr(
        dns_resolver.socket, "create_connection",
        lambda addr, timeout=None: FakeSocket(),
    )
    monkeypatch.setattr(dns_resolver.ssl.SSLContext, "wrap_socket", boom)
    assert dns_resolver.verify_tls("coinmarketcap.com", "1.2.3.4") is False


def test_first_working_address_skips_failing_candidates(monkeypatch):
    checked = []

    def fake_verify(host, address, port=443):
        checked.append(address)
        return address == "2.2.2.2"

    monkeypatch.setattr(dns_resolver, "verify_tls", fake_verify)
    result = dns_resolver.first_working_address(
        "coinmarketcap.com", ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    )
    assert result == "2.2.2.2"
    assert checked == ["1.1.1.1", "2.2.2.2"]


def test_resolve_host_returns_none_when_system_dns_is_healthy(monkeypatch):
    monkeypatch.setattr(
        dns_resolver, "system_addresses", lambda host, port=443: ["1.1.1.1"]
    )
    monkeypatch.setattr(dns_resolver, "verify_tls", lambda *a, **k: True)

    def fail(*args, **kwargs):
        raise AssertionError("DoH must not be queried when DNS is healthy")

    monkeypatch.setattr(dns_resolver, "doh_addresses", fail)
    assert dns_resolver.resolve_host() is None


def test_resolve_host_uses_doh_when_system_answer_is_dead(monkeypatch):
    monkeypatch.setattr(
        dns_resolver, "system_addresses", lambda host, port=443: ["9.9.9.9"]
    )
    monkeypatch.setattr(
        dns_resolver, "doh_addresses", lambda host, resolvers=None: ["8.8.8.8"]
    )
    monkeypatch.setattr(
        dns_resolver,
        "verify_tls",
        lambda host, address, port=443: address == "8.8.8.8",
    )
    assert dns_resolver.resolve_host() == "8.8.8.8"


def test_resolve_host_uses_doh_when_system_dns_fails_entirely(monkeypatch):
    monkeypatch.setattr(dns_resolver, "system_addresses", lambda host, port=443: [])
    monkeypatch.setattr(
        dns_resolver, "doh_addresses", lambda host, resolvers=None: ["8.8.8.8"]
    )
    monkeypatch.setattr(dns_resolver, "verify_tls", lambda *a, **k: True)
    assert dns_resolver.resolve_host() == "8.8.8.8"


def test_resolve_host_returns_none_when_nothing_verifies(monkeypatch):
    monkeypatch.setattr(
        dns_resolver, "system_addresses", lambda host, port=443: ["9.9.9.9"]
    )
    monkeypatch.setattr(
        dns_resolver, "doh_addresses", lambda host, resolvers=None: ["8.8.8.8"]
    )
    monkeypatch.setattr(dns_resolver, "verify_tls", lambda *a, **k: False)
    assert dns_resolver.resolve_host() is None


def script_verification(monkeypatch, outcomes):
    """Make each DoH verification attempt return the next scripted outcome.

    System DNS is stubbed as unresolvable so the only calls recorded are the
    DoH attempts themselves.
    """
    monkeypatch.setattr(
        dns_resolver, "system_addresses", lambda host, port=443: []
    )
    monkeypatch.setattr(
        dns_resolver, "doh_addresses", lambda host, resolvers=None: ["8.8.8.8"]
    )

    calls = []

    def fake_first_working_address(host, candidates, port=443):
        calls.append(list(candidates))
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(
        dns_resolver, "first_working_address", fake_first_working_address
    )
    return calls


def test_resolve_host_retries_when_first_attempt_fails(
    monkeypatch, no_retry_delay
):
    """A transient edge failure must not abandon the fallback."""
    calls = script_verification(monkeypatch, [None, "8.8.8.8"])

    assert dns_resolver.resolve_host() == "8.8.8.8"
    assert len(calls) == 2, "should verify once more after the first failure"
    assert no_retry_delay == [config.DNS_RETRY_DELAY]


def test_resolve_host_retries_until_attempts_exhausted(
    monkeypatch, no_retry_delay
):
    """All configured attempts are used, then it gives up cleanly."""
    attempts = config.DNS_VERIFY_ATTEMPTS
    assert attempts == 3
    calls = script_verification(monkeypatch, [None] * attempts)

    assert dns_resolver.resolve_host() is None
    assert len(calls) == attempts
    # No sleep after the final attempt.
    assert no_retry_delay == [config.DNS_RETRY_DELAY] * (attempts - 1)


def test_resolve_host_does_not_retry_after_first_success(
    monkeypatch, no_retry_delay
):
    """A healthy first attempt short-circuits; no extra query, no sleep."""
    calls = script_verification(monkeypatch, ["8.8.8.8"])

    assert dns_resolver.resolve_host() == "8.8.8.8"
    assert len(calls) == 1
    assert no_retry_delay == []


def test_resolve_host_rechecks_candidates_on_every_attempt(
    monkeypatch, no_retry_delay
):
    """Each attempt re-queries DoH rather than reusing a stale answer."""
    queries = []

    def counting_doh(host, resolvers=None):
        queries.append(host)
        return [f"10.0.0.{len(queries)}"]

    monkeypatch.setattr(
        dns_resolver, "system_addresses", lambda host, port=443: []
    )
    monkeypatch.setattr(dns_resolver, "doh_addresses", counting_doh)

    outcomes = [None, None, "10.0.0.3"]
    calls = []

    def fake_first_working_address(host, candidates, port=443):
        calls.append(list(candidates))
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(
        dns_resolver, "first_working_address", fake_first_working_address
    )

    assert dns_resolver.resolve_host() == "10.0.0.3"
    assert queries == ["coinmarketcap.com"] * 3
    assert calls == [["10.0.0.1"], ["10.0.0.2"], ["10.0.0.3"]]


def test_resolve_host_respects_the_config_switch(monkeypatch):
    monkeypatch.setattr(config, "DNS_FALLBACK_ENABLED", False)

    def fail(*args, **kwargs):
        raise AssertionError("must not resolve when the fallback is disabled")

    monkeypatch.setattr(dns_resolver, "system_addresses", fail)
    assert dns_resolver.resolve_host() is None