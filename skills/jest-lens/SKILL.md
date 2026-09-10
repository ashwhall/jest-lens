---
name: jest-lens
description: Run Jest and get a short report instead of tens of thousands of lines, keeping the full output under a run id. Use for any request to run tests, run jest, check or verify a suite, or re-run a failing test; also after editing code and before a commit or PR. When the question is what a run printed or logged, grep that stored run by its id rather than re-running the suite.
---

# jest-lens

Pipe Jest's output through this plugin's `jest_lens.py`. The `2>&1` is
required, because Jest reports to stderr.

```bash
yarn jest path/to/thing.test.ts 2>&1 | python3 "${CLAUDE_PLUGIN_ROOT}/jest_lens.py"
```

Below, `<jl>` stands for that same `python3` invocation.

You get the counts, the failure blocks up to a ~5KB cap, and a run id. Exit
status is 0 on a pass, 1 on failures or a pre-test error, 2 on an unparseable
stream, and 130 on Ctrl-C, which still keeps the partial run. Jest's own exit
code is lost to the pipe.

## Recovering what the report omits

Jest keeps console output out of its failure blocks, and the report caps those
blocks. The stored run holds everything, so a question about what a run
printed is answered by reading it, not by running the suite again.

```bash
<jl> --console a3f19c        # console output, absent from failures
<jl> --all-failures a3f19c   # every failure block, no cap
<jl> --full-log a3f19c       # the entire stream, greppable
<jl> --failed-paths a3f19c   # file paths of failing suites, not test names
<jl> --list-runs             # stored runs, newest first
<jl> --audit a3f19c          # that run's cost against tail -40 of its log
```

Each needs the id the run printed, except `--list-runs` and a bare `--audit`,
which reports the lifetime totals instead. An unambiguous prefix works, and
`--list-runs` finds a lost one. Only `--all-failures` and `--console` combine.

Every recovery call is added to its run's cost, so `--audit` after several of
them can report a cost rather than a saving. That is meant to be visible: on a
short run the report is dearer than a tail, and the saving comes from the runs
where a tail would have been huge.

Re-running failures covers whole files, so their passing tests run again. There
is no `-t` equivalent.

```bash
yarn jest $(<jl> --failed-paths a3f19c) 2>&1 | <jl>
```

## Choosing the command

This never runs Jest. The runner, the node version and the flags are yours.

- `yarn jest`, `pnpm jest`, `pnpm nx test <project>`, or `npx jest`. To inherit
  the repo's own Jest flags, pipe its script instead: `yarn test 2>&1 | <jl>`.
  Watch for a `test` script that also lints, whose exit code then reads as a
  test failure.
- Activate the repo's pinned node first. A wrong major crashes at module load,
  native modules first. On a pre-test error, read the `AMBIENT NODE:` line.
- Never `--silent`, which drops the console output the run exists to keep, or
  `--watch`, which never terminates without a TTY.
- `--watchman=false` if a run dies inside watchman rather than in a test.
- `--coverage=false` where a repo collects it by default. The report drops the
  table, and instrumentation dominates a short run.
