"""Run Ruff and strict mypy, failing on diagnostics added since the baseline."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
BASELINES = ROOT / ".github/python-quality"


class RuffLocation(TypedDict):
    row: int
    column: int


class RuffFinding(TypedDict):
    filename: str
    location: RuffLocation
    code: str
    message: str


class MypyFinding(TypedDict):
    file: str
    line: int
    column: int
    code: str
    severity: str
    message: str


@cache
def source_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    column: int
    code: str
    message: str

    def key(self) -> str:
        path = Path(self.file)
        if not path.is_absolute():
            path = ROOT / path
        relative = path.relative_to(ROOT)
        # A nearby edit can move the line without changing the finding.
        anchor = source_lines(path)[self.line - 1].strip() if self.line > 0 else ""
        message = " ".join(self.message.splitlines())
        return f"{relative.as_posix()} [{self.code}] {message} | {anchor}"

    def location(self) -> str:
        path = Path(self.file)
        if path.is_absolute():
            path = path.relative_to(ROOT)
        return f"{path.as_posix()}:{self.line}:{self.column}: {self.code} {self.message}"


def ruff_findings(output: str) -> Iterator[Finding]:
    findings: list[RuffFinding] = json.loads(output)
    for item in findings:
        yield Finding(
            item["filename"],
            item["location"]["row"],
            item["location"]["column"],
            item["code"],
            item["message"],
        )


def mypy_findings(output: str) -> Iterator[Finding]:
    for line in output.splitlines():
        item: MypyFinding = json.loads(line)
        if item["severity"] == "error":
            yield Finding(
                item["file"], item["line"], item["column"] + 1, item["code"], item["message"]
            )


def collect(tool: str, *args: str) -> list[Finding]:
    result = subprocess.run(
        [sys.executable, "-m", tool, *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode not in (0, 1) or (result.returncode == 1 and not result.stdout):
        raise RuntimeError(f"{tool} did not complete (exit {result.returncode})\n{result.stdout}")
    parse = ruff_findings if tool == "ruff" else mypy_findings
    return list(parse(result.stdout))


def check(tool: str, findings: list[Finding]) -> bool:
    path = BASELINES / f"{tool}.txt"
    baseline = Counter(path.read_text(encoding="utf-8").splitlines())
    new_findings = []
    for finding in findings:
        key = finding.key()
        if baseline[key] > 0:
            baseline[key] -= 1
        else:
            new_findings.append(finding)
    for finding in new_findings:
        print(finding.location(), file=sys.stderr)
    print(f"{tool}: {len(new_findings)} new, {len(findings) - len(new_findings)} existing")
    return not new_findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prune-baseline",
        action="store_true",
        help="Remove resolved diagnostics from the baseline",
    )
    args = parser.parse_args()
    ruff = collect("ruff", "check", "--output-format", "json")
    mypy = collect("mypy", "--no-incremental", "--output", "json")
    ruff_ok = check("ruff", ruff)
    mypy_ok = check("mypy", mypy)
    if not (ruff_ok and mypy_ok):
        return 1
    if args.prune_baseline:
        for tool, findings in (("ruff", ruff), ("mypy", mypy)):
            keys = sorted(finding.key() for finding in findings)
            (BASELINES / f"{tool}.txt").write_text(
                "".join(f"{key}\n" for key in keys), encoding="utf-8"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
