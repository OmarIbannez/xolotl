# SP-04 execution kit

This directory is for the agent that executes the three SP-04 plans, one pull request per task. Read it first.

## State at hand-off (2026-10-20)

- **`main`** holds SP-01, SP-02, SP-03 (plans 3a, 3b and 3c, the screening library T1 to T6, the SP-03 exit test), the SP-04 spec (`docs/superpowers/specs/2026-10-20-sp04-monitoring-captures-design.md`, a draft dated 2026-10-20 that the product owner has not yet formally accepted; the plans build its Decisions and §21 defaults under the blanket approval of 2026-10-19 and list their departures as Deviations), the three SP-04 plans, the reconciliation record and this kit. The migration chain ends at `0011_screening_hit_run_index`; SP-04's migrations are `0012_transactions` (A2), `0013_rules_captures` (B4) and `0014_decide_profiles` (C4), merged in that order.
- **The screening library** is complete and merged. Our tasks never implement or edit it (see `agreement.md`); the spec's §11.4 library change is withdrawn, so there is no `external` row.
- **No SP-04 code exists yet.** The Tasks board has one row per plan task, XOL-112 (A1) to XOL-149 (C11), all To Do; each page names its plan section, branch and dependencies. The `xol`, `notion` and `notion_url` columns of `tasks.json` are filled from the board.
- **The kit's home path** is `/Users/omaribannez`; on the other machine it is `/Users/oibanez` and the worktrees live under `/Users/oibanez/code/xolotl-worktrees`. Pass `kit` and `worktrees` to the workflow there.

## Files

