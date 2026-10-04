"""Resolves the target host, working around incorrect system DNS answers.

Some networks (captive portals, DNS filtering) answer queries for the target
host with an unrelated address that accepts no connections. The system answer
is therefore probed first; only when it fails is the host re-resolved over
DNS-over-HTTPS and each candidate checked with a real TLS handshake.

Nothing here changes Windows DNS, the hosts file, or proxy settings: the result
is handed to Chrome as a --host-resolver-rules argument for a single host.
"""

import json
import logging
import socket
import ssl
import time
import urllib.parse
import urllib.request
from typing import List, Optional

from . import config

logger = logging.getLogger(__name__)


def system_addresses(host: str, port: int = 443) -> List[str]:
    """Addresses the operating system resolver returns for ``host``."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        logger.debug("System DNS lookup for %s failed: %s", host, exc)
        return []

    addresses: List[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def doh_addresses(
    host: str, resolvers: Optional[tuple] = None
) -> List[str]:
    """Resolve ``host`` over DNS-over-HTTPS using every configured resolver.

    All resolvers are queried and their answers combined instead of stopping at
    the first one that replies. A provider that answers must not hide the
    addresses a second provider would supply if its own edges all fail the TLS
    check. Results keep resolver order and contain no duplicates.
    """
    combined: List[str] = []

    for resolver in resolvers or config.DOH_RESOLVERS:
        url = f"{resolver}?{urllib.parse.urlencode({'name': host, 'type': 'A'})}"
        request = urllib.request.Request(
            url, headers={"Accept": "application/dns-json"}
        )
        try:
            with urllib.request.urlopen(
                request, timeout=config.DNS_RESOLVE_TIMEOUT
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            logger.debug("DoH resolver %s failed: %s", resolver, exc)
            continue

        addresses = [
            str(answer.get("data"))
            for answer in payload.get("Answer", [])
            if answer.get("type") == 1 and answer.get("data")
        ]
        if addresses:
            logger.debug("DoH resolver %s returned %s", resolver, addresses)
            combined.extend(addresses)

    # dict.fromkeys de-duplicates while preserving insertion order.
    return list(dict.fromkeys(combined))


def verify_tls(host: str, address: str, port: int = 443) -> bool:
    """Confirm ``address`` serves a valid certificate for ``host``.

    This is what keeps TLS honest: an address is only accepted if the
    certificate chain validates against the real hostname, so a hijacked or
    merely reachable address cannot be used.
    """
    try:
        context = ssl.create_default_context()
        with socket.create_connection(
            (address, port), timeout=config.DNS_RESOLVE_TIMEOUT
        ) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return tls.getpeercert() is not None
    except (OSError, ssl.SSLError) as exc:
        logger.debug("TLS check failed for %s (%s): %s", host, address, exc)
        return False


def first_working_address(
    host: str, candidates: List[str], port: int = 443
) -> Optional[str]:
    """First candidate that both connects and presents a valid certificate."""
    for address in candidates:
        if verify_tls(host, address, port):
            logger.info("Using %s for %s", address, host)
            return address
        logger.debug("Rejected address %s for %s", address, host)
    return None


def resolve_host(host: Optional[str] = None) -> Optional[str]:
    """Return a usable IP for ``host``, or None to let Chrome resolve it.

    The system resolver is tried first so that healthy networks are untouched.
    Only if its addresses cannot be verified does the DoH path run.
    """
    host = host or config.TARGET_HOST

    if not config.DNS_FALLBACK_ENABLED:
        return None

    system = system_addresses(host)
    if system:
        working = first_working_address(host, system)
        if working:
            logger.info("System DNS for %s is healthy (%s)", host, working)
            return None
        logger.warning(
            "System DNS returned %s for %s, but none of them accept a valid "
            "TLS connection; falling back to DNS-over-HTTPS.",
            ", ".join(system),
            host,
        )
    else:
        logger.warning("System DNS could not resolve %s.", host)

    for attempt in range(1, config.DNS_VERIFY_ATTEMPTS + 1):
        fallback = first_working_address(host, doh_addresses(host))
        if fallback:
            return fallback

        if attempt < config.DNS_VERIFY_ATTEMPTS:
            logger.debug(
                "DoH verification attempt %s of %s found no usable address; "
                "retrying in %ss",
                attempt,
                config.DNS_VERIFY_ATTEMPTS,
                config.DNS_RETRY_DELAY,
            )
            time.sleep(config.DNS_RETRY_DELAY)

    logger.error(
        "No address for %s passed verification via DNS-over-HTTPS after %s "
        "attempts.",
        host,
        config.DNS_VERIFY_ATTEMPTS,
    )
    return None