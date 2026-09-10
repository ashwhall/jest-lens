#!/usr/bin/env python3
"""jest-lens: turn a Jest run's output into a short report, and keep the full
log recoverable.

Jest prints tens of thousands of lines to say "93 passed". Pipe it through this
and you get the counts plus the failure blocks, capped. The complete output is
stored under a short run id, so anything the report leaves out (console.log
lines, in particular, which never appear in a failure block) is one command
away.

    yarn jest --someArgs 2>&1 | jest_lens.py
    jest_lens.py --console a3f19c

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


def restrict(path: Path) -> None:
    """Keep a stored run to this user.

    Test output carries whatever the suite logged, which can include tokens
    pulled from the environment, so the default umask is too generous. Best
    effort: a filesystem that cannot represent the mode is not worth failing a
    test run over.
    """
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


def log_path(run_id: str) -> Path:
    return CACHE_DIR / f"{run_id}.log"


def meta_path(run_id: str) -> Path:
    return CACHE_DIR / f"{run_id}.json"


def stored_runs() -> list[Path]:
    return sorted(CACHE_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def resolve(run_id: str) -> str:
    """Map a run id, or an unambiguous prefix of one, to a stored run.

    There is deliberately no "most recent" alias: one store holds the runs from
    every repo, so the newest is often from somewhere else entirely.
    """
    runs = stored_runs()
    if not runs:
        sys.exit("jest-lens: no stored runs")
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
        # (title, body, is_console) — console blocks are Jest's captured
        # output, kept apart from failures but bounded the same way.
        self.blocks: list[tuple[str, list[str], bool]] = []
        self.counts: dict[str, int] = {}
        self.duration = ""
        self.snapshots_failed = 0
        self.failed_suites: list[str] = []
        self.leaks: list[str] = []
        self.no_tests = False
        self.error_shaped = False
        self.lines = 0
        self.saw_suite_line = False


def parse(lines, echo: bool = True, block_max_lines: int | None = BLOCK_MAX_LINES) -> Parsed:
    """Consume the stream, optionally echoing suite progress to stderr.

    Passes echo as a single dot: that output lands in a calling agent's
    context, where one line per suite would cost more than the report it
    accompanies. Re-reading a stored run passes `echo=False`, and no block cap.
    """
    out = Parsed()
    block: list[str] | None = None
    title = ""
    suite_path = ""
    is_console = False
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
                out.blocks.append((title, block, is_console))
                block = None
            suite_path = suite.group(2)
            if suite.group(1) == "FAIL":
                out.failed_suites.append(suite.group(2))
                if echo and dots:
                    print(file=sys.stderr, flush=True)
                    dots = 0
                if echo:
                    print(line, file=sys.stderr, flush=True)
            elif echo:
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
                out.blocks.append((title, block, is_console))
            heading = bullet.group(1).strip()
            title = f"{suite_path} › {heading}" if suite_path else heading
            is_console = heading.startswith("Console")
            # Other non-failure bullets are warnings with nothing to report.
            block = [] if is_console or not heading.startswith(NON_FAILURE_BULLETS) else None
        elif SUMMARY.match(line):
            if block is not None:
                out.blocks.append((title, block, is_console))
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
            out.blocks.append((title, block, is_console))
            block = None
        elif block is not None and (block_max_lines is None or len(block) < block_max_lines):
            if line.strip() or block:
                block.append(line)

    if echo and dots:
        print(file=sys.stderr, flush=True)
    if block is not None:
        out.blocks.append((title, block, is_console))
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
    hint = (f"Recover: jest_lens.py --full-log {run_id}"
            "   (also --all-failures, --console, --failed-paths)")

    if not parsed.summary:
        if parsed.no_tests:
            print("RESULT: no tests matched")
            print()
            print("\n".join(log.read_text(errors="replace").splitlines()[-TAIL_LINES:]))
            print()
            print(hint)
            return 1, "no-tests"

        if not parsed.saw_suite_line and not parsed.error_shaped:
            print("RESULT: no Jest output in the stream")
            print()
            if parsed.lines < 20:
                print("Jest reports to stderr, so the pipeline needs `2>&1`:")
                print("  yarn jest 2>&1 | jest_lens.py")
                print()
            print("\n".join(log.read_text(errors="replace").splitlines()[-TAIL_LINES:]))
            print()
            print(hint)
            return 2, "unparseable"

        print("RESULT: pre-test error (Jest printed no summary)")
        print(f"AMBIENT NODE: {ambient_node()}")
        print()
        failures = [(t, b) for t, b, console in parsed.blocks if not console]
        if failures:
            title, body = failures[0]
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

    failures = [(t, b) for t, b, console in parsed.blocks if not console]
    shown = 0
    written = 0
    for title, body in failures:
        if written >= REPORT_MAX_BYTES:
            break
        shown += 1
        print(f"--- FAILURE {shown}: {title} ---")
        text = "\n".join(body)
        print(text)
        print()
        written += len(text)

    omitted = len(failures) - shown
    if omitted > 0:
        print(f"[TRUNCATED: {omitted} more. All blocks: jest_lens.py --all-failures {run_id}]")
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
    """Print a stored run's failure blocks, or its console blocks, uncapped.

    Goes through `parse` rather than rescanning, so the block boundaries and
    the suite-qualified headings match what the report showed.
    """
    with log_path(run_id).open(errors="replace") as log:
        parsed = parse(log, echo=False, block_max_lines=None)
    for title, body, is_console in parsed.blocks:
        if is_console != want_console:
            continue
        print(f"● {title}")
        print("\n".join(body))
        print()


def dump_failed_paths(run_id: str) -> None:
    """Print the paths of suites that failed, space separated.

    Suite files, not test names: re-running these covers their passing tests
    too, and nothing here feeds Jest's `-t`.
    """
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
        print(f"{path.stem}  {when}  {size:>6}K  "
              f"{meta.get('result', '?'):<14} {meta.get('cwd', '')}")


# --- entry point ------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="jest_lens.py",
        allow_abbrev=False,
        description="Summarise piped Jest output; recover a stored run by id.",
        epilog="Jest reports to stderr, so pipe with 2>&1:  yarn jest 2>&1 | jest_lens.py",
    )
    ap.add_argument("run", nargs="?", metavar="ID",
                    help="stored run to read, as printed by the run itself")
    ap.add_argument("--all-failures", action="store_true",
                    help="every failure block, ignoring the report cap")
    ap.add_argument("--console", action="store_true",
                    help="the console output, which no failure block carries")
    ap.add_argument("--full-log", action="store_true",
                    help="the entire stream, not only the sections above")
    ap.add_argument("--failed-paths", action="store_true",
                    help="file paths of failing suites, on one line")
    ap.add_argument("--list-runs", action="store_true", help="list stored runs, newest first")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    restrict(CACHE_DIR)

    if args.list_runs:
        list_runs()
        return 0

    # --all-failures and --console are sections of one run and read well together.
    # The other two are not: --full-log already contains both sections, and
    # --failed-paths is consumed by a shell, so prose alongside it would break
    # the caller.
    sections = [n for n, on in (("--all-failures", args.all_failures),
                                ("--console", args.console)) if on]
    whole = [n for n, on in (("--full-log", args.full_log),
                             ("--failed-paths", args.failed_paths)) if on]

    if whole and (sections or len(whole) > 1):
        ap.error(f"{whole[0]} cannot be combined with {(sections + whole[1:])[0]}")

    if whole or sections:
        if args.run is None:
            ap.error(f"an ID is required with {(whole + sections)[0]}"
                     " (--list-runs to find one)")
        run_id = resolve(args.run)
        if args.full_log:
            dump_logs(run_id)
        elif args.failed_paths:
            dump_failed_paths(run_id)
        else:
            if args.all_failures:
                dump_sections(run_id, want_console=False)
            if args.console:
                dump_sections(run_id, want_console=True)
        return 0

    if sys.stdin.isatty():
        ap.print_help()
        return 2

    if args.run is not None:
        ap.error("an ID is only meaningful with an output flag")

    # One undecodable byte anywhere in a test's output would otherwise abort the
    # run and lose the report.
    sys.stdin.reconfigure(errors="replace")

    run_id = new_run_id()
    log = log_path(run_id)
    print(f"RUN ID: {run_id}", flush=True)

    def on_sigint(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)

    log.touch()
    restrict(log)
    with log.open("w", buffering=1, errors="replace") as sink:
        try:
            parsed = parse(tee(sys.stdin, sink))
        except KeyboardInterrupt:
            # The partial log is already on disk and the id is already printed.
            print("\njest-lens: interrupted; partial log kept.", file=sys.stderr)
            print(f"Recover: jest_lens.py --full-log {run_id}")
            return 130

    code, result = report(run_id, parsed, log)
    meta_path(run_id).write_text(
        json.dumps(
            {
                "cwd": os.getcwd(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "result": result,
                "counts": parsed.counts,
            }
        )
    )
    restrict(meta_path(run_id))
    prune()
    return code


if __name__ == "__main__":
    sys.exit(main())
