"""CI entrypoint: reject pinned synthetic fixture drift without repinning."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype"))

from lead_inquiry_runtime_drift import check_runtime_drift


def main():
    report = check_runtime_drift()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "RUNTIME_FIXTURE_VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
