# Findings

- Installed Router diverged from repository: local inbox bypasses broker/index refresh; repository has memory_service/mutation_service.
- Preserve installed todo_time.py semantics: capture time is not scheduled time.
- Biography: 10 cards all verified without review receipts; template defaults verified; dashboard counts all; iCal links to unrelated ITT.
- Investment already owns ledger validation and must retain it; a capture is a pending input, never a booked transaction.
- Existing Context store has reviewed definitions: merge rather than replace.
- Repository has pre-existing uncommitted hardlink-read fixes; preserve and verify them.

Runtime evidence: full index sync 15.12 seconds for 2,370 readable Markdown files, no skipped source. Five Context briefs in a three-worker smoke run took 12.44–14.21 seconds each. This is a concurrent machine-local sample, not a latency SLA. Prefer scoped/incremental verified snapshots next; do not add a database to solve repeated scans. Local CLI deliberately delegates to canonical working-tree code; future changes must run regression before use.

Performance profile: 18.98s under cProfile; discovery 12.07s, scan 6.80s, 342,044 extract_tags calls for 2,370 notes. Timings are instrumented, not the prior unprofiled latency sample. Backup: C:\Users\ZOZN109\.codex\backups\router-performance-20261025-105442

Verified process-level comparison: Biography median 10511.050 ms -> 979.634 ms (90.68% lower; 10.73x). Three samples each; all five Context evidence/source-count/state results identical. Explicit module-path assertions protect the baseline.

Index baseline on real 2,370-note corpus: first full scan 8.627s, new SQLite build 10.802s, second full scan 7.635s; source generations equal. Derived database built only under a temporary directory. Backup: ~/.codex/backups/incremental-index-20261025-111011/.
