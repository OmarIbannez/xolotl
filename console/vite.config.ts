import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API listens on :8000 by default. If that port is busy, start it elsewhere and run
// `API_PROXY_TARGET=http://localhost:8010 npm run dev`.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    // The API serves everything under /v1: routes, the OpenAPI document and the docs.
    proxy: { '/v1': apiTarget },
  },
  build: {
    outDir: 'dist',
    rolldownOptions: {
      output: {
        // Long-lived libraries get chunks of their own, so a release that only changes the
        // app's code leaves them cached in browsers, and no chunk comes near Vite's 500 kB
        // warning. The feature pages are already lazy routes (App.tsx). `$initial` keeps a
        // group to what the first screen loads: a library that only a lazy page uses (the QR
        // code of the Security page) stays with that page instead of joining an eager chunk.
        codeSplitting: {
          groups: [
            {
              name: 'react',
              test: /node_modules[\\/](?:react|react-dom|scheduler)[\\/]/,
              tags: ['$initial'],
              priority: 3,
            },
            { name: 'router', test: /node_modules[\\/]react-router/, tags: ['$initial'], priority: 3 },
            {
              name: 'forms',
              test: /node_modules[\\/](?:react-hook-form|@hookform|zod)[\\/]/,
              tags: ['$initial'],
              priority: 3,
            },
            { name: 'vendor', test: /node_modules/, tags: ['$initial'], priority: 1 },
          ],
        },
      },
    },
  },
  test: {
    // Logic tests (*.test.ts: the API client, the session store, routing rules, catalogues)
    // need no DOM and run in node. Component tests (*.test.tsx) opt in to jsdom with a
    // `// @vitest-environment jsdom` comment on their first line.
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    testTimeout: 15_000,
    // Dates and hours are read in one fixed zone, whatever the machine running the suite.
    env: { TZ: 'America/Mexico_City' },
  },
})
