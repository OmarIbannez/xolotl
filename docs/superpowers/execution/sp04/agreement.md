# Working agreement for every agent executing an SP-04 plan task

The repository is xolotl (`git@github.com:OmarIbannez/xolotl.git`, default branch `main`; Python 3.13 and uv, FastAPI, SQLAlchemy 2.1, Alembic, psycopg 3, Postgres 17; a React 19, Vite and TypeScript console in `console/` served by the API). The main checkout at `/Users/omaribannez/code/xolotl` is shared and stays on `main`: never edit, commit, check out or run tests there; use it only to run `git fetch` and `git worktree add`. Every task works in its own git worktree under `/Users/omaribannez/code/xolotl-worktrees/<slug>` on branch `feat/<slug>`; your task prompt names both. On the other machine the home is `/Users/oibanez`, the main checkout `/Users/oibanez/code/xolotl` and the worktrees live under `/Users/oibanez/code/xolotl-worktrees`: read every path in this kit with that substitution when you run there. Start every shell command with `cd <that worktree> &&`. Never dispatch subagents. Never force-push, never rewrite history, never merge anything (a separate merge step does that), never touch another task's worktree. Leave `reset-repo.sh` and `rewrite-history.sh` in the main checkout alone.

## Product rules
This is an MVP: one application container plus Postgres. No S3, Kafka, Redis, Temporal or KMS; keep ports and adapters, with the local-filesystem object store as the only storage adapter. Build the smallest thing that proves the flow; no gold-plating. Architectural changes need the product owner: stop and report `BLOCKED`. Small ambiguities you decide yourself and record under "Decisions" in your report and the PR body.

## Gates CI enforces
`make ci` runs these gates:
- **ruff:** every rule (line length 88, preview). Google-style docstrings with `Returns:` / `Raises:` / `Yields:` sections where a function returns something non-trivial, raises or yields. Comments in plain prose; discarded results assigned to `_`; no `print`; `cast("T", value)` with the type as a string.
- **Type checkers:** basedpyright in "all" mode on `src` and `tests` (no `Any`, no unknown types in `src`; tests are relaxed), and ty.
- **vulture:** unused code in `src` fails; delete it or use it.
- **import-linter:** `platform` imports nothing from `modules`; module dependencies stay acyclic as the program spec's §3.3 table says; the SP-04 contracts (`monitoring-core-is-pure`, `monitoring-boundaries`, `screening-never-imports-monitoring`, `monitoring-evaluator-is-pure`) hold once the task that adds each has merged.
- **pytest:** 90% coverage over `src`, against a real Postgres started by testcontainers (Docker must be running).

Partial runs need `--no-cov`, for example `uv run pytest tests/platform/test_x.py --no-cov -q`. `uv run ruff format src tests` and `uv run ruff check --fix src tests` fix most lint. Before every commit, run `make lint`, `make types`, `make vulture`, `make importlint` and the targeted tests. Run the full `make ci` once at the end of the task (allow up to 25 minutes). When the task touches `console/`, also run `cd console && npm ci && npm run typecheck && npm run lint && npm test && npm run build`; all must pass.

## Conventions the SP-01 kernel fixes
- **Tenant tables:** `tenant_id` first in a composite primary key `(tenant_id, id)`; composite foreign keys to other tenant tables; forced row-level security through the migration helper. Each needs an isolation factory registered in `tests/isolation/factories.py`, a model imported in `src/xolotl/models.py`, and the schema-sync test green. A partitioned table (`transaction`, `txn_decision`) keys on `(tenant_id, <range column>, id)`, is registered with `xolotl.platform.partitions`, and every partition carries forced RLS.
- **Shared tables** are listed in `SHARED_TABLES`, and in `SHARED_WRITABLE` when the app role writes them.
- **Routes:** routers are built with `v1_router()` and access with `require_access`. Errors are `XolotlError` subclasses with stable codes. POSTs honour idempotency keys; list endpoints paginate with cursors. An API client never receives a rule name, a hit, a capture, an aggregate or a counterparty's screening status (tipping-off, spec §15).
- **Personal data** lives in `Encrypted*` columns, without an `_enc` suffix on the name, with blind-index columns for equality. A human sees plaintext only through `reveal_for_response`, which audits `VIEW_PII`.
- **Events and work:** domain events go through the outbox with ids-only payloads; background work runs through the job queue and its schedules. Staff-only event types are registered with `register_staff_only`; there is no per-endpoint opt-in (reconciliation finding 7).
- **Enums:** values are UPPERCASE text in non-native enum columns.
- **Migrations are serialised:** A2's `0012_transactions`, then B4's `0013_rules_captures`, then C4's `0014_decide_profiles`. A migration task starts only once its predecessor is merged on `main`, and no other SP-04 task adds a migration.

