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

- **Full local suite: 188 tests passed**, ruff and bytecode compilation clean. The single warning is
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
- **External compiled area**: `contexts compile --save` writes the reviewed card outside the Vault
  (default `<vault>_compiled/`) with no write policy, refusing any target inside the Vault or an
  Obsidian Vault. Tests prove the card lands outside the Vault, the Vault stays byte-identical,
  overwrite requires an explicit flag and unsafe names are rejected.
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

## Link resolution and review-ledger integrity

Two defect classes were reproduced and fixed, and one structural duplication was removed.

**Display truncation leaked into the review ledger.** `_quality_items` sliced its candidate list to
the display limit before the ledger ids, the fingerprint and the deltas were computed. With the
notes completely unchanged, lowering `--limit` from 20 to 1 therefore reported every omitted item as
`resolved_item_ids`. The helper now returns the full list; truncation applies to the returned
`items` page only and is disclosed as `items_truncated`.

**An empty agenda could be reported as `clear` over an incomplete basis.** The status was
`attention if items else clear`. It is now `attention` when items exist, `clear` only when the basis
is complete, and `incomplete` otherwise, naming `no_registered_contexts`, `scan_skipped_sources`,
`poll_incomplete` or `poll_truncated`. Every ledger entry stores that `basis`; a resolution delta
additionally requires a complete basis on both sides, so a pre-fix entry yields `comparable: false`,
a named `incomparable_reason`, and `resolved_suppressed` instead of an invented resolution.

**One resolver instead of two, and a corrected model.** `vault_doctor` and `link_graph` each carried
their own copy of the union-candidates-then-count logic, which could disagree about the same link.
`link_resolver.py` now owns resolution. The first version of it used a "relative path → vault path →
unique basename → unique alias" ladder, which was then **wrong and corrected** after checking
Obsidian's official help: an explicit path must not be overridden by a bare-name match elsewhere,
and the documented default is "only the filename if it is unique in the vault". The implemented rule
is therefore:

- a target containing a path separator resolves **as a path** — first relative to the linking note,
  then from the vault root;
- a bare target resolves by **unique filename**, then by unique alias;
- non-Markdown targets must carry their extension.

Two cases are deliberately *not* decided, because the documentation does not cover them:
`ambiguous_proximity_unique` counts ambiguous targets a proximity preference would collapse, and
`unresolved_with_unique_basename_fallback` counts unresolved path targets whose basename is unique
elsewhere. Both are reported as information; the verdicts stay ambiguous and unresolved.
`tests/test_link_resolver.py` asserts that the two consumers cannot diverge.

**Conservative triage.** Every broken occurrence now carries `triage_hint`, `triage_reason` and
`confidence: hint`, with the original totals retained as `broken_link_occurrences` and
`broken_by_hint`. No hint is a verdict: a Zotero-generated or template source does not prove a
reference is irrelevant, and a plausible name such as `CCS` is deliberately left `needs_review`
rather than promoted to actionable.

Read-only acceptance on a ~2,150-note Obsidian Vault:

- 7,158 wikilink occurrences = 897 resolved + 6,251 broken + 10 ambiguous (no occurrence dropped)
- broken links that a real file could have served: **0** (no missed resolutions)
- resolved destinations that are not real Vault paths: **0**
- ambiguous verdicts carrying fewer than two candidates: **0**
- doctor and link graph agree exactly on broken and ambiguous counts
- triage re-attributes 5,537 of 6,251 unresolved occurrences (~89%) to a single auto-generated
  Zotero note directory, leaving 562 `needs_review`, 100 asset references and 52 template
  placeholders; the full total is still reported
- 13 unresolved path targets are flagged as undocumented-basename-fallback cases and 4 ambiguities
  as proximity-collapsible; neither is resolved
- resolution mix: 798 unique filename, 106 vault path, 3 alias, 0 note-relative
- the agenda surfaces the composition via `counts_by_hint`, detects a stale externally deposited
  compiled card, and two consecutive runs report no new and no resolved items, with the earlier
  pre-fix entry correctly refusing comparison instead of claiming resolutions

Suite: **207 tests**, ruff clean. The ambiguous and basename-fallback cases remain undecided by
design, so any count that depends on them is a report rather than a verdict, and the triage ranking
is a convenience rather than a completeness proof.

