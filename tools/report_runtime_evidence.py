"""Print measured mock runtime fixture and historical registry status, no secrets."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))
from lead_inquiry_runtime_drift import check_runtime_drift
from lead_inquiry_runtime_evidence import classify_runtime_evidence


def main():
    current = check_runtime_drift()
    report = classify_runtime_evidence(current)
    print(json.dumps(report, sort_keys=True))
    # Current drift failure is always blocking; historical registration alone
    # is not a valid reason to approve failed current fixture verification.
    return 0 if current["status"] == "RUNTIME_FIXTURE_VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
