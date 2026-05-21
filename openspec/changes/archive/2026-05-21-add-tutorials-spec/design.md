## Context

`docs/tutorials/index.md` has carried three planned-but-unwritten pages
since the documentation platform landed (ADR 0015). The Diátaxis
quadrant requirement in the `documentation` spec mandates the
directory exists, but does not say anything about *what* tutorials the
project ships, the shape of a tutorial page, or that placeholders are
forbidden. The result is durable rot: a TODO list visible on the
published site, no enforceable contract to drive the pages to
completion.

The bundled VXX reference strategy + the optimize CLI already give a
working happy path for each of the three planned pages. The work of
this change is the *contract* (one new requirement set on the existing
`documentation` capability) plus the three pages themselves.

## Goals / Non-Goals

**Goals:**

- Make the tutorial contract enforceable at code review and at
  `mkdocs build --strict` time.
- Land the three initial tutorials so the published `tutorials/index.md`
  has no placeholders.
- Make future tutorial pages cheap to author: a fixed skeleton future
  authors can fill in.
- Stay inside the existing `documentation` capability (this is a Diátaxis
  detail, not a new capability).

**Non-Goals:**

- No executable tutorial-runner test (tutorials are prose + CLI examples;
  the CLI smoke + existing pytest already cover the surface).
- No expansion of the four-quadrant Diátaxis structure.
- No reshuffling of existing how-to / reference / explanation pages.
- No version-pinned dependency upgrades.

## Decisions

### Decision: Tutorial pages follow a fixed skeleton

The skeleton mirrors the methodology-page convention (which already
forces a Limitations section). For tutorials, the required sections
are:

1. **Learning goal** — one sentence on what the reader will be able to
   do at the end of the page.
2. **Prerequisites** — bullet list of tools / files / env vars assumed.
3. **Walkthrough** — numbered steps. Each step has a command + the
   expected output snippet so a reader can confirm they are on track
   without running ahead.
4. **What you just did** — one short paragraph naming the components
   exercised.
5. **What next** — bullet list of links into the other three quadrants
   (how-to recipes, reference pages, explanation pages).

Locking the skeleton makes future tutorials a fill-in-the-blanks
exercise and lets reviewers reject incomplete pages mechanically.

**Alternative considered:** free-form tutorials. Rejected because the
existing tutorial slot has rotted under a no-contract regime for the
docs platform's entire history; the failure mode is exactly the
absence of a skeleton.

### Decision: The initial tutorial set is the three already named

The Diátaxis principle is that tutorials are the *first* exposure for a
new user, not an exhaustive feature dump. Three tutorials covering
(a) running the bundled reference strategy, (b) writing a new
strategy, (c) running an optimize pass cover the major user roles
(quant, engineer, operator) with minimal overlap.

The three tutorials this change ships are:

- `docs/tutorials/first-backtest.md` — clone → install → run VXX → read
  the result.
- `docs/tutorials/authoring-a-strategy.md` — implement the sealed
  `Strategy` trait in Rust against the engine-rt PROMPT_API surface.
- `docs/tutorials/running-an-optimization.md` — author an experiment
  spec, run `strategy-gpt optimize`, read `best.json`.

**Alternative considered:** a single mega-tutorial. Rejected because
the audiences diverge fast (a quant authoring a strategy and an
operator running an optimization need very different framings).

**Alternative considered:** ship more tutorials. Rejected because we
should not commit to maintaining tutorials we have not written. The
spec wording permits future additions inside the same skeleton; the
contract floor is three.

### Decision: Forbid placeholders in `tutorials/index.md`

Every link in the tutorials index MUST resolve to a committed page
under `docs/tutorials/`. This is already implicitly enforced by
`mkdocs build --strict`, but stating it in the spec gives reviewers a
named requirement to cite when a future PR tries to add a "coming
soon" entry.

**Alternative considered:** leave placeholder tolerance up to reviewer
discretion. Rejected — the current TODO entries are exactly what the
no-contract regime produces.

## Risks / Trade-offs

[Tutorial drift over time] → CLI surface and strategy crate API will
evolve. Mitigation: tutorials reference the reference strategy
verbatim, and the smoke fixture in `kb/fixtures/smoke_run.json`
already exercises the same code path. When that fixture regenerates,
the operator regenerating it should re-read the tutorials. Captured
as a "What next" pointer in the tutorial itself.

[Skeleton too restrictive] → Some future tutorial may need a richer
shape (e.g., a multi-strategy walkthrough). Mitigation: the spec
phrases the skeleton as required sections in order, but does not
forbid additional sections; the contract floor is "what's there must
be present," not "nothing else may appear."

[Three is the wrong number] → The right number could be four or five.
Mitigation: the spec says "at least these three." Adding a fourth is
additive, not a breaking change to the contract.

## Migration Plan

1. Land this change's proposal + design + delta spec.
2. Author the three tutorials in `docs/tutorials/` against the
   skeleton.
3. Replace `docs/tutorials/index.md` with a clean index pointing at
   the new pages.
4. Wire mkdocs nav entries for the three pages.
5. `mkdocs build --strict` + `make lint` clean.
6. Archive.

No rollback path needed — change is additive and documentation-only.

## Open Questions

- **Should the spec name a hard length cap for tutorials?** Current
  text leaves length open. First-pass tutorials may inform whether a
  cap is worth adding. Defer to a follow-up.
- **Should the spec require a screenshot in the "what you just did"
  section?** Helpful for CLI-shy readers, expensive to maintain.
  Defer.
