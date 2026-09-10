# jest-lens

Jest prints tens of thousands of lines to say "93 passed". This turns a run into
a short report and keeps the full output recoverable by a short id.

```
$ yarn jest 2>&1 | jl
RUN ID: a3f19c
FAIL  2 failed, 91 passed in 18.02 s

--- FAILURE 1: src/b.test.ts › uplift calculator › applies the 1.05 factor ---
    expect(received).toBe(expected)

    Expected: 105
    Received: 100

      at Object.<anonymous> (src/b.test.ts:22:19)

Test Suites: 2 failed, 1 passed, 3 total
Tests:       2 failed, 91 passed, 93 total

Recover: jl --logs a3f19c   (also --failures, --console, --failed-paths)
```

Written for driving Jest from an LLM agent, where the report competes with the
code for a context budget. It works the same for a human who is tired of
scrolling.

## Why a pipe rather than a wrapper

The tool never runs Jest. A wrapper has to guess the runner, the node version
and the flags, and a monorepo, a pinned node major or a native module built
against the wrong ABI all defeat the guess. Piping leaves that to whoever knows
the repo, and works unchanged with `yarn jest`, `pnpm nx test`, a repo's own
`test` script, or anything else that emits Jest's reporter format.

## Install

```bash
git clone https://github.com/ashwhall/jest-lens ~/projects/ash/jest-lens
~/projects/ash/jest-lens/install.sh
```

That symlinks `jl` into `~/.local/bin` and the skill into `~/.claude/skills`.
No dependencies, and nothing to build: it is one stdlib-only Python file.

## Use

`2>&1` is required. Jest reports to stderr.

| Command | Prints |
| --- | --- |
| `yarn jest 2>&1 \| jl` | the report, and a run id |
| `jl --logs [id]` | the run's complete output |
| `jl --failures [id]` | every failure block, untruncated |
| `jl --console [id]` | the console output Jest captured |
| `jl --failed-paths [id]` | failing suite paths, for composing a rerun |
| `jl --runs` | recent runs, newest first |

The id defaults to `last`, and a unique prefix works in place of a full id.

Exit status is 0 on a pass, 1 on failures or a pre-test error, 2 when the stream
could not be parsed at all. Jest's own exit code is lost to the pipe, so the
status is inferred from the parsed counts.

Logs live in `~/.cache/jest-lens/`, pruned to the last 20 runs. Set
`JEST_LENS_DIR` to move them.

## What it surfaces beyond the counts

- Failed snapshots, as a line rather than buried in the summary.
- Jest's "did not exit" and worker-failed-to-exit warnings, which mean a handle
  outlived the suite.
- `No tests found`, which is almost always a mistyped path rather than a real
  result.
- The ambient node version on a pre-test error, since a wrong major crashes at
  module load and the output never names it.
- A missing `2>&1`, guessed from a stream that holds neither test results nor
  anything error-shaped.

Ctrl-C keeps the partial log and prints the id.
