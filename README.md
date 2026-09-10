# jest-lens

Jest prints tens of thousands of lines to say "93 passed". Pipe it through `jl`
and you get the counts plus the failure blocks, while the full output is stored
under a short run id so nothing is lost. Written for driving Jest from an LLM
agent, where that output competes with the code for a context budget.

`2>&1` is required, because Jest reports to stderr.

```console
$ yarn jest 2>&1 | jl
RUN ID: a3f19c
FAIL  2 failed, 91 passed in 18.02 s

--- FAILURE 1: src/b.test.ts › uplift calculator › applies the 1.05 factor ---
    Expected: 105
    Received: 100

      at Object.<anonymous> (src/b.test.ts:22:19)

$ jl --console            # console.log output, which failure blocks never hold
$ jl --failures           # every failure block, untruncated
$ jl --logs | grep 'foo'  # the raw output
$ jl --failed-paths       # failing suite paths, to compose a rerun
```

The id defaults to the last run. Install with `./install.sh`, which symlinks
`jl` and the Claude Code skill. Stdlib-only Python, no dependencies.
