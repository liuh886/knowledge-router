# KnowledgeRouter: Evaluation & Quality Criteria

## 2026-10-23 — architecture 0.2 convergence

Baseline: `e959dce`, 292 passing tests and 39 ruff findings. Verification is on Windows with
Python 3.12.12, isolated temporary Vaults and state roots; no production Vault or Ductor activation.

- Final local regression: **320 passed in 80.39 seconds**. Ruff (`scripts tests integrations`)
  and bytecode compilation passed. Formatting was applied to changed Python files.
- Regression evidence covers all reported defects: default read-only rejection for inbox/contact/
  triage, native exclusive handles, policy revocation after waiting, CAS preservation of human
  edits, prefix/BOM/CRLF retention, duplicate-name/alias refusal and exact-path selection, explicit
  nonrecursive/repeatable buffer import, preserved original entry/completion, immediate cited recall,
  and pending-index replay without duplicate source writes. The legacy Python capture helper also
  delegates to the shared commit/index boundary and is covered by immediate-recall acceptance.
- New edit capability: schema 3 retains full original bytes; append-create cannot authorize a full
  edit. Tests cover short writes/refused blind retries, completed-write interruption reconciliation
  and historical schema 2 checksum/replay compatibility. Full edits are checked in-place operations,
  not crash-atomic swaps; partial sources require manual reconciliation using retained originals.
- Budget regressions execute the README brief command and measure the complete formatted JSON,
  including citations and Markdown. Numbers are character estimates, not tokenizer guarantees.
- Runtime-backup regression verifies workflow intents, frozen original material and the mutation
  journal remain in the archive. Source files are never traversed by runtime backup, though frozen
  edit plans can contain original source content. Published external artifacts need their own file backup.
- A clean wheel was built without runtime dependencies and installed in a fresh virtual environment;
  installed version/metadata, `kr` scaffold, capture, immediate search and budgeted brief passed.
  The final expanded installation smoke also exercises contact prefix preservation, frozen reviewed
  edits and completed buffer import/replay. Temporary installation environments are removed afterward.
- Windows Python 3.11/3.12/3.13 CI is configured. Only the local 3.12 run is claimed here; the remote
  matrix, real-Vault knowledge-update loop and host activation remain unverified operational gates.

Historical results below retain their original dates and scope; they are not current release totals.

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

**Closing the loop: orphans and convergence.** Reporting a broken link says "this points at
nothing"; it says nothing about the other half — notes nothing points at. `contexts review` now
includes in-scope orphans as `orphan_note` items, so an isolated note inside a maintained scope
becomes visible, and `counts_by_code` plus `--code` expose the composition without changing what
the ledger records.

Repeating the same missing name was still N findings for one decision. `contexts converge` groups
unresolved targets case-insensitively and attaches the evidence, then suggests one of three steps:
a unique same-name variant (`check_naming_variant`), nothing similar (`create_or_relink`), or a name
too generic to guess (`needs_judgement`). On the reference Vault this turns 562 `needs_review`
occurrences over 186 distinct names into 146 in-scope targets: 117 `create_or_relink`, 4
naming-variant, 25 `needs_judgement`, with 2,112 machine-generated targets (5,534 occurrences)
excluded but counted. The single largest case, `CCS`/`ccs` at 269 occurrences, collapses to one
decision.

The only write path this unlocks goes through the existing capture broker: `converge --create`
freezes a plan and `capture --commit` lands it under `append-create`, and staging refuses a target
path that already exists because a unique-sibling rename would leave the link broken while looking
like success. Alias edits cannot be expressed under `append-create` at all, and that is stated
instead of approximated. A round-trip test stages, commits, and asserts the previously broken link
now resolves.

**Removing dead weight rather than adding machinery.** Two sources of bloat were measured rather
than guessed at. Per-revision `bindings` snapshots made up 759 KB of an 855 KB store; only the
latest revision is ever read, so `contexts compact` (and every append) keeps that one and preserves
the rest of the history. The external index built a trigram table that no code ever queried — a
grep for the table name found only its creation and its inserts. Removing it cuts the index from
102 MB to 51.8 MB on the reference Vault, with Chinese search still served by `note_bigrams`. The
schema version is part of the generation hash, so the layout change rebuilds instead of silently
reusing the old index.

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
- agenda: 2,734 items = 2,255 broken + 475 scoped orphans + 3 ambiguous + 1 stale card, all
  reported by `counts_by_code`
- external state: 217.3 MB → 164.5 MB, with the store at 855 KB → 237 KB (−72%) and the new index
  at 51.8 MB vs 102 MB; the previous generation is retained for rollback
- a staged stub committed under `append-create` resolved a real broken link in an isolated Vault

Suite: **224 tests**, ruff clean. The ambiguous and basename-fallback cases remain undecided by
design, so any count that depends on them is a report rather than a verdict; the triage ranking is a
convenience rather than a completeness proof; and convergence suggestions are evidence, not
verdicts.

