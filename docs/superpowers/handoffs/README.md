# Parallel work packages

Prompts for engineers who want to work alongside the main SP-02/SP-03 line with their own AI coding agent. Each package is a **pure library**: no database tables, no migrations, no routes, so it cannot collide with the sub-projects being merged on `main`.

| Package | Prompt | Notion task | Branch prefix |
|---|---|---|---|
| Reporting serializers (Formato 41 and aviso XML) | [reporting-serializers.md](reporting-serializers.md) | "Reporting serializers library (SP-05 head start)" | `feat/reporting-serializers-*` |
| Screening library (list parsers and name matcher) | [screening-library.md](screening-library.md) | "Screening library: list parsers and name matcher (SP-03 head start)" | `feat/screening-lib-*` |

How to use one: paste the whole prompt file into your agent as its first message, in a fresh clone of `git@github.com:OmarIbannez/xolotl.git`. The prompt tells the agent what to read, where the code goes, the gates CI enforces and how to deliver PRs. Assign yourself the Notion task first so two people do not take the same package.
