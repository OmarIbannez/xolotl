export const meta = {
  name: 'sp04-execute',
  description: 'Execute SP-04 plan tasks as one PR each: implementer, reviewer with up to three fix rounds, finisher (make ci, PR ready) and merger, scheduled by dependency',
  whenToUse: 'Executing docs/superpowers/plans/2026-10-20-sp04[abc]-*.md with one pull request per task, merged when green',
  phases: [
    { title: 'Implement', detail: 'one fresh implementer per task, in its own worktree and draft PR' },
    { title: 'Review', detail: 'task reviewer, fix rounds (at most 3), re-review' },
    { title: 'Finish', detail: 'full make ci and console gates, PR marked ready' },
    { title: 'Merge', detail: 'wait for checks, merge origin/main if behind, merge commit, Notion Done' },
  ],
}

// args (see README.md):
//   tasks     the array in tasks.json (required)
//   reports   a writable directory for the agents' report files, e.g. a scratchpad folder (required)
//   trailers  the exact trailer lines every commit message ends with (required)
//   prFooter  the exact footer every pull request body ends with (required)
//   targets   task ids to drive; their unmerged dependencies run first (default: every task)
//   done      task ids already merged on main, external prerequisites included (default: none)
//   kit       this directory as an absolute path (default: the main checkout's copy)
//   worktrees where task worktrees live (default: /Users/omaribannez/code/xolotl-worktrees)
//   finishModel, mergeModel  model for the mechanical finish and merge steps (default: sonnet)
const A = args || {}
if (!Array.isArray(A.tasks) || !A.reports || !A.trailers || !A.prFooter) {
  throw new Error('args needs tasks (the array in tasks.json), reports, trailers and prFooter; see README.md')
}
const KIT = A.kit || '/Users/omaribannez/code/xolotl/docs/superpowers/execution/sp04'
const WORKTREES = A.worktrees || '/Users/omaribannez/code/xolotl-worktrees'
const REPORTS = A.reports
const FINISH_MODEL = A.finishModel || 'sonnet'
const MERGE_MODEL = A.mergeModel || 'sonnet'
const tasks = A.tasks
const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
const done = new Set(A.done || [])
// Rows with "external": true are prerequisites built outside SP-04 (none at hand-off; the mechanism is kept). They are never
// implemented here: one listed in `done` counts as merged; any other is SKIPPED, and so are the tasks that need it.
const targets = A.targets || tasks.filter(t => !t.external).map(t => t.id)
for (const id of targets) {
  if (!byId[id]) throw new Error(`unknown task id in targets: ${id}`)
  if (byId[id].external) throw new Error(`${id} is an external prerequisite; it cannot be a target`)
}
for (const id of done) if (!byId[id]) throw new Error(`unknown task id in done: ${id}`)
for (const t of tasks) for (const d of t.deps) if (!byId[d]) throw new Error(`${t.id} depends on unknown task ${d}`)

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['DONE', 'DONE_WITH_CONCERNS', 'BLOCKED', 'NEEDS_CONTEXT'] },
    branch: { type: 'string' }, pr_number: { type: 'integer' }, pr_url: { type: 'string' },
    base: { type: 'string' }, head: { type: 'string' }, tests: { type: 'string' }, concerns: { type: 'string' },
  },
  required: ['status', 'branch', 'base', 'head', 'tests', 'concerns'],
}
const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    spec_ok: { type: 'boolean' },
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['Critical', 'Important', 'Minor'] },
      file: { type: 'string' }, line: { type: 'integer' }, title: { type: 'string' }, detail: { type: 'string' },
    }, required: ['severity', 'file', 'title', 'detail'] } },
    cannot_verify: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['spec_ok', 'findings', 'summary'],
}
const FIX_SCHEMA = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['DONE', 'BLOCKED'] }, head: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } }, tests: { type: 'string' }, disputed: { type: 'string' },
  },
  required: ['status', 'head', 'commits', 'tests'],
}
const FINISH_SCHEMA = {
  type: 'object',
  properties: {
    green: { type: 'boolean' }, head: { type: 'string' }, fix_commits: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' }, status_clean: { type: 'boolean' },
  },
  required: ['green', 'head', 'summary', 'status_clean'],
}
const MERGE_SCHEMA = {
  type: 'object',
  properties: {
    state: { type: 'string', enum: ['MERGED', 'BLOCKED'] }, merge_commit: { type: 'string' },
    check_rounds: { type: 'integer' }, notes: { type: 'string' },
  },
  required: ['state', 'notes'],
}