## 2026-09-22 — P0 correctness closeout

Four reproduced boundary defects from the roadmap review were re-verified as still open against the
then-current code, and closed:

- A Context scope change no longer reports vanished items as resolved. The review `basis` is now
  semantic — Context revision, a `definitions_fingerprint` over scopes/filters/aliases/project
  ids/maintenance, and the diagnostics version — and refuses the comparison with
  `context_definitions_changed` / `diagnostics_version_changed`. Entries predating the semantic basis
  report `previous_basis_not_semantic` rather than being misread as a definitions change.
- An incomplete `contexts review` / `converge` now exits 2, like `contexts tick` / `coverage`.
- Note-relative `../` and `./` links are normalised lexically and bounded to the Vault; a path that
  would escape stays unresolved.
- Every `--save` records its real output path plus `{source: checksum}`, so custom
  `--out-root`/`--name` cards are detected as stale, scope-changed or missing — and a newly added
  scoped source counts as drift, so new material reaches existing cards.

Stated limits: the acceptance Vault contains zero `../` links, so that fix is proven only by synthetic
tests; the card registry cannot retro-cover cards written before it existed, and those are
deliberately not adopted because their true dependencies are unknown; a `converge` suggestion remains
evidence, not a verdict.

Suite: **236 tests**, ruff clean.

## 2026-09-22 — P1: versioned knowledge pages

