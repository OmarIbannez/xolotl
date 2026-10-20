# Roles

Every role reads `agreement.md` (next to this file) first; it is binding. The task prompt gives the plan file, the task id, the worktree, the branch, the PR, the report file, the Notion page id and the attribution lines to use.

## Implementer

1. **Set up.** Create the worktree yourself: `cd /Users/omaribannez/code/xolotl && git fetch -q origin && git worktree add -b <branch> <worktree> origin/main`, then `cd <worktree> && uv sync --locked`. Run `cd console && npm ci` only if the task touches `console/`. Record BASE (`git rev-parse HEAD` right after the worktree is created).
2. **Resume, never duplicate.** If the branch already exists on origin or the worktree directory already exists, the task was started before. Reuse them: `git fetch -q origin && git worktree add <worktree> <branch>`, or cd into the existing worktree. Read the report file if it exists and continue from where it stopped. Never create a second branch or a second PR.
3. **Implement.** Do the task as the agreement says: push, open the draft PR, set the Notion status, write the report, and return the structured result with BASE and HEAD.
4. **When you cannot.** If the task cannot be done as written, return `BLOCKED` with the reason instead of improvising around it. Examples: a dependency is not merged, or a contradiction you cannot rule on.

## Task reviewer

You review one task's branch in its worktree. You change nothing: no edits, no commits, no pushes, no subagents.

Read, in this order:
1. the plan's header sections (Global Constraints, Deviations, Review Focus) and the task's section, which holds the requirement with its exact names and values;
2. the spec sections the task cites;
3. the implementer's report, including any "Fix round" sections;
4. the diff: `git log --oneline BASE..HEAD`, `git diff --stat BASE..HEAD` and `git diff -U10 BASE..HEAD`;
5. the surrounding code you need.

Judge two things; both are required.
- **Spec compliance.** Every requirement of the task, and of the plan's Global Constraints that apply, is implemented with the exact names, signatures, values and wording it fixes. Nothing beyond it was built (YAGNI; the MVP rule). The tests the task names exist and assert what the task says. Any deviation the implementer recorded is justified by the spec. Mark anything you cannot verify from the diff and the files as "cannot verify", with what would verify it.
- **Quality.** Check each of these:
  - tests that fail before the change and pass after it;
  - no flaky timing and no network dependency;
  - the repository's conventions (read neighbouring code);
  - row-level security, an isolation factory and `SHARED_TABLES` discipline for any new table;
  - no plaintext personal data or secret stored, logged, returned in a machine view or echoed in an error;
  - the migration chain intact;
  - the documentation the task names updated;
  - conventional commit messages with the trailers;
  - a PR body that is honest about what is included.

Trust the report's test evidence unless it is missing or contradicts the diff; then run the targeted tests yourself with `--no-cov`. Never run the whole suite.

Give every finding a severity, the file and line, and what to change:
- **Critical:** wrong behaviour, a leak, a missing requirement, or a gate that will fail.
- **Important:** a requirement met loosely, a weak test, or a convention CI will reject.
- **Minor:** style or wording.

Say `spec_ok` true only when every requirement is met.

## Fixer

1. Read the plan task (header sections plus the task's section), the report file and the findings in your prompt. Work in the worktree your prompt names.
2. Fix every finding on the list, or explain in the report why it is wrong, citing the plan or the spec.
3. Re-run the tests that cover the amended code and the four lint and type gates.
4. Append a "Fix round" section to the same report file, with what you changed and the commands and their result lines.
5. Commit with a conventional message and the trailers, and push.

Never amend or rewrite existing commits. No subagents. Do not mark the PR ready or merge it.

## Finisher

The task's review is clean. In the worktree, run the full gate exactly as CI does: `uv sync --locked`, then `make ci` (allow up to 25 minutes). When the branch touches `console/`, also run the console gates: `cd console && npm ci && npm run typecheck && npm run lint && npm test && npm run build`.

If anything is red, fix it with the smallest change that keeps the task's behaviour. A coverage shortfall is fixed by testing the uncovered lines of this branch, never by excluding them. Re-run the gates, commit with a conventional message and the trailers, and push.

Then refresh the PR body if needed (`gh pr edit <number> --body-file`) and mark the PR ready (`gh pr ready <number>`). Report the result line of every gate, the coverage total, the test counts, the fix commits, and `git status --short`, which must be clean. Never amend or rewrite commits; never merge; no subagents.

## Merger

You merge one ready pull request. Work from its worktree, starting every shell command with `cd <worktree> &&`.

1. **Check the PR.** `gh pr view <number> --json state,isDraft,mergeable,mergeStateStatus,headRefName,baseRefName` must show an open, non-draft PR against `main`. If it is already MERGED, do steps 5 to 7 only.
2. **Wait for the checks.** Run `gh pr checks <number> --watch --interval 30` and allow up to 40 minutes. If a check fails, read its log (`gh run view <run id> --log-failed`) and fix the cause in the worktree with the smallest change. Commit with a conventional message and the trailers, push, and wait again. After two such rounds, report `BLOCKED` with the failing check's log excerpt.
3. **Catch up with main.** If `main` moved since the branch was cut, merge it in with `git merge --no-edit origin/main`; `git fetch -q origin && git merge-base --is-ancestor origin/main HEAD` fails in that case. Resolve conflicts faithfully to both sides and never drop the other side's changes. For Alembic, keep a single chain and point `down_revision` at the new head; `console/src/api/schema.d.ts` is regenerated, never merged by hand. Run `make lint types vulture importlint` and the targeted tests of the files you resolved, push, and wait for the checks again.
4. **Merge** with a merge commit and delete the remote branch: `gh pr merge <number> --merge --delete-branch`. Never `--squash`, never `--rebase`, never `--admin`.
5. **Update Notion:** set the task's page to Done, as the agreement says.
6. **Verify:** `gh pr view <number> --json state,mergeCommit` shows MERGED and the merge commit; check `git fetch -q origin && git log --oneline -1 origin/main`.
7. **Clean up:** `cd /Users/omaribannez/code/xolotl && git worktree remove --force <worktree> && git worktree prune`, then `git branch -D <branch>` if it still exists locally. Never touch another task's worktree.

Return the state, the merge commit sha, how many check rounds ran, and anything you had to fix or resolve. Never force-push, never rewrite history, no subagents.
