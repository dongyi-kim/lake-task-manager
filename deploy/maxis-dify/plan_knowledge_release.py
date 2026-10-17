"""Read local release candidates and print a content-free blue/green dry-run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.maxis.knowledge_release import build_candidate, historical_snapshot, plan_blue_green


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("invalid_arguments")


def main(argv=None) -> int:
    parser = _Parser(description="Offline Knowledge release review; never uploads or activates.")
    parser.add_argument("--current", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--source-root", required=True)
    try:
        args = parser.parse_args(argv)
        current = historical_snapshot(args.current, args.source_root)
        candidate = build_candidate(args.candidate, args.source_root)
        result = plan_blue_green(current, candidate)
    except Exception:
        # File paths, validation input and document values are not diagnostics.
        print(json.dumps({"dryRun": True, "activationAllowed": False,
                          "error": "knowledge_release_review_failed"}))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
