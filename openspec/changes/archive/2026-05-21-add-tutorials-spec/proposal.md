## Why

The `documentation` spec mandates a Diátaxis quadrant structure with a
`tutorials/` directory, but the spec is silent on what tutorials must
exist, what shape each tutorial takes, and what it means for a tutorial
to be "complete." The result is that `docs/tutorials/index.md` lists
three TODOs (`first-backtest.md`, `authoring-a-strategy.md`,
`running-an-optimization.md`) that have lingered since the docs
platform landed — there is no enforceable contract to drive them to
completion, and no skeleton future authors can follow.

Tutorials are the learn-by-doing entry point. They are how a first-time
user goes from `git clone` to a working backtest. Leaving them
unspecified leaves the most valuable on-ramp documented only as a
README bullet.

## What Changes

- Add a fixed tutorial-page skeleton: prerequisites, learning goal, one
  happy-path walkthrough, expected output at each major step, "what
  next" pointers into the other quadrants.
- Enumerate the three tutorials the project must ship in its initial
  set: `first-backtest`, `authoring-a-strategy`, `running-an-optimization`.
  Each MUST exist in `docs/tutorials/` before this change archives.
- Forbid forward-looking placeholders in `docs/tutorials/index.md`;
  every entry MUST link to a real, committed page.
- Each tutorial MUST run end-to-end from the commands it lists; the
  contract is enforced by code review (no executable tutorial-runner
  test today — strategy-gpt CLI surface is large enough that a green
  CLI smoke covers most of the surface tutorials touch).
- Tutorials live in exactly one quadrant (`docs/tutorials/`), per the
  existing quadrant requirement, with mkdocs nav entries mirroring the
  filesystem path.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `documentation`: adds requirements specifying the tutorial page
  skeleton, the initial tutorial set the project ships, and the
  no-placeholder rule for the tutorials index.

## Impact

- **Docs**: three new pages under `docs/tutorials/`. The existing
  `docs/tutorials/index.md` TODO list is replaced with real links.
  `mkdocs.yml` nav grows three entries under the Tutorials section.
- **Spec**: one delta file at `openspec/changes/add-tutorials-spec/specs/documentation/spec.md`
  adding the new requirements.
- **CI**: `mkdocs build --strict` already gates broken links; no new CI
  job is required. The new tutorials run through the same gate.
- **No code changes**. This is a documentation-only change.
