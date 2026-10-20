// Checks tasks.json: unique ids, every dep exists, the graph is acyclic, internal rows are complete.
// Prints the longest chain, the first wave for a given `done` list, and which tasks wait on each external row.
// Usage: node check-graph.mjs [comma-separated done ids]   e.g. node check-graph.mjs A1,A3
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const tasks = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'tasks.json'), 'utf8'))
const done = new Set((process.argv[2] || '').split(',').filter(Boolean))
const byId = new Map()
const errors = []
for (const t of tasks) {
  if (byId.has(t.id)) errors.push(`duplicate id ${t.id}`)
  byId.set(t.id, t)
}
for (const t of tasks) {
  for (const d of t.deps) if (!byId.has(d)) errors.push(`${t.id} depends on unknown ${d}`)
  if (t.external) continue
  for (const k of ['xol', 'plan', 'title', 'slug', 'branch', 'notion', 'notion_url', 'console']) {
    if (t[k] === undefined || t[k] === '') errors.push(`${t.id} lacks ${k}`)
  }
  if (t.branch !== `feat/${t.slug}`) errors.push(`${t.id} branch is not feat/<slug>`)
}
for (const d of done) if (!byId.has(d)) errors.push(`unknown id in done: ${d}`)

// Depth-first search for cycles and the longest chain (counted in tasks, externals included).
const state = new Map()
const best = new Map()
function visit(id, path) {
  if (state.get(id) === 'open') { errors.push(`cycle: ${[...path, id].join(' -> ')}`); return [] }
  if (state.get(id) === 'closed') return best.get(id)
  state.set(id, 'open')
  let longest = []
  for (const d of byId.get(id).deps) {
    if (!byId.has(d)) continue
    const chain = visit(d, [...path, id])
    if (chain.length > longest.length) longest = chain
  }
  state.set(id, 'closed')
  best.set(id, [...longest, id])
  return best.get(id)
}
let longest = []
for (const t of tasks) { const c = visit(t.id, []); if (c.length > longest.length) longest = c }

if (errors.length) { console.error(errors.join('\n')); process.exit(1) }
const internal = tasks.filter(t => !t.external)
const externals = tasks.filter(t => t.external)
console.log(`ok: ${internal.length} tasks, ${externals.length} external prerequisites, acyclic, every dep exists`)
console.log(`longest chain (${longest.length}): ${longest.join(' -> ')}`)
const internalChain = longest.filter(id => !byId.get(id).external)
console.log(`  of which our tasks (${internalChain.length}): ${internalChain.join(' -> ')}`)

// Waves: a task is runnable when every dep is done or in an earlier wave; an external not in done never runs.
const reached = new Set(done)
const waves = []
for (;;) {
  const wave = internal.filter(t => !reached.has(t.id) && t.deps.every(d => reached.has(d))).map(t => t.id)
  if (!wave.length) break
  waves.push(wave)
  for (const id of wave) reached.add(id)
}
console.log(`done: [${[...done].join(', ')}]`)
console.log(`first wave: ${waves.length ? waves[0].join(', ') : '(none)'}`)
waves.forEach((w, i) => console.log(`  wave ${i + 1}: ${w.join(', ')}`))
const stuck = internal.filter(t => !reached.has(t.id)).map(t => t.id)
console.log(`waiting on an external prerequisite: ${stuck.length ? stuck.join(', ') : '(none)'}`)
for (const e of externals) {
  const direct = internal.filter(t => t.deps.includes(e.id)).map(t => t.id)
  console.log(`  ${e.id}${done.has(e.id) ? ' (done)' : ''}: direct dependents ${direct.join(', ') || '(none)'}`)
}
