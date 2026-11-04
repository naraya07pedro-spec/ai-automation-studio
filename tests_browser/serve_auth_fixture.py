"""Browser CI fixture: authenticated loopback server with TEST-ONLY owner.

Production app/local_server.py never accepts an ephemeral/default password:
it requires offline one-time owner provisioning.
"""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler


def main():
    owner = LocalOperatorAuth.ephemeral_for_tests("ci_only_fixture_password_1234")
    core = LocalWorkflowCore(":memory:")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 8765),
        create_handler(local_core=core, operator_auth=owner))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.close()


if __name__ == "__main__":
    main()
