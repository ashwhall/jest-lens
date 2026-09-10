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

$ python3 jest_lens.py --console   # console output; Jest omits it from failures
$ python3 jest_lens.py --failures  # all failure blocks, ignoring the ~5KB cap
$ python3 jest_lens.py --logs      # the whole run, passes included
$ python3 jest_lens.py --failed-paths   # paths of failing suites, not test names
```

The id defaults to the last run. Stdlib-only Python, nothing to build.

## As a Claude Code plugin

```console
$ claude plugin marketplace add ashwhall/jest-lens
$ claude plugin install jest-lens@jest-lens
```

The skill then fires on any request to run tests, and invokes the script from
`$CLAUDE_PLUGIN_ROOT`. It costs ~105 tokens always-on and ~590 when it fires.