## Method
Work task by task with TDD.
1. **Read.** Read the plan's header sections: Goal, Architecture, Tech Stack, Spec, Accepted by the product owner, Dependencies outside this plan, Interfaces, Global Constraints, Deviations, Review Focus, File structure and Parallelism. Then read your task's section only, the spec sections the task cites, the code its Files list names, and the neighbouring tests for style.
2. **Test first.** Write the failing tests and run them to see them fail for the right reason.
3. **Implement.** Make the smallest change that passes them, with the exact names and values the task fixes. Build no feature the task does not ask for and no speculative abstraction.
4. **Check.** Run the targeted tests and the gates, and keep the documentation the task names in step.

When the task text and the spec disagree, the spec wins and your report says so, except where the plan's "Deviations" section records a departure on purpose: then the plan wins and your report cites the deviation. `docs/superpowers/plans/2026-10-20-sp04-reconciliation.md` records the cross-plan decisions the three SP-04 plans already carry; the spec is `docs/superpowers/specs/2026-10-20-sp04-monitoring-captures-design.md` (a draft the product owner has not yet formally accepted; its migration numbers read 0012, 0013, 0014 here). A file another plan's task creates (the ownership table in reconciliation finding 17) is appended to, never rewritten; if it is not on `main` yet when you need it, your task's dependency list is wrong: stop and report `BLOCKED`.

## The screening library is not yours
`src/xolotl/modules/screening/lists/`, `src/xolotl/modules/screening/matching/`, `src/xolotl/modules/screening/data/` (the library's tables), the library's tests and fixtures under `tests/modules/screening/{lists,matching,fixtures,corpus}/` and `tests/modules/screening/support.py`, and `docs/superpowers/plans/2026-10-19-screening-library.md` belong to the screening library package, built by another engineer (`docs/superpowers/handoffs/screening-library.md`). Import its public names (spec §11 and SP-03 spec §4); never edit those files. A library name that differs from the plan is used as the library ships it and recorded under "Decisions". If your task needs something the library does not provide, stop and report `BLOCKED` with what is missing instead of building it in the library or working around it. The library's tasks are all merged, so `tasks.json` has no `external` row; the spec's §11.4 "optional names" change is withdrawn (4c Deviation 1: `main` already scores an identifier-only query).

The SP-03 screening engine and re-screening packages (`screening/engine/`, `screening/review/`, `screening/rescreen/`, `screening/catalog/`, `screening/api.py`, `screening/schemas.py`, `screening/events.py`, `screening/composition.py`, the screening routes and the console's `features/screening/`) are ours, but a 4c task changes them only where its section says: C1 edits `engine/enums.py`, `engine/screen.py` (one `ScreenOptions` key and one `subject_query` branch), `engine/subjects.py`, `events.py` and `composition.py`; C2 edits `rescreen/list_delta.py`, `rescreen/walkers.py` and the `table` parameter of `rescreen/blocking.py`; C3 edits `api.py`, `schemas.py` and the two match pages. Anything else in the engine stays as SP-03 left it; a change elsewhere is an architectural question for the product owner.

## Commits and the pull request
Use conventional commit messages (`feat(scope): ...`, `test(scope): ...`, `docs: ...`) with a body in plain prose, ending with exactly the trailer lines your task prompt gives. The git author is configured in the repository (Omar Ibañez <OmarIbannez@gmail.com>); never change it.

Push the branch with `git push -u origin feat/<slug>`. Open a draft pull request against `main` as soon as the first commit exists, so CI starts early: `gh pr create --draft --base main --title "<conventional title>" --body-file <a file you write>`. The body has three parts:
- a "Summary": what and why, the plan and task it implements, and the decisions you took;
- a "Test plan": the commands and what they prove;
- the footer your task prompt gives, at the very end.

If the summary changed by the end, update the body with `gh pr edit --body-file`. Do not mark the PR ready and do not merge it; a later step does.

## Notion
Your task prompt names the task's Notion page id (a row of the Tasks board). Load the update tool once with `ToolSearch` (query `select:mcp__plugin_Notion_notion__notion-update-page`). Call it with `command: "update_properties"`, that `page_id` and `allow_async: false`:
- the implementer sets `{"Status": "In Progress"}` before its first commit;
- the implementer sets `{"Status": "In Review", "PR": "<the PR URL>"}` right after opening the draft PR;
- the merger sets `{"Status": "Done"}` after the merge.

The property is named `PR`. Make one call each and no page content edits. If the tool is unavailable or errors, say so in the report and carry on.

## Report
Write the full report to the report file your task prompt names. It covers:
- what you changed and where, and the tests added, by name;
- every test and gate command with its final result line;
- the commit hashes and the PR URL;
- your decisions, and any deviation from the task text with its reason;
- anything you noticed but left alone.

The structured return value carries only the status (`DONE`, `DONE_WITH_CONCERNS`, `BLOCKED` or `NEEDS_CONTEXT`), the branch, the PR number, the commit range, a one-line test summary and the concerns.