The roadmap review's P1 asks for one research topic and one project to have a knowledge page, an
evidence manifest, and a budgeted context pack. It is implemented as an external derived artifact
(the review's option (c)), so no new write primitive is introduced and the Vault stays untouched.

The property that makes a page worth trusting is the distinction it preserves between a **sourced**
claim and an **inference**. So the draft format is deliberately narrow and refusals are explicit: a
sourced claim must cite at least one real note (recorded with its checksum); an inference must be
labelled and cites nothing; a `[conflict]` claim needs at least two sources; and a citation to a note
that does not exist, or to lines beyond the end of the note, is rejected outright — an unresolvable
citation is indistinguishable from a fabricated one. Items with no marker are refused rather than
guessed at. The tool never writes the prose; it validates claims the caller authored.

Reads are budgeted and honest about it: `contexts get` returns purpose, scope, claims marked
`sourced`/`conflict`/`inference`, open questions, coverage and its own estimated token cost, with
claims that do not fit omitted **and counted**, so a caller never receives a silent truncation.
`contexts expand` returns a claim's sources as they are now, each `current` / `changed` / `missing`;
changed or missing means the page is stale, not that the claim is wrong.

Acceptance on the packaged Vault: `research:ccs` and `topic:new_business` were each published with
real citations read from the notes, served as `current`, and expanded to genuine excerpts. Basing the
citations on the Vault's own retrieval also caught two would-be errors before publishing — an
inference asserting `dMRV` had no note (it does), and a personnel judgement that was unnecessary.

One bug was found by the acceptance run and fixed: staleness compared the scoped source snapshot
against *all* Vault notes, so both pages reported stale the moment they were published. Drift is now
computed against the Context's current scoped set, which also means a newly added in-scope note makes
an existing page stale. The regression is pinned by a fixture containing an out-of-scope note.

Stated limits: no `[conflict]` claim was published on the real Vault because no verified disagreement
between two sources was found, so that path is covered by tests only; the token budget is an estimate
(CJK characters as one token, others as one quarter), not a tokenizer; re-scoping marks a page
`scope_changed` but never rewrites it or decides which claims survive; and a page remains a record of
what a Context's sources support, not a guarantee that its claims are true.

Suite: **256 tests**, ruff clean.

## 2026-09-22 — tightening the derived-artifact contracts

A second review of this work (HEAD `ce3e956`) reproduced six defects in the mechanisms added the same
day. All six were re-verified and fixed; each has a regression test.

**A card was vouched for by the wrong source state.** `write_compiled_card` wrote the prose and *then*
recorded the scoped checksums, so a draft authored when a source said OLD was registered against NEW:
review reported `clear`. `compile` now returns a `source_snapshot_sha256`, and
`--save --expected-source-snapshot <hash>` verifies it before anything is written — a mismatch is
refused rather than silently registered. Omitting it is allowed, but the card is then recorded as
`basis: unverified`, reported as `compiled_card_unverified_basis`, and "sources unchanged" carries no
trustworthy meaning for it. The recorded `card_sha256` is also checked on read, so a card edited
outside the tool becomes `compiled_card_edited_externally` instead of passing as the original.

**A damaged registry made stale items look resolved.** `load_compiled_cards` swallowed a JSON error
into an empty list, and the review basis did not include registry readability, so a card simply
vanished and its disappearance was reported as a resolution. Reading now returns
`absent / valid / unreadable / invalid` with a reason; the state is part of the basis and of the
agenda as `card_registry_unreadable`; an unreadable state makes the basis incomplete, blocks
`resolved`, and exits 2. The write path refuses to rebuild a damaged registry as an empty list, and
the registry is now checked *before* any file is written, so an unregistered artifact cannot be left
behind. The same treatment was applied to the page registry.

**Scope was decided from a truncated display sample.** Doctor keeps a 20-source sample per target and
review/convergence used it to decide which Context a target belongs to, so a target whose only
in-scope source was its twenty-first occurrence disappeared entirely, and an out-of-scope majority
could decide the hint a scope was judged by. Scope is now decided over the full occurrence stream,
before aggregation; per-scope counts and `counts_by_hint` are computed from in-scope occurrences
only, and the vault-wide total is reported separately as `vault_target_occurrences`. On the reference
Vault this changed three targets' in-scope counts (CCS 262→261, MRV 58→57, ESG30 14→11) and moved the
`needs_review` total 146→143, because ambiguous items no longer borrow the `needs_review` default.
The noise figure became more accurate too: it now counts individual in-scope noisy occurrences
(5582) rather than occurrences of noise-dominant targets (5534).

**An unrelated Context marked a card re-scoped.** The fingerprint was the whole definition set, so
adding any Context flagged every existing card. Cards and pages now record and compare a per-Context
`context_fingerprint`; a record predating it is reported as needing re-verification rather than being
misread as a scope change.

**No definitions looked like a complete empty result.** `converge` with nothing registered returned
zero targets and exit 0. It now reports `configured: false`, marks the result incomplete and exits 2,
and an unknown `--context` is an error instead of an empty answer.

**A successful create is not a fixed link.** `stage_missing_note` only verified that the broker had not
renamed the file, so `[[Missing]]` staged as `Area/Different.md` committed successfully and left the
link broken. The planned path is now re-resolved with the shared resolver before staging: the
occurrences it would fix are listed, and a create that would resolve nothing is refused.

Also fixed: `status` no longer masks an incomplete basis when items exist. Incompleteness is a
top-level `incomplete` field and the warning is printed whenever it is set, not only when the agenda
is empty.

Suite: **271 tests**, ruff clean. Unchanged limits: the card registry can only cover cards written
after it existed; an unverified-basis card is flagged rather than retro-fixed; and the token budget
remains an estimate.

## 2026-09-22 — one read snapshot per command, and honest coverage

The roadmap review's last recommendation was to unify source reading and move expensive scanning off
the repeated paths. A measurement on the reference Vault (2,144 notes) came first, because the claim
was about cost and guessing would have been wrong.

A single `contexts review` used to read the whole Vault twice: once through the maintenance poll and
once for the quality and card checks, so the two halves of one agenda could also disagree about what
the Vault looked like. `poll_contexts` and `maintenance_queue` now accept a `source_scan`, and review
takes one snapshot and passes it down. Measured scan calls per review: 2 -> 1.

The measurement then showed the real cost was somewhere else. Before the change: scan 8.1s, poll
14.1s, quality 10.8s, cards 0.3s (33.3s). The poll produced **zero items** and still cost the most,
because `maintenance_queue` computed a scope-and-checksum baseline for every Context even though the
loop only inspects Contexts with an enabled policy — and none of the six have one. It now computes
the baseline only for enabled policies, and returns `no_policies` without computing anything when
there are none: poll 14.09s -> 0.03s, total 33.3s -> 22.0s (−34%).

An empty poll no longer reads as "maintenance is covered". `poll_contexts` reports
`maintenance_status` and `maintenance_configured_contexts`, the review basis carries them, and the
printed agenda states that zero Contexts have an enabled policy so the empty result is a coverage gap
rather than a clean bill of health. That was the documented intent — an unconfigured policy is shown
as unconfigured, never as active coverage — which the earlier `clear` status had quietly hidden.

Stated limits: this is one command's snapshot, not a shared snapshot across `search`/`brief`/
`compile`/`review` — that remains roadmap item D. `diagnose_vault` (10.8s) is now the dominant cost
of a review, and nothing here reduces it. The measurement is one machine, one Vault and one run; the
percentages are not a general performance claim.

Suite: **273 tests**, ruff clean.

## 2026-09-22 — an unregistered card is not a trusted card

The third closeout item from the development plan. The fallback for cards written before the registry
existed emitted an item only when a source was newer than the card file, so a card that was merely
*older* than its sources passed with no item at all — even though neither its content nor its
authoring basis is known to the tool. A newer file timestamp is not evidence of currency.

Such cards now report `compiled_card_unregistered` with `basis: unregistered` and the command to
re-publish under a recorded basis. On the reference Vault this surfaced a real case immediately: the
second published card had been silently trusted, so the agenda went from 2,734 to 2,735 items, one
card stale and one unregistered. That is the honest state — it was not two cards passing.

Suite: **274 tests**, ruff clean.
