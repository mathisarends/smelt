"""Compare two Smelt import roots on one policy; optionally execute selected pytest targets."""

import argparse
import json
import sys
from pathlib import Path

from smelt.engine.evaluation import EvaluationOptions, evaluate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--baseline",
        required=True,
        type=Path,
        help="Directory containing the baseline smelt package (checkout or extracted wheel).",
    )
    parser.add_argument(
        "--candidate", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--reports", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--select",
        default="SMT42",
        help="Comma-separated rules; an empty string uses adopted policies.",
    )
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--source-revision")
    parser.add_argument("--pytest-target", action="append", default=[])
    parser.add_argument("--pytest-arg", action="append", default=[])
    parser.add_argument("--test-python", type=Path)
    parser.add_argument("--pythonpath", action="append", type=Path, default=[])
    args = parser.parse_args()
    try:
        result = evaluate(
            EvaluationOptions(
                config=args.config,
                baseline=args.baseline,
                candidate=args.candidate,
                reports=args.reports,
                python=args.python,
                select=tuple(value for value in args.select.split(",") if value),
                timeout=args.timeout,
                pytest_targets=tuple(args.pytest_target),
                pytest_args=tuple(args.pytest_arg),
                test_python=args.test_python,
                python_paths=tuple(args.pythonpath),
                source_revision=args.source_revision,
            )
        )
    except (OSError, ValueError) as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "error",
                    "exit_code": 2,
                    "error": {"kind": "input", "message": str(exc)},
                }
            )
            + "\n"
        )
        return 2
    sys.stdout.write(
        f"{result['status']}: {args.reports.resolve() / 'evaluation.json'}\n"
    )
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
