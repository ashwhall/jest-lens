# jest-lens

Jest prints tens of thousands of lines to say "93 passed". Pipe it through
`jest_lens.py` and you get the counts, plus the failure blocks up to a ~5KB
cap. The whole output is stored under a short run id, so the cap and the
console output Jest keeps out of its failure blocks are both one command away.
Written for driving Jest from an LLM agent, where that output competes with the
code for a context budget.

`2>&1` is required, because Jest reports to stderr.

```console
$ yarn jest 2>&1 | python3 jest_lens.py
RUN ID: a3f19c
FAIL  2 failed, 91 passed in 18.02 s

--- FAILURE 1: src/b.test.ts › uplift calculator › applies the 1.05 factor ---
    Expected: 105
    Received: 100

      at Object.<anonymous> (src/b.test.ts:22:19)

$ python3 jest_lens.py --console a3f19c        # console output, absent above
$ python3 jest_lens.py --all-failures a3f19c   # every block, ignoring the cap
$ python3 jest_lens.py --full-log a3f19c       # the whole run, passes included
$ python3 jest_lens.py --failed-paths a3f19c   # file paths of failing suites
```

Every recovery flag needs the run id the run printed. There is no "most
recent" alias, because one store holds the runs from every repo and the newest
is often from another one. `--list-runs` finds an id you have lost.
`--all-failures` and `--console` can be given together. `--full-log` is the
entire stream rather than just those two sections, carrying the per-suite
results, the summary, any coverage table and whatever the package manager
printed, so it refuses to be combined. Stdlib-only Python, nothing to
build.

## As a Claude Code plugin

```console
$ claude plugin marketplace add ashwhall/jest-lens
$ claude plugin install jest-lens@jest-lens
```

`--audit` reports what a run cost against reading its raw log, or the lifetime
totals when given no id.

The skill then fires on any request to run tests, and invokes the script from
`${CLAUDE_PLUGIN_ROOT}`. It costs ~80 tokens always-on and ~560 when it fires.
