# jest-lens

Jest prints tens of thousands of lines to say "93 passed". Pipe it through
`jest_lens.py` and you get the counts plus the failure blocks, while the full
output is stored under a short run id so nothing is lost. Written for driving
Jest from an LLM agent, where that output competes with the code for a context
budget.

`2>&1` is required, because Jest reports to stderr.

```console
$ yarn jest 2>&1 | python3 jest_lens.py
RUN ID: a3f19c
FAIL  2 failed, 91 passed in 18.02 s

--- FAILURE 1: src/b.test.ts › uplift calculator › applies the 1.05 factor ---
    Expected: 105
    Received: 100

      at Object.<anonymous> (src/b.test.ts:22:19)

$ python3 jest_lens.py --console      # console.log output, absent from failures
$ python3 jest_lens.py --failures     # every failure block, untruncated
$ python3 jest_lens.py --logs | grep 'foo'   # the raw output
$ python3 jest_lens.py --failed-paths # failing suite paths, to compose a rerun
```

The id defaults to the last run. Stdlib-only Python, nothing to build.

As a Claude Code skill, either install the plugin:

```console
$ claude plugin marketplace add ashwhall/jest-lens
$ claude plugin install jest-lens@jest-lens
```

or clone straight into your skills directory:

```console
$ git clone https://github.com/ashwhall/jest-lens ~/.claude/skills/jest-lens
```
