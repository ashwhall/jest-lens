---
name: jest-lens
description: Run Jest and get a short report instead of tens of thousands of lines. Use for ANY request to run tests, run jest, check tests, verify a suite, re-run a failing test, or ask what the tests printed. Also use after editing code, before a commit, or before a PR. Pipe Jest's output through jest_lens.py; the full log stays recoverable by run id, so console.log output and truncated failures are one command away.
---

# jest-lens

Run Jest however the repo needs, and pipe its output through the
`jest_lens.py` that sits beside this file, invoked by its absolute path.
Examples below write `<jl>` for `python3 /that/path/jest_lens.py`.

```bash
yarn jest path/to/thing.test.ts 2>&1 | <jl>
```

**The `2>&1` is not optional.** Jest reports to stderr, so without it the pipe
gets the package manager's banner and nothing else. The tool will tell you when
it sees that, but only after wasting a run.

Output is the counts, the failure blocks capped at ~5KB, and a run id:

```
RUN ID: a3f19c
FAIL  2 failed, 91 passed in 18.02 s

--- FAILURE 1: src/b.test.ts › uplift calculator › applies the 1.05 factor ---
...
```

Exit status is 0 on a pass, 1 on failures or a pre-test error, 2 when the
stream could not be parsed. Jest's own exit code is lost to the pipe.

## Recovering what the report left out

The report never contains console output, and it truncates past ~5KB. Both are
in the stored log. `last` is the default, so the id is usually unnecessary.

```bash
<jl> --console            # console.log output Jest captured
<jl> --failures           # every failure block, untruncated
<jl> --logs | grep 'foo'  # the raw output, for anything else
<jl> --failed-paths       # failing suite paths, to compose a rerun
<jl> --runs               # recent runs, newest first
```

Re-run only what broke:

```bash
yarn jest $(<jl> --failed-paths) 2>&1 | <jl>
```

Logs live in `~/.cache/jest-lens/`, pruned to the last 20 runs.

## Choosing the command

This tool never runs Jest. Picking the runner, the node version and the flags
is yours, because the repo's requirements are not derivable from its lockfile.

- Runner: `yarn jest`, `pnpm jest`, `pnpm nx test <project>`, or `npx jest`.
- Node: repos pin incompatible majors. Where a `.nvmrc` or `.node-version`
  exists, run `fnm use` first; it persists across later commands.
- To inherit the repo's own tuned Jest flags, pipe its script instead:
  `yarn test 2>&1 | <jl>`. Watch for a `test` script that also runs a linter,
  whose exit code then reads as a test failure.
- Never pass `--silent`. It suppresses the console output the log exists to keep.
- Never pass `--watch`. Without a TTY it never terminates.
- If a run dies inside watchman rather than in a test, pass `--watchman=false`.
  Jest falls back to its own file crawler.
- Where a repo collects coverage by default, `--coverage=false` is usually worth
  it. The report drops the table anyway, and instrumentation dominates the run
  time on a short one.

If a run reports a pre-test error, read the `AMBIENT NODE:` line first. A wrong
node major is the most common cause, and it crashes at module load.