const attribution = `End every commit message with exactly these trailer lines:\n${A.trailers}\nEnd every pull request body with exactly this footer:\n${A.prFooter}`
const where = t => `Worktree: ${WORKTREES}/${t.slug}. Branch: ${t.branch}. Notion page id: ${t.notion}.`
const consoleNote = t => (t.console ? ' This task touches console/: the console gates in the agreement must pass too.' : '')
const deps = t => (t.deps.length ? t.deps.join(', ') : 'none')

const implPrompt = t => `You are the implementer for SP-04 task ${t.id} ("${t.title}") of plan ${t.plan} in the xolotl repository.
Read ${KIT}/agreement.md (binding), then the "Implementer" section of ${KIT}/roles.md, then the plan file ${t.plan} inside your worktree: its header sections and the section "### Task ${t.id}:" only, plus the spec sections that task cites and the files its Files list names.
${where(t)} Base: origin/main, where this task's dependencies (${deps(t)}) are already merged.${consoleNote(t)}
Report file: ${REPORTS}/${t.id}-implement.md.
${attribution}
Return the structured result: status, branch, pr_number, pr_url, base (the commit the worktree started from), head (your last commit), a one-line test summary, concerns.`

const reviewPrompt = (t, impl, head, round) => `You are the task reviewer for SP-04 task ${t.id} ("${t.title}") of plan ${t.plan}${round ? ` (re-review after fix round ${round})` : ''}.
Read ${KIT}/agreement.md, then the "Task reviewer" section of ${KIT}/roles.md: it is your rubric. ${where(t)} BASE ${impl.base}; HEAD ${head}. The implementer's report is ${REPORTS}/${t.id}-implement.md${round ? '; it has a "Fix round" section per earlier round: confirm each earlier finding is addressed or justifiably disputed, and look for regressions in the fix commits' : ''}.
Return spec_ok, findings (severity, file, line, title, detail saying what to change), cannot_verify, summary.`

const fixPrompt = (t, findings, round) => `You are the fixer for SP-04 task ${t.id} ("${t.title}") of plan ${t.plan}, fix round ${round} of 3.
Read ${KIT}/agreement.md, then the "Fixer" section of ${KIT}/roles.md. ${where(t)} Report file: ${REPORTS}/${t.id}-implement.md. Plan section: "### Task ${t.id}:".
Findings to address (Critical and Important must be fixed or disputed with a citation; Minor ones when cheap):
${JSON.stringify(findings, null, 1)}
${attribution}
Return status, head (your last commit), commits, a one-line test summary, disputed (findings you did not apply and why).`

const finishPrompt = (t, impl) => `You are the finisher for SP-04 task ${t.id} ("${t.title}").
Read ${KIT}/agreement.md, then the "Finisher" section of ${KIT}/roles.md. ${where(t)} PR: ${impl.pr_url || '#' + impl.pr_number}.${consoleNote(t)}
${attribution}
Return green, head, fix_commits, summary (the result line of every gate, the coverage total, the test counts), status_clean.`

const mergePrompt = (t, impl) => `You are the merger for SP-04 task ${t.id} ("${t.title}").
Read ${KIT}/agreement.md, then the "Merger" section of ${KIT}/roles.md. PR number ${impl.pr_number} (${impl.pr_url || ''}). ${where(t)}
${attribution}
Return state (MERGED or BLOCKED), merge_commit, check_rounds, notes (what you fixed or resolved, or why it is blocked).`

