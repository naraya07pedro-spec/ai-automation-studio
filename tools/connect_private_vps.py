"""Windows/Linux secure SSH-tunnel client to a private VPS workbench.

Do not embed passwords or keys; never bypass host key verification.
"""
import argparse
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

PORT = 8765
URL = "http://127.0.0.1:8765"
_TARGET = re.compile(
    r"[a-z_][a-z0-9_-]{0,31}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?",
    re.ASCII,
)


def valid_destination(destination):
    if not isinstance(destination, str) or not _TARGET.fullmatch(destination):
        return False
    hostname = destination.split("@", 1)[1]
    return (".." not in hostname and ".-" not in hostname and "-." not in hostname
            and not hostname.startswith("-") and not hostname.endswith("-"))


def ssh_args(destination, ssh="ssh"):
    if not valid_destination(destination):
        raise ValueError("invalid_ssh_destination")
    return [
        ssh, "-N", "-T", "-L", "127.0.0.1:8765:127.0.0.1:8765",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=ask",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        destination,
    ]


def local_port_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", PORT))
        except OSError:
            return False
    return True


def app_responding():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL, timeout=0.6) as r:
            return r.status == 200 and r.headers.get("X-Frame-Options") == "DENY"
    except urllib.error.HTTPError as exc:
        return (exc.code in (401, 403)
                and exc.headers.get("X-Frame-Options") == "DENY")
    except (OSError, ValueError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Connect to your private VPS Studio over SSH.")
    parser.add_argument("--destination", help="Existing SSH username@public-IP-or-hostname")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    target = args.destination
    if target is None:
        try:
            target = input("VPS SSH destination (username@public-IP): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 2
    if not valid_destination(target):
        print("Blocked: enter only username@host or username@IPv4; no SSH options.", file=sys.stderr)
        return 2
    if not local_port_free():
        print("Blocked: local port 8765 is already used. Close the local Studio first.",
              file=sys.stderr)
        return 2
    ssh = shutil.which("ssh")
    if not ssh:
        print("Blocked: install/enable OpenSSH client before connecting.", file=sys.stderr)
        return 2
    print("SSH host key confirmation must match the VPS provider console.")
    print("Keep this terminal open; Ctrl+C disconnects. No public app HTTP port is needed.")
    proc = subprocess.Popen(ssh_args(target, ssh=ssh))
    try:
        while proc.poll() is None:
            if app_responding():
                print("PRIVATE_VPS_TUNNEL_READY: " + URL)
                if not args.no_browser:
                    webbrowser.open(URL)
                return proc.wait()
            time.sleep(0.25)
        print("SSH ended before the private app became reachable. "
              "Check VPS service, SSH permissions and address.",
              file=sys.stderr)
        return proc.returncode or 2
    except KeyboardInterrupt:
        print("Disconnecting SSH tunnel.")
        return 130
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
