from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    result = {
        "summary": f"Fake agent completed {task['id']}.",
        "artifacts": [],
        "criteria_checked": [criterion["id"] for criterion in task["acceptance_criteria"]],
        "needs_consultation": False,
        "consultation_question": ""
    }
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