- `tasks.json`: one row per task (id, XOL number, plan, title, slug, branch `feat/<slug>`, dependencies, Notion page id and URL, whether it touches the console, the board's estimate and tags). Thirty-eight rows: A1 to A12, B1 to B15, C1 to C11. No `external` rows.
- `agreement.md`: the rules every agent follows. It is the SP-03 agreement with the SP-04 references, the migration order, the note on the other machine's paths, and "The screening library is not yours" extended with what of the SP-03 engine a 4c task may touch.
- `roles.md`: implementer, task reviewer, fixer, finisher and merger (unchanged from SP-03).
- `execute.workflow.js`: the Workflow script, the SP-03 one with the kit path pointing here. It keeps the `external` handling (a row marked external is never implemented), which nothing in SP-04 uses.
- `check-graph.mjs`: checks `tasks.json` (unique ids, every dependency exists, acyclic, complete rows) and prints the longest chain and the waves for a given `done` list. Run it before every wave: `node docs/superpowers/execution/sp04/check-graph.mjs A1,A3`.
- `../../plans/2026-10-20-sp04-reconciliation.md`: the twenty-two cross-plan findings and the decisions the plans carry, the shared-file ownership table (finding 17) and the questions for the product owner.

## Order of work

1. **Prepare.** `git -C /Users/omaribannez/code/xolotl pull --ff-only`, Docker running, `gh auth status` green. Read the board's Done tasks to build `done`.
2. **Verify the reconciliation.** One reviewer reads the reconciliation record against the task sections it touches; a mismatch is fixed in a small docs PR before those tasks start. A2 (the first migration) should not start before spec §5.2's first block is accepted by the product owner (4a Open question 4).
3. **Execute in waves.** Migrations are serialised by the graph: A2 (`0012_transactions`), then B4 (`0013_rules_captures`), then C4 (`0014_decide_profiles`). `node docs/superpowers/execution/sp04/check-graph.mjs` with nothing done prints:

   ```
   ok: 38 tasks, 0 external prerequisites, acyclic, every dep exists
   longest chain (12): A1 -> A2 -> A7 -> B1 -> B3 -> B5 -> B6 -> B7 -> B10 -> B11 -> C6 -> C11
     of which our tasks (12): A1 -> A2 -> A7 -> B1 -> B3 -> B5 -> B6 -> B7 -> B10 -> B11 -> C6 -> C11
   done: []
   first wave: A1, A3
     wave 1: A1, A3
     wave 2: A2
     wave 3: A4, A6, A7, B4
     wave 4: A5, A8, B1, C1
     wave 5: A9, B2, B3, C2
     wave 6: A10, B5
     wave 7: A11, A12, B6, C3, C4
     wave 8: B7, C7
     wave 9: B8, B10, B12, C8
     wave 10: B9, B11, B14, C5, C9
     wave 11: B13, B15, C6, C10
     wave 12: C11
   waiting on an external prerequisite: (none)
   ```

   - The first wave is **A1 and A3**; A2 follows both, and from wave 3 on the three plans run abreast (B4 beside A4, A6 and A7; C1 beside A5, A8 and B1).
   - 4b's pure tasks (B1, B2, B3) wait only for A7; its tables (B4) wait only for A2; B7, the plan's centre, waits for A8.
   - 4c's counterparty screening (C1, C2) needs A4 and B4; C3 and C4 need A10; C5 needs B7 and B12; C6 needs B8 and B11; C8 needs B7; C9 needs A11.
   - The longest chain is twelve tasks; with three agents the waves above take about four and a half weeks, the spec's estimate.
   - Every task is its own PR, merged with a merge commit as soon as CI is green; its Notion page moves from In Progress to In Review to Done.
4. **Close the umbrella tasks** with a note as the plans complete: XOL-23 (ingest, enrichment, monitoring consumer with aggregates and alerts) after A1 to A12 and B7, noting that alerts are hits until SP-06; XOL-24 (Mexico reportable-event captures, tier limits and late events) after B10, B11, B15 and C5; XOL-36 (synchronous decision API, rule simulation, effectiveness metrics and behaviour profiles) after B8, B9, B13, C5, C6, C8 and C9; XOL-7 (rule expression language) stays open, with a note that SP-04 built parameterised kinds (spec Decision 1, §21 Q1).
5. **Hand off.** Update the Notion handoff page at the end of the session.

## Running the workflow

```
Workflow({
  scriptPath: "/Users/omaribannez/code/xolotl/docs/superpowers/execution/sp04/execute.workflow.js",
  args: {
    tasks: <the array in tasks.json>,
    reports: "<your scratchpad>/sp04-reports",
    trailers: "Co-Authored-By: <the model line your session's attribution gives>\nClaude-Session: <your session URL>",
    prFooter: "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n<your session URL>",
    targets: ["A1", "A3"],
    done: []
  }
})
```

- **`targets`** names the tasks to drive; their unmerged dependencies run first. Without it, every task is a target.
- **`done`** lists what is already merged on `main`. Keep it in step with the board between waves.
- **Run in waves** and read the result table between waves. It lists each task's state (MERGED, BLOCKED, FAILED, SKIPPED), its PR, its merge commit and why it stopped.
- **The finish and merge steps** default to the `sonnet` model; pass `finishModel` or `mergeModel` to change that. On the other machine pass `kit: "/Users/oibanez/code/xolotl/docs/superpowers/execution/sp04"` and `worktrees: "/Users/oibanez/code/xolotl-worktrees"`.

Without the script, follow superpowers:subagent-driven-development one plan at a time, with `agreement.md` and `roles.md` as the briefs, and keep one PR per task.

## Limits and cost

- Per task expect four to six agents: implementer, reviewer, up to three fixes, finisher and merger. Budget for that and run in waves.
- Usage limits stop agents mid-run. A stopped agent returns nothing, so the script marks that task FAILED at its stage and skips its dependents. After the limit resets, run again with `done` updated; an implementer that finds its branch or PR already there reuses it.
- A BLOCKED task has one of four causes: an architectural question, something the library does not provide (agreement: "The screening library is not yours"), a Critical finding after three fix rounds, or a red gate. Architectural questions go to the product owner and library gaps to the library owner; decide the rest.

## Open product questions

The reconciliation record ends with eleven questions for the product owner, collected from the plans' "Open questions" sections (four for 4a, six for 4b, four for 4c, some merged): the USD and FIX rule, the opt-in webhooks, the jurisdiction lists, the December 2026 fixtures, the counterparty purge, the spec's acceptance, who may submit a version, the `rules.loaded` audit, short simulations, the Playwright flow, and counterparties on private lists and re-indexing. Execution proceeds on the plans' defaults; a different answer becomes a follow-up task.