const results = {}
const running = {}
function run(id) {
  if (done.has(id)) return Promise.resolve({ id, state: 'MERGED', stage: 'already merged' })
  if (running[id]) return running[id]
  if (byId[id].external) {
    log(`${id}: skipped, external prerequisite not merged (add it to done once it is on main)`)
    results[id] = { id, state: 'SKIPPED', stage: 'external', note: 'external prerequisite not merged' }
    running[id] = Promise.resolve(results[id])
    return running[id]
  }
  running[id] = (async () => {
    const t = byId[id]
    const depResults = await Promise.all(t.deps.map(run))
    const blocked = t.deps.filter((d, i) => !depResults[i] || depResults[i].state !== 'MERGED')
    if (blocked.length) {
      log(`${id}: skipped, dependencies not merged: ${blocked.join(', ')}`)
      return (results[id] = { id, state: 'SKIPPED', stage: 'deps', note: 'dependencies not merged: ' + blocked.join(', ') })
    }
    log(`${id}: implementing (${t.title})`)
    const impl = await agent(implPrompt(t), { label: `impl:${id}`, phase: 'Implement', schema: IMPL_SCHEMA })
    if (!impl) return (results[id] = { id, state: 'FAILED', stage: 'implement', note: 'implementer returned nothing (usage limit or error); re-run to resume' })
    if (impl.status === 'BLOCKED' || impl.status === 'NEEDS_CONTEXT') {
      log(`${id}: implementer ${impl.status}: ${impl.concerns}`)
      return (results[id] = { id, state: 'BLOCKED', stage: 'implement', impl })
    }
    let head = impl.head
    let round = 0
    let review = null
    const fixes = []
    for (;;) {
      review = await agent(reviewPrompt(t, impl, head, round), { label: `review:${id}${round ? '#' + round : ''}`, phase: 'Review', schema: REVIEW_SCHEMA })
      if (!review) return (results[id] = { id, state: 'FAILED', stage: 'review', impl, fixes })
      const open = review.findings.filter(f => f.severity !== 'Minor')
      if (review.spec_ok && open.length === 0) break
      if (round >= 3) {
        if (open.some(f => f.severity === 'Critical')) {
          log(`${id}: a Critical finding is still open after 3 fix rounds; not merging`)
          return (results[id] = { id, state: 'BLOCKED', stage: 'review', impl, review, fixes })
        }
        log(`${id}: ${open.length} Important finding(s) still open after 3 fix rounds; going to the gate, listed in the result`)
        break
      }
      round += 1
      log(`${id}: fix round ${round} (${open.length} open finding(s))`)
      const fix = await agent(fixPrompt(t, review.findings, round), { label: `fix:${id}#${round}`, phase: 'Review', schema: FIX_SCHEMA })
      if (!fix) return (results[id] = { id, state: 'FAILED', stage: 'fix', impl, review, fixes })
      fixes.push(fix)
      if (fix.head) head = fix.head
      if (fix.status === 'BLOCKED') {
        log(`${id}: fixer blocked: ${fix.disputed || ''}`)
        return (results[id] = { id, state: 'BLOCKED', stage: 'fix', impl, review, fixes })
      }
    }
    const finish = await agent(finishPrompt(t, impl), { label: `finish:${id}`, phase: 'Finish', schema: FINISH_SCHEMA, model: FINISH_MODEL })
    if (!finish) return (results[id] = { id, state: 'FAILED', stage: 'finish', impl, review, fixes })
    if (!finish.green || !finish.status_clean) {
      log(`${id}: gate not green; not merging`)
      return (results[id] = { id, state: 'BLOCKED', stage: 'finish', impl, review, fixes, finish })
    }
    const merge = await agent(mergePrompt(t, impl), { label: `merge:${id}`, phase: 'Merge', schema: MERGE_SCHEMA, model: MERGE_MODEL })
    if (!merge) return (results[id] = { id, state: 'FAILED', stage: 'merge', impl, review, fixes, finish })
    log(`${id}: ${merge.state} ${merge.merge_commit || ''}${merge.state === 'MERGED' ? '' : ' ' + merge.notes}`)
    return (results[id] = { id, state: merge.state, stage: 'merge', pr: impl.pr_url, impl, review, fixes, finish, merge })
  })()
  return running[id]
}

await Promise.all(targets.map(run))
const table = Object.values(results).map(r => ({
  id: r.id, state: r.state, stage: r.stage,
  pr: r.pr || (r.impl && r.impl.pr_url), merge: r.merge && r.merge.merge_commit,
  open_findings: r.review ? r.review.findings.filter(f => f.severity !== 'Minor').length : undefined,
  note: r.note || (r.merge && r.merge.state !== 'MERGED' ? r.merge.notes : undefined) || (r.impl && r.impl.concerns),
}))
log(`done: ${table.filter(r => r.state === 'MERGED').length} merged of ${table.length} driven`)
return { table, details: results }
