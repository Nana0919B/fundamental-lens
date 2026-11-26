"""Project-scoped Yahoo DNS and CA configuration; no system network changes."""

import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def _session(dns, ca_bundle):
    from curl_cffi import requests

    options = {"impersonate": "chrome"}
    if dns:
        options["dns"] = list(dns)
    if ca_bundle:
        options["verify"] = ca_bundle
    return requests.Session(**options)


def session_options():
    """Use one shared session when configured, else retain yfinance defaults.

    c-ares can use a stale VPN resolver from resolv.conf even when macOS's
    native resolver works. An explicit IP resolver changes only these requests;
    browser TLS impersonation, hostnames, SNI and certificate checks stay intact.
    """
    raw = os.getenv("TRADINGAGENTS_YAHOO_DNS", "").strip()
    dns = tuple(part for part in re.split(r"[,\s]+", raw) if part)
    for address in dns:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ValueError("TRADINGAGENTS_YAHOO_DNS must contain DNS server IP addresses") from None
    bundle = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    ca_bundle = str(Path(bundle).resolve()) if bundle and Path(bundle).is_file() else None
    if not dns and not ca_bundle:
        return {}
    return {"session": _session(dns, ca_bundle)}
