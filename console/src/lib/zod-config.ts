import { z } from 'zod'

/**
 * Keep zod from compiling its parsers at run time.
 *
 * Zod builds a faster parser for each object schema with `new Function`, after probing
 * once whether the engine allows it. The API serves the console under a
 * Content-Security-Policy without 'unsafe-eval', so the browser blocks the probe and
 * reports a violation (and a red console error) on every page load, although zod catches
 * the failure and falls back to its plain parser. `jitless` skips the probe. The forms here
 * validate a handful of fields, so the plain parser costs nothing noticeable.
 *
 * The flag is read when a schema is created, and the forms create theirs when their
 * modules load, so `main.tsx` imports this module before everything else.
 */
z.config({ jitless: true })
