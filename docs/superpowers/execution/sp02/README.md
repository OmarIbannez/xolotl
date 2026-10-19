# SP-02 execution kit

This directory is for the agent that executes the three SP-02 plans, one pull request per task. Read it first. The Notion page "Handoff — SP-02 plans merged, ready to execute (2026-10-19)" carries the same state in short.

## State at hand-off (2026-10-19)

- **`main`** holds SP-01 (PRs #5 to #12), the deployment-review follow-ups (PR #13), and the SP-02 plans with this kit (the docs PR of Notion task XOL-46).
- **Production** is https://xolotl.onrender.com. `GET /readyz` answers 200, Render redeploys `main` when its checks pass, and tenant `acme` exists. Never sign in with credentials pasted in chat; check the service unauthenticated (`/readyz`, `/login`).
- **No SP-02 code exists yet.** The Tasks board has one task per plan task, XOL-47 (A1) to XOL-73 (C9), all To Do; each page names its branch and dependencies. XOL-74, the verification read of the reconciliation, goes first.

## Files

- `tasks.json` has one row per task: id, XOL number, plan, title, slug, branch, dependencies, Notion page id and URL, and whether it touches the console.
- `agreement.md` holds the rules every agent follows: product rules, gates, kernel conventions, commits, pull requests, Notion and the report.
- `roles.md` covers the implementer, task reviewer, fixer, finisher and merger.
- `execute.workflow.js` is a Workflow script. It runs the per-task loop over the dependency graph, in parallel where the graph allows.
- `../../plans/2026-10-19-sp02-reconciliation.md` records the fifteen cross-plan findings and the decisions the plans carry.

## Order of work

1. **Prepare.** Run `git -C /Users/omaribannez/code/xolotl pull --ff-only`, with Docker running and `gh auth status` green.
2. **XOL-74.** One reviewer reads the reconciliation record against the task sections it touches. A mismatch is fixed in a small docs PR.
3. **Execute.** The first wave has no dependencies: A1, A2 and C1. After that the graph in `tasks.json` decides.
   - Migrations are serialised by the graph: A2 (`0006`), then B1 (`0007`), then C2 (`0008`).
   - The longest chain is nine tasks and ends at C9.
   - Every task is its own PR, merged with a merge commit as soon as CI is green, and its Notion page moves from In Progress to In Review to Done.
4. **Close the umbrella tasks** as each plan completes, each with a note:
   - **Plan 2a** closes XOL-8 and XOL-6. XOL-6's text names a `country-kits` repository and `xolotl kit load`; the spec chose `policy-packs/` and `xolotl packs`.
   - **Plan 2b** closes XOL-10.
   - **Plan 2c** closes XOL-13. Its consent records belong to SP-08 in the program spec, so note that or split them into their own task.
5. **Hand off.** Update the Handoff page in Notion at the end of the session.

## Running the workflow

```
Workflow({
  scriptPath: "/Users/omaribannez/code/xolotl/docs/superpowers/execution/sp02/execute.workflow.js",
  args: {
    tasks: <the array in tasks.json>,
    reports: "<your scratchpad>/sp02-reports",
    trailers: "Co-Authored-By: <the model line your session's attribution gives>\nClaude-Session: <your session URL>",
    prFooter: "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n<your session URL>",
    targets: ["A1", "A2", "C1"],
    done: []
  }
})
```

- **`targets`** names the tasks to drive. Their unmerged dependencies run first.
- **`done`** lists tasks already merged; read them from the board (Status Done).
- **Run in waves** and read the result table between waves. It lists each task's state (MERGED, BLOCKED, FAILED, SKIPPED), its PR, its merge commit and why it stopped.
- **The finish and merge steps** default to the `sonnet` model; pass `finishModel` or `mergeModel` to change that.

Without the script, follow superpowers:subagent-driven-development one plan at a time, with `agreement.md` and `roles.md` as the briefs, and keep one PR per task.

## Limits and cost

- **Measured on 2026-10-19:**

  | Run | Agents | Subagent tokens |
  |---|---|---|
  | Writing the three plans | 19 | about 6.3 million |
  | The five-item backlog PR | 12 | about 1.5 million |

- **Per task**, expect four to six agents: implementer, reviewer, up to three fixes, finisher and merger. Budget for that and run in waves.
- **Usage limits** stop agents mid-run, with "session limit" or "out of usage credits". A stopped agent returns nothing, so the script marks that task FAILED at its stage and skips the tasks that depend on it. After the limit resets, run again with `done` set to the merged tasks. An implementer that finds its branch or PR already there reuses it.
- **A BLOCKED task** has one of three causes: an architectural question, a Critical finding after three fix rounds, or a red gate. Only an architectural question goes to the product owner; decide the rest.

## Open product questions

Each plan ends with "Open questions for the product owner": seven for 2a, four for 2b and four for 2c. Execution proceeds on the plans' defaults. A different answer from the product owner becomes a follow-up task.
