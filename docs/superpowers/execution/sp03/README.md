# SP-03 execution kit

This directory is for the agent that executes the three SP-03 plans, one pull request per task. Read it first.

## State at hand-off (2026-10-19)

- **`main`** holds SP-01, SP-02 (plans 2a, 2b and 2c, including C9), the SP-03 spec (`docs/superpowers/specs/2026-10-19-sp03-lists-screening-design.md`, departures and open-question defaults accepted by the product owner on 2026-10-19), the three SP-03 plans, the reconciliation record and this kit.
- **The screening library** (`docs/superpowers/plans/2026-10-19-screening-library.md`, T1 to T6) is built by another engineer on branches `feat/screening-lib-*`. **T1 is merged** (PR #52: `screening/lists/` types, `parse`, `diff`, the UN parser). T2 and T3 have open branches; T4, T5 and T6 have not started. Our tasks never implement or edit the library (see `agreement.md`).
- **No SP-03 code of ours exists yet.** The Tasks board has one row per plan task, XOL-80 (D1) to XOL-106 (F8), all To Do; each page names its plan section, branch and dependencies.

## Files

- `tasks.json`: one row per task (id, XOL number, plan, title, slug, branch `feat/<slug>`, dependencies, Notion page id and URL, whether it touches the console), plus six `external` rows for the library tasks (`LIB-lists-T1`, `LIB-lists-T2`, `LIB-lists-T3`, `LIB-matching-T4`, `LIB-matching-T5`, `LIB-corpus-T6`) that our tasks depend on.
- `agreement.md`: the rules every agent follows. It is the SP-02 agreement with the SP-03 references and one added section, "The screening library is not yours".
- `roles.md`: implementer, task reviewer, fixer, finisher and merger (unchanged from SP-02).
- `execute.workflow.js`: the Workflow script, the SP-02 one with the kit path pointing here and one addition: an `external` task is never implemented. If it is in `done` it counts as merged; otherwise it is SKIPPED with the note "external prerequisite not merged", and every task that needs it is skipped too.
- `check-graph.mjs`: checks `tasks.json` (unique ids, every dependency exists, acyclic, complete rows) and prints the longest chain, the waves for a given `done` list and the tasks waiting on each external row. Run it before every wave: `node docs/superpowers/execution/sp03/check-graph.mjs LIB-lists-T1`.
- `../../plans/2026-10-19-sp03-reconciliation.md`: the sixteen cross-plan findings and the decisions the plans carry, including the shared-file ownership table and the library gaps.

## Order of work

1. **Prepare.** `git -C /Users/omaribannez/code/xolotl pull --ff-only`, Docker running, `gh auth status` green. Read the board's Done tasks and the library's merged PRs (`gh pr list --state merged --search screening-lib`) to build `done`.
2. **Verify the reconciliation.** One reviewer reads the reconciliation record against the task sections it touches; a mismatch is fixed in a small docs PR before those tasks start.
3. **Execute in waves.** Migrations are serialised by the graph: D1 (`0009_lists`), then E2 (`0010_screening`). There is no `0011`.

   | Library merged | Runnable waves of our tasks |
   |---|---|
   | none | D1, E1 → D2, E2 → D3 |
   | T1 (today) | D1, E1 → D2, E2 → D3, D4 → D5, D6 → D7 → D8, D9 |
   | T1, T4, T5 | D1, E1 → D2, E2 → D3, D4, E4 → D5, D6, E6 → D7, E3 → D8, D9, E5 → E7, E8 → E9, E10, F1, F5 |
   | T1 to T6 | as above with F2 in wave 4, then F3, F6 → F4 → F7 → F8 (twelve waves) |

   - With T1 merged (today), the first wave is **D1 and E1**; plan 3a can run to the end (D9).
   - Plan 3b's engine waits for library **T5** (E3, E4, E5, E8) and **T4** (E4). E6 waits only through E4.
   - Plan 3c waits for 3b's E5, E7 and E8, and F2, F6 and F7 for library **T6**.
   - The longest chain is twelve tasks: D1 → D2 → D3 → D6 → E3 → E5 → E7 → F1 → F3 → F4 → F7 → F8.
   - Every task is its own PR, merged with a merge commit as soon as CI is green; its Notion page moves from In Progress to In Review to Done.
4. **Close the umbrella tasks** with a note as the plans complete: XOL-18 (list ingestion) after D1 to D8; XOL-25 (delta re-screening and private lists) after D6, D9 and F1 to F5, noting that its PEP-status and counterparty-cache parts are outside SP-03 (spec §18 Q7); XOL-19 (name matcher) after E1 to E10 and F6, noting that the matcher itself is the library's.
5. **Hand off.** Update the Notion handoff page at the end of the session.

## Running the workflow

```
Workflow({
  scriptPath: "/Users/omaribannez/code/xolotl/docs/superpowers/execution/sp03/execute.workflow.js",
  args: {
    tasks: <the array in tasks.json>,
    reports: "<your scratchpad>/sp03-reports",
    trailers: "Co-Authored-By: <the model line your session's attribution gives>\nClaude-Session: <your session URL>",
    prFooter: "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n<your session URL>",
    targets: ["D1", "E1"],
    done: ["LIB-lists-T1"]
  }
})
```

- **`targets`** names our tasks to drive (an external id is refused); their unmerged dependencies run first. Without it, every non-external task is a target.
- **`done`** lists what is already merged on `main`, **external library tasks included**. Leaving a merged library task out makes its dependents SKIPPED, never implemented.
- **Run in waves** and read the result table between waves. It lists each task's state (MERGED, BLOCKED, FAILED, SKIPPED), its PR, its merge commit and why it stopped; an external row shows `stage: external`.
- **The finish and merge steps** default to the `sonnet` model; pass `finishModel` or `mergeModel` to change that.

Without the script, follow superpowers:subagent-driven-development one plan at a time, with `agreement.md` and `roles.md` as the briefs, and keep one PR per task.

## Limits and cost

- Per task expect four to six agents: implementer, reviewer, up to three fixes, finisher and merger. Budget for that and run in waves.
- Usage limits stop agents mid-run. A stopped agent returns nothing, so the script marks that task FAILED at its stage and skips its dependents. After the limit resets, run again with `done` updated; an implementer that finds its branch or PR already there reuses it.
- A BLOCKED task has one of four causes: an architectural question, something the library does not provide (agreement: "The screening library is not yours"), a Critical finding after three fix rounds, or a red gate. Architectural questions go to the product owner and library gaps to the library owner; decide the rest.

## Open product questions

Each plan ends with "Open questions for the product owner": three for 3a, four for 3b and three for 3c. Execution proceeds on the plans' defaults; a different answer becomes a follow-up task. The library gaps of reconciliation finding 15 go to the library owner, not the product owner.
