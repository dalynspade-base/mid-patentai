"""Run current contracts and historical engineering tests as separate groups."""
import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def group_tests(suite):
    groups = {"current_engineering": [], "legacy_engineering": []}

    def visit(item):
        if isinstance(item, unittest.TestSuite):
            for child in item:
                visit(child)
        else:
            historical = item.id().startswith(("legacy.", "tests.legacy."))
            groups["legacy_engineering" if historical else "current_engineering"].append(item)

    visit(suite)
    return {key: unittest.TestSuite(values) for key, values in groups.items()}


def load_suite(current_only=False):
    # Selecting current-only must not import historical optional dependencies at discovery time.
    names = ["tests." + path.stem for path in sorted((ROOT / "tests").glob("test*.py"))]
    if not current_only:
        names += ["tests.legacy." + path.stem for path in sorted((ROOT / "tests/legacy").glob("test*.py"))]
    return unittest.defaultTestLoader.loadTestsFromNames(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="每次使用新目录")
    parser.add_argument("--current-only", action="store_true", help="仅执行当前业务工程/语义契约测试")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    groups = group_tests(load_suite(args.current_only))
    if args.current_only:
        groups.pop("legacy_engineering")
    try:
        checkout = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if Path(checkout).resolve() != ROOT.resolve():
            raise OSError("this directory is not its own Git checkout")
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    summary = {
        "scope": "engineering_contracts_not_model_semantic_accuracy", "groups": {},
        "python": sys.executable, "python_version": sys.version, "command": sys.argv,
        "commit": commit,
        "note": "工程断言与历史夹具分别列出；次数不是语义质量指标或独立专利样本数。",
    }
    for key, suite in groups.items():
        count = suite.countTestCases()
        with (args.output_dir / f"{key}.txt").open("x", encoding="utf-8") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        summary["groups"][key] = {
            "count": count, "passed": result.wasSuccessful(), "skipped": len(result.skipped),
            "failures": len(result.failures), "errors": len(result.errors),
        }
    summary["all_engineering_checks_passed"] = all(v["passed"] for v in summary["groups"].values())
    (args.output_dir / "verification.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_engineering_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
