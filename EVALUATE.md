# KnowledgeRouter: Evaluation & Quality Criteria

## 1. Quality metrics

| Dimension | Target | Method |
|---|---|---|
| Warm search latency | p95 < 50 ms | Repeated warm FTS queries on a 1,500+ note Vault |
| Lexical Recall@5 | = 1.00 on the fixture | Automated cases with known targets (lexical + paraphrase) |
| MRR | = 1.00 on the fixture | Rank of the first correct source |
| Abstention precision | = 1.00 | Non-existent terms must return an explicit evidence gap, never a fabricated answer |
| Citation coverage | = 1.00 | Every returned excerpt carries path, lines, source SHA-256 and generation |
| Zero-corruption | 100% | Before/after SHA-256 snapshot of every Vault file |
| Write safety | 100% | Cross-process append exclusion, parent-replacement refusal, crash recovery, tamper refusal |

Process-inclusive timings are reported separately from warm in-process timings. One machine's
timing is never presented as a universal SLA; the broad live Context/project brief has a recorded
baseline only.

## 2. Test fixtures & synthetic scenarios

1. **PARA / Johnny Decimal hybrid** (`100_Project`, `200_Area`, `300_Resources`, `400_Archives`, `000_Inbox`).
2. **Pure daily / chronological** (`Daily/`, `Journals/`).
3. **Zettelkasten** (`Permanent/`, `Literature/`, `Fleeting/`).
4. **Unstructured / flat** (root directory of Markdown files).
5. **Functional Contexts**: Daily, Contact/entity, Project, Research, Trip, Job and custom equipment
   types exercised through one lifecycle core.

## 3. How to reproduce

```bash
python -m pytest -q                      # full suite
python -m pytest -q tests/test_universal_router.py
python -m pytest -q tests/test_effect_performance.py
python -m ruff check scripts tests integrations
```

## 4. Recorded results

- **Full local suite: 183 tests passed**, ruff and bytecode compilation clean. The single warning is
  an intentional duplicate ZIP entry used by the backup tamper-refusal fixture.
- **Read-only relationship graph**: `contexts links` reuses the stored graph in ~0.4 s versus a
  ~10 s cold rebuild on a 2,100+ note Vault. It reports topic anchors, related Contexts (overlap
  aware), hub notes and bridges. Assets resolve by name so `![[image.png]]` embeds are counted as
  `asset_links`, never as broken note edges. A live sample Vault showed 2,143 notes / 605 note
  edges / 30 MOCs; ~6,200 wikilinks resolved to nothing, which is a property of that Vault rather
  than a resolver defect.
- **Context filters + graph affinity**: `include`/`exclude`/`tags` narrow membership identically
  across discovery, briefs, drift bindings and `contexts links`; a brief re-ranks in-scope
  passages toward MOC-anchor linked notes when a stored graph exists. Applied to a live Vault,
  excluding four off-topic notes dropped a broad Context from 338 to 334 sources and removed a
  same-folder noise passage from the top of the brief, with 57 affinity-linked sources reported.
- **Compiled Context cards**: `contexts compile` assembles a cited scaffold without writing
  anything, and `--stage` freezes an external broker plan without touching the Vault. Tests
  prove staging creates a plan only, the target directory must pre-exist, and a follow-up commit
  adds exactly the compiled card while leaving every other source byte-identical. A live
  read-only bundle on a 334-source Context completed in ~21 s and returned recorded state, open
  follow-ups, eight anchors and eight cited passages.
- **Deposition loop**: `contexts review` aggregates maintenance-due, due follow-ups and
  maintained-scope doctor findings into a bounded, occurrence-ranked agenda and records each run
  in an external ledger with a stable fingerprint. Tests cover overdue maintenance, scoped-only
  quality filtering, resolved-item deltas, byte-identical Vault sources, `--no-record`, stale
  compiled-card detection and the CLI path. A live run collapsed 6,244 raw link occurrences into
  15 ranked distinct targets and reported the delta against the previous ledger entry.
- **Real-Vault benchmark** (local, historical): 22 retrieval cases over a 1,500+ note Vault,
  1,012 latency samples, Recall@5 = MRR = abstention precision = citation coverage = 1.0,
  warm p50 ≈ 4.87 ms, p95 ≈ 32.33 ms. Reproduce with your own fixture:

  ```bash
  python scripts/evaluate.py --fixture path/to/cases.json --index path/to/index.<generation>.db
  ```

  A fixture is `{"limit", "excerpt_chars", "thresholds", "cases": [{"id", "query",
  "expected_paths" | "abstain", "difficulty"}]}` and must contain both positive and abstention
  cases.
- **Representative effect test** (`tests/test_effect_performance.py`): isolated temporary Vault with
  Daily, Contact/entity, Project and old-state archive sources; asserts all four quality metrics at
  1.0 across three warm repetitions and measures process-inclusive `search`/`contexts brief` timings
  without asserting a machine-specific SLA.
- **Packaged-copy verification**: the suite passes independently in its own directory (153 tests),
  and a standalone CLI `attach` + `search` smoke test in an isolated temp state root returns a
  cited source with `path`, lines, `source_sha256`, `index_generation` and `stale=false`.
- **Cross-process write safety**: Windows append obtains an OS handle denying competing writers and
  pins parent directories against replacement; non-Windows append fails closed.
- **Recovery**: allowlisted checkpoints/backups with per-entry SHA-256 manifests and sidecars;
  restore is verification-first, isolated-target-only and exclusive-create, and never overwrites
  live state.

These are local measured results, not a promise about other machines. Re-run the suite on your own
hardware before relying on a latency number.
