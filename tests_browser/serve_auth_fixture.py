"""Browser CI fixture: authenticated loopback server with TEST-ONLY owner.

Production app/local_server.py never accepts an ephemeral/default password:
it requires offline one-time owner provisioning.
"""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth
from local_source_policy import LocalPublicSourcePolicy
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler


def main():
    home = tempfile.TemporaryDirectory()
    owner_path = Path(home.name) / "operator.json"
    LocalOperatorAuth.provision(owner_path, "ci_only_fixture_password_1234")
    owner = LocalOperatorAuth.load(owner_path)
    policy = LocalPublicSourcePolicy()
    # Only the test fixture grants a sample public repository on startup.
    # Shipping server starts with no grants.
    policy.grant("Octocat/Hello-World")
    audit = LocalSecurityAudit(Path(home.name) / "security-audit.sqlite3")
    core = LocalWorkflowCore(":memory:")
    def fixture_local_agent(prompt, model):
        return {"mode": "LOCAL_MODEL", "answer": "Fixture answer: " + str(len(prompt)),
                "tools_used": ["calculate"], "external_dispatch_count": 0}

    server = ThreadingHTTPServer(
        ("127.0.0.1", 8765),
        create_handler(local_core=core, operator_auth=owner,
                       source_policy=policy, security_audit=audit,
                       local_agent_runner=fixture_local_agent))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.close()
        audit.close()
        home.cleanup()


if __name__ == "__main__":
    main()
