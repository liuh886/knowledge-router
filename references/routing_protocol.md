# Vault-adaptive routing protocol

Classification is subordinate to the Vault's detected structure. A semantic category never
authorizes creation of a new directory.

Routing precedence:

1. A named active project plus progress/blocker/milestone signals targets existing
   `PROGRESS.md`; task/todo/issue signals target existing `TASKS.md`. Missing state files become
   new files inside the existing project directory.
2. Daily, meeting, and weekly signals target an existing daily/weekly directory when present,
   otherwise the detected log directory.
3. A named area targets its existing area directory.
4. Reference signals target the detected resource directory.
5. Ambiguous or fleeting input targets the detected inbox; if no inbox exists, use the Vault
   root rather than inventing one.

Creation names are local-date slugs. Existing names receive a collision suffix frozen into the
reviewable plan before commit.
Tags in YAML omit the Markdown `#` prefix. Low-confidence plans remain previews unless capture
was explicitly configured for automatic commit.
