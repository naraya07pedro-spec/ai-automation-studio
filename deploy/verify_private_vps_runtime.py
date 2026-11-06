"""Read-only live acceptance probe for an existing PRIVATE systemd installation.

Run ON THE VPS, never against a public URL. Prints no IP, username, token,
vault key, message metadata, payload, or URL path supplied by the operator.
No app state mutations, service restart or outgoing provider requests.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

PORT_HEX = format(8765, "04X")
URL = "http://127.0.0.1:8765"
UNIT = "ai-automation-studio.service"
LOOPBACK_HEX = "0100007F"


def parse_listening_addresses(contents, *, ipv6=False):
    """Return raw /proc/net/tcp{,6} listener hex IPs on exact port 8765."""
    matches = []
    for line in contents.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":
            continue
        try:
            address, port = parts[1].split(":")
        except ValueError:
            continue
        if (port.upper() == PORT_HEX and
                len(address) == (32 if ipv6 else 8) and
                all(ch in "0123456789ABCDEFabcdef" for ch in address)):
            matches.append(address.upper())
    return matches


def _listen_status():
    try:
        ipv4 = parse_listening_addresses(
            Path("/proc/net/tcp").read_text(encoding="ascii"))
        ipv6 = parse_listening_addresses(
            Path("/proc/net/tcp6").read_text(encoding="ascii"), ipv6=True)
    except OSError:
        return False, False
    # Server intentionally binds IPv4 loopback only. Any extra listener on
    # 8765 is a contract break, even if the private HTTP check responds.
    return bool(ipv4 or ipv6), ipv4 == [LOOPBACK_HEX] and not ipv6


def _http_checks():
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(URL + "/mission", timeout=3) as response:
            headers = response.headers
            landing = (response.status == 200
                       and headers.get("X-Frame-Options") == "DENY"
                       and "frame-ancestors 'none'" in
                       headers.get("Content-Security-Policy", ""))
    except (OSError, URLError, ValueError, TimeoutError):
        return False, False
    try:
        opener.open(Request(URL + "/api/local-schedules"), timeout=3)
        protected = False
    except HTTPError as error:
        protected = error.code == 401
    except (OSError, URLError, ValueError, TimeoutError):
        protected = False
    return landing, protected


def probe():
    if sys.platform != "linux" or not Path("/run/systemd/system").is_dir():
        return {"status": "NOT_VERIFIED", "reason": "linux_systemd_required"}
    try:
        service = subprocess.run(
            ["systemctl", "is-active", "--quiet", UNIT],
            timeout=4, capture_output=True, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        service = False
    listener, loopback = _listen_status()
    homepage, denied = _http_checks() if listener and loopback else (False, False)
    checks = {
        "systemd_active": service,
        "listener_present": listener,
        "loopback_only_ipv4_8765": loopback,
        "mission_login_page_http": homepage,
        "protected_api_denies_anonymous": denied,
    }
    passed = all(checks.values())
    return {
        "status": "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED" if passed else "NOT_VERIFIED",
        "checks": checks,
        "limitations": "Single-host snapshot only. No proof of reboot recovery, Tencent VM identity, user login, scheduler execution, Gmail consent or downstream n8n delivery.",
    }


def main():
    result = probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
