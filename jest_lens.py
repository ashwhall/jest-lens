#!/usr/bin/env python3
"""jest-lens: turn a Jest run's output into a short report, and keep the full
log recoverable.

Jest prints tens of thousands of lines to say "93 passed". Pipe it through this
and you get the counts plus the failure blocks, capped. The complete output is
stored under a short run id, so anything the report leaves out (console.log
lines, in particular, which never appear in a failure block) is one command
away.

    yarn jest --someArgs 2>&1 | jest_lens.py
    jest_lens.py --logs last | grep 'special value'

The tool never runs Jest. Choosing the runner, the node version and the flags
stays with the caller, who knows the repo.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

CACHE_DIR = Path(os.environ.get("JEST_LENS_DIR") or (Path.home() / ".cache" / "jest-lens"))
KEEP_RUNS = 20

BLOCK_MAX_LINES = 60
REPORT_MAX_BYTES = 5000
TAIL_LINES = 40

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
SUMMARY = re.compile(r"^(Test Suites|Tests|Snapshots|Time|Ran all test suites)\b")
BULLET = re.compile(r"^\s*●\s*(.*)$")
SUITE_LINE = re.compile(r"^(PASS|FAIL)\s+(\S.*?)(?:\s+\([\d.]+\s*m?s\))?$")
COUNT = re.compile(r"(\d+)\s+(failed|passed|skipped|todo|pending)")
NO_TESTS = re.compile(r"^No tests found")
# A failure block runs to the next heading or the summary, but a coverage table
# sits between the two and would otherwise be swallowed into the last block.
BLOCK_END = re.compile(r"^-{5,}\|-|^={5,}$")
# The first of these in a crash is usually the sentence worth reading. A blind
# tail lands in a stack trace instead.
ERROR_ANCHOR = re.compile(
    r"Cannot find module|SyntaxError|TypeError|ReferenceError|error TS\d+"
    r"|Validation Error|Unhandled '?error|^\s*Error:|NODE_MODULE_VERSION"
)
# Any of these means the stream held a real failure, not a lost stderr.
ERROR_SHAPED = re.compile(r"Error|error|Cannot find|●|^\s+at ")

# Headings Jest prints as a `●` bullet that are not a test failure.
NON_FAILURE_BULLETS = ("Console", "Deprecation Warning", "Validation Warning")

# Warnings that mean a handle outlived the suite. A leak here is the difference
# between a slow run and one that hangs, so it is worth pulling into the report.
LEAK_WARNINGS = (
    "Jest did not exit one second after",
    "A worker process has failed to exit",
    "Force exiting Jest",
)


# --- storage ----------------------------------------------------------------


def new_run_id() -> str:
    while True:
        run_id = secrets.token_hex(3)
        if not log_path(run_id).exists():
            return run_id


def log_path(run_id: str) -> Path:
    return CACHE_DIR / f"{run_id}.log"


def meta_path(run_id: str) -> Path:
    return CACHE_DIR / f"{run_id}.json"


def stored_runs() -> list[Path]:
    return sorted(CACHE_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def resolve(run_id: str | None) -> str:
    """Map a run id, an unambiguous prefix, or `last`, to a stored run."""
    runs = stored_runs()
    if not runs:
        sys.exit("jest-lens: no stored runs")
    if run_id in (None, "last", "-1"):
        return runs[0].stem
    if log_path(run_id).exists():
        return run_id
    matches = [p.stem for p in runs if p.stem.startswith(run_id)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        sys.exit(f"jest-lens: `{run_id}` matches {', '.join(matches)}")
    sys.exit(f"jest-lens: no run `{run_id}` (have {', '.join(p.stem for p in runs[:5])})")


def prune() -> None:
    for stale in stored_runs()[KEEP_RUNS:]:
        stale.unlink(missing_ok=True)
        meta_path(stale.stem).unlink(missing_ok=True)


# --- parsing ----------------------------------------------------------------


def clean(line: str) -> str:
    """Strip ANSI and resolve carriage-return overwrites, so the stored log greps."""
    line = line.rstrip("\n")
    if "\r" in line:
        segments = [s for s in line.split("\r") if s.strip()]
        line = segments[-1] if segments else ""
    return ANSI.sub("", line)


class Parsed:
    def __init__(self) -> None:
        self.summary: list[str] = []
        self.blocks: list[tuple[str, list[str]]] = []
        self.counts: dict[str, int] = {}
        self.duration = ""
        self.snapshots_failed = 0
        self.failed_suites: list[str] = []
        self.leaks: list[str] = []
        self.no_tests = False
        self.error_shaped = False
        self.lines = 0
        self.saw_suite_line = False


def parse(lines) -> Parsed:
    """Consume the stream, echoing suite progress to stderr as it arrives.

    Passes echo as a single dot: this output lands in a calling agent's context,
    where one line per suite would cost more than the report it accompanies.
    """
    out = Parsed()
    block: list[str] | None = None
    title = ""
    suite_path = ""
    dots = 0

    for raw in lines:
        line = clean(raw)
        out.lines += 1

        suite = SUITE_LINE.match(line)
        if suite:
            out.saw_suite_line = True
            # Jest prints a suite's `●` blocks directly under its result line,
            # so this both closes the previous block and names the next ones.
            if block is not None:
                out.blocks.append((title, block))
                block = None
            suite_path = suite.group(2)
            if suite.group(1) == "FAIL":
                out.failed_suites.append(suite.group(2))
                if dots:
                    print(file=sys.stderr, flush=True)
                    dots = 0
                print(line, file=sys.stderr, flush=True)
            else:
                print(".", end="", file=sys.stderr, flush=True)
                dots += 1

        if any(w in line for w in LEAK_WARNINGS):
            out.leaks.append(line.strip())

        if NO_TESTS.match(line):
            out.no_tests = True
        if not out.error_shaped and ERROR_SHAPED.search(line):
            out.error_shaped = True

        bullet = BULLET.match(line)
        if bullet:
            if block is not None:
                out.blocks.append((title, block))
                block = None
            heading = bullet.group(1).strip()
            title = f"{suite_path} › {heading}" if suite_path else heading
            if not heading.startswith(NON_FAILURE_BULLETS):
                block = []
        elif SUMMARY.match(line):
            if block is not None:
                out.blocks.append((title, block))
                block = None
            out.summary.append(line)
            if line.startswith("Tests:"):
                for value, kind in COUNT.findall(line):
                    out.counts[kind] = out.counts.get(kind, 0) + int(value)
            elif line.startswith("Snapshots:"):
                for value, kind in COUNT.findall(line):
                    if kind == "failed":
                        out.snapshots_failed += int(value)
            elif line.startswith("Time:"):
                out.duration = line.split(":", 1)[1].strip()
        elif block is not None and BLOCK_END.match(line):
            out.blocks.append((title, block))
            block = None
        elif block is not None and len(block) < BLOCK_MAX_LINES:
            if line.strip() or block:
                block.append(line)

    if dots:
        print(file=sys.stderr, flush=True)
    if block is not None:
        out.blocks.append((title, block))
    return out


def tee(stream, sink):
    for raw in stream:
        sink.write(clean(raw) + "\n")
        yield raw


# --- reporting --------------------------------------------------------------


def ambient_node() -> str:
    """The node on PATH now. Not necessarily the one Jest ran under, which the
    output never states, so the report labels it as ambient."""
    node = shutil.which("node")
    if not node:
        return "not on PATH"
    try:
        return subprocess.run([node, "-v"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def crash_excerpt(log: Path) -> str:
    """Window the log around its first error line, falling back to the tail."""
    lines = log.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        if ERROR_ANCHOR.search(line):
            start = max(0, index - 2)
            return "\n".join(lines[start:index + 30])
    return "\n".join(lines[-TAIL_LINES:])


def report(run_id: str, parsed: Parsed, log: Path) -> tuple[int, str]:
    hint = f"Recover: jest_lens.py --logs {run_id}   (also --failures, --console, --failed-paths)"

    if not parsed.summary:
        if parsed.no_tests:
            print("RESULT: no tests matched")
            print()
            print("\n".join(log.read_text(errors="replace").splitlines()[-TAIL_LINES:]))
            print()
            print(hint)
            return 1, "no-tests"

        if not parsed.saw_suite_line and not parsed.error_shaped and parsed.lines < 20:
            print(
                "jest-lens: no Jest output in the stream. Jest reports to stderr,\n"
                "so the pipeline needs `2>&1`:  yarn jest 2>&1 | jest_lens.py",
                file=sys.stderr,
            )
        print("RESULT: pre-test error (Jest printed no summary)")
        print(f"AMBIENT NODE: {ambient_node()}")
        print()
        if parsed.blocks:
            title, body = parsed.blocks[0]
            print(f"● {title}")
            print("\n".join(body))
        else:
            print(crash_excerpt(log))
        print()
        print(hint)
        return 1, "pre-test error"

    failed = parsed.counts.get("failed", 0)
    parts = [f"{n} {kind}" for kind, n in parsed.counts.items() if n]
    headline = ", ".join(parts) or "no tests"
    if parsed.duration:
        headline += f" in {parsed.duration}"

    passed = not failed and not parsed.failed_suites
    print(f"{'PASS' if passed else 'FAIL'}  {headline}")

    if parsed.snapshots_failed:
        print(f"SNAPSHOTS: {parsed.snapshots_failed} failed (rerun with -u only if the change is intended)")
    for leak in dict.fromkeys(parsed.leaks):
        print(f"LEAK: {leak}")
    print()

    if passed:
        print(hint)
        return 0, "pass"

    shown = 0
    written = 0
    for title, body in parsed.blocks:
        if written >= REPORT_MAX_BYTES:
            break
        shown += 1
        print(f"--- FAILURE {shown}: {title} ---")
        text = "\n".join(body)
        print(text)
        print()
        written += len(text)

    omitted = len(parsed.blocks) - shown
    if omitted > 0:
        print(f"[TRUNCATED: {omitted} further failure(s). Full blocks: jest_lens.py --failures {run_id}]")
        print()

    for line in parsed.summary:
        if line.startswith(("Test Suites:", "Tests:")):
            print(line)
    print()
    print(hint)
    return 1, "fail"


# --- recovery ---------------------------------------------------------------


def dump_logs(run_id: str) -> None:
    sys.stdout.write(log_path(run_id).read_text(errors="replace"))


def dump_sections(run_id: str, want_console: bool) -> None:
    """Reparse a stored log and print every failure block, or every console
    block, untruncated."""
    keep = False
    for line in log_path(run_id).read_text(errors="replace").splitlines():
        bullet = BULLET.match(line)
        if bullet:
            title = bullet.group(1).strip()
            keep = title.startswith("Console") if want_console else not title.startswith(NON_FAILURE_BULLETS)
            if keep:
                print()
                print(line)
            continue
        if SUMMARY.match(line):
            keep = False
        if keep:
            print(line)


def dump_failed_paths(run_id: str) -> None:
    """Print the failing suite paths, ready to paste back into a Jest command."""
    paths = []
    for line in log_path(run_id).read_text(errors="replace").splitlines():
        suite = SUITE_LINE.match(line)
        if suite and suite.group(1) == "FAIL":
            paths.append(suite.group(2))
    print(" ".join(dict.fromkeys(paths)))


def list_runs() -> None:
    runs = stored_runs()
    if not runs:
        sys.exit("jest-lens: no stored runs")
    for path in runs:
        meta = {}
        if meta_path(path.stem).exists():
            try:
                meta = json.loads(meta_path(path.stem).read_text())
            except ValueError:
                pass
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        size = path.stat().st_size // 1024
        note = meta.get("label") or meta.get("cwd", "")
        print(f"{path.stem}  {when}  {size:>6}K  {meta.get('result', '?'):<14} {note}")


# --- entry point ------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="jest_lens.py",
        description="Summarise piped Jest output; recover the full log by run id.",
        epilog="Jest reports to stderr, so pipe with 2>&1:  yarn jest 2>&1 | jest_lens.py",
    )
    ap.add_argument("--logs", nargs="?", const="last", metavar="ID", help="print a stored run's full output")
    ap.add_argument("--failures", nargs="?", const="last", metavar="ID", help="print every failure block, untruncated")
    ap.add_argument("--console", nargs="?", const="last", metavar="ID", help="print the console output Jest captured")
    ap.add_argument("--failed-paths", nargs="?", const="last", metavar="ID",
                    help="print the failing suite paths, for pasting into a rerun")
    ap.add_argument("--runs", action="store_true", help="list stored runs, newest first")
    ap.add_argument("--label", metavar="TEXT", help="note stored with the run, shown by --runs")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.runs:
        list_runs()
        return 0
    for flag, action in (
        (args.logs, lambda i: dump_logs(i)),
        (args.failures, lambda i: dump_sections(i, want_console=False)),
        (args.console, lambda i: dump_sections(i, want_console=True)),
        (args.failed_paths, lambda i: dump_failed_paths(i)),
    ):
        if flag:
            action(resolve(flag))
            return 0

    if sys.stdin.isatty():
        ap.print_help()
        return 2

    # One undecodable byte anywhere in a test's output would otherwise abort the
    # run and lose the report.
    sys.stdin.reconfigure(errors="replace")

    run_id = new_run_id()
    log = log_path(run_id)
    print(f"RUN ID: {run_id}", flush=True)

    def on_sigint(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)

    with log.open("w", buffering=1, errors="replace") as sink:
        try:
            parsed = parse(tee(sys.stdin, sink))
        except KeyboardInterrupt:
            # The partial log is already on disk and the id is already printed.
            print("\njest-lens: interrupted; partial log kept.", file=sys.stderr)
            print(f"Recover: jest_lens.py --logs {run_id}")
            return 130

    code, result = report(run_id, parsed, log)
    meta_path(run_id).write_text(
        json.dumps(
            {
                "cwd": os.getcwd(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "result": result,
                "counts": parsed.counts,
                "label": args.label or "",
            }
        )
    )
    prune()
    return code


if __name__ == "__main__":
    sys.exit(main())
