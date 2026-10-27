# KnowledgeRouter: Evaluation & Quality Criteria

## 2026-10-24 — hardlinked sources after a committed capture

- Reproduced exit 2 at the real post-commit index boundary: strict source reading rejected a
  hardlinked `AGENTS.md`. The mutation was already COMMITTED; the Telegram binding stayed pending.
- Added a dedicated read-only source capability. Source reads permit ordinary hardlinks within
  the rooted path while reparse/symlink/traversal checks remain. Writes, mutation planning and
  control-record reads still reject hardlinks; requesting hardlink permission on a writable
  handle is refused.
- Regression first failed with `applied_pending_index`, matching the reported failure. After
  repair: **323 tests passed in 104.21 seconds**; ruff and bytecode checks passed. The installed
  runtime's same three new regressions also passed (0.86 seconds).
- Real original-plan retry returned exit 0, `success` and `idempotent_replay`; the target file's
  SHA-256 was identical before/after the retry. All 2183 eligible notes were indexed, with no
  decode or I/O skips; 1827 sources were hardlinked rather than silently excluded.
- Only the installed Router files needed for the repair were patched, with a local rollback
  backup; no full skill replacement or note rewrite. End-to-end callback verification exposed
  a second failure: the host's 15-second commit timeout expired after 15.45 seconds.
- Ductor commits now have a 120-second deadline while inspections keep 15 seconds. Its bridge
  drains both pipes concurrently and retains a bounded stderr tail instead of discarding the cause.
  The two directly affected host suites passed 35 tests plus strict ruff checks. Extended host
  regression passed **253 tests in 87.80 seconds** after adapting an older cancellation-test
  subprocess mock to provide the newly captured stderr pipe (the incomplete mock had caused
  Windows cleanup/stack-overflow failures). No production behavior was relaxed for that fixture.
- The full actual callback, using the real original plan and a temporary confirmation binding,
  completed in 24.83 seconds and marked the temporary binding saved; the target source and real
  binding were unchanged. After confirming idle state and that only this repair's two files differed
  from startup, PM2 restarted the existing service. The new process is alive, polling Telegram and
  reports source_match. The original button can complete the preserved real confirmation binding.
- Incident:
  [capture/index hardlinks](docs/incidents/2026-10-24-capture-hardlinks.zh.md).

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

## 2026-10-25 — personal capture and domain integration

- Full Python suite: **404 passed in 96.80s**. After a nullable source-state representation correction, the 20 affected Context/stream tests passed again; all scripts/tests pass ruff and compileall.
- Gateway adapter: 3 Node tests passed; server syntax checked. No production gateway/service restart or live HTTP capture claimed.
- Temporary-Vault tests cover source CAS, preview, stable block IDs, cross-year/month, BOM/CRLF, same-intent retries, pending-index recovery, no auto-booking, no implicit directories, todo timing and shared domain-review queue. Production captures: zero.
- Installed stable CLI now resolves one canonical repository through local_source.json; old sibling scripts are not on its import path. PowerShell dot-sources a thin canonical integration. Explicit state/policy unchanged. No provider restart, remote publication or Git push.
- Context revision 6 adds idea/memo/todo/biography/investment while retaining 5 previous versions and the other 6 Contexts. Fixed an absent Daily scope to its actual historical archive and removed a stale CCS MOC scope already covered by Research.
- Five live Context briefs returned evidence with stale=false and no evidence gaps. Biography excerpts disclose pending state; Investment excerpts disclose source verification dates and raw-intake/accounting boundary.
- Production index sync: 2,370 notes, zero skipped I/O/decoding, 15.12 seconds. Concurrent three-worker Context brief smoke samples: 12.44–14.21 seconds; not a performance SLA.
- Real structural findings: 10 Biography pending reviews, 23 Investment pending-check records, 23 missing Investment verification dates. These are real unresolved data-review items, not failed implementation tests or automatic truth judgments. All 23 Investment data files match pre-change hashes.
- Biography dashboard JavaScript evaluated against real card metadata: 10 cards, 0 verified with complete receipts, 10 pending. Obsidian visual rendering was not exercised.
- Recovery archive and live reports: ~/.codex/backups/personal-contexts-20261025-002500/. Archive hashes verified; activation.json records executable source hashes and boundaries.

Remaining product work: source-level review of achievement evidence, actual broker reconciliation when the user requests it, and incremental/scoped verified source reads to reduce repeated whole-Vault scans. The canonical working tree is active code; a later code edit takes effect on the next invocation and must pass regression before use.

Final live check: index refreshed again after link corrections (2,370 notes, 14.12s). Biography review has **0 broken-reference groups**, 10 pending evidence reviews and 1 source_changed maintenance notice caused by this authorized edit. All 9 previously reported broken-reference groups resolved on a complete comparable basis. No review receipt was fabricated to clear the source-change notice.

## 2026-10-25 — measured scoped Context performance

- Full regression: **418 passed in 100.76s**; ruff over all scripts/tests and compileall pass. Fourteen added cases exercise scoped I/O, live source membership, parity, source mutation, encoding failures, ambiguity, illegal paths and bounded tag parsing.
- Real-library profile before change: 18.98s instrumented, including discovery 12.07s and scan 6.80s; 342,044 tag parses for 2,370 source notes. Profiling overhead means this is not compared directly with process wall latency.
- Verified original implementation vs current implementation, sequential process-inclusive samples, Biography/iCal, three each: median **10,511.050 -> 979.634 ms**, sample p95/max **11,834.487 -> 994.538 ms**. Median reduction 90.68%, speedup 10.73x. Three samples do not establish a production p95 SLA.
- Five independent Context/query comparisons: exact evidence arrays, source_count and recorded_state all unchanged. New path 840.459–923.639 ms; original 10,583.604–11,257.189 ms.
- The first comparison wrapper accidentally loaded current modules due to Windows path normalization; its figures were discarded. Final baseline asserts the saved module paths for fts_indexer/context_manager/context_brief and asserts the baseline lacks the new scope-disclosure field.
- `source_generation_scope` explicitly distinguishes scoped digests from Vault generations. Exact registered ids read selected scope content plus event links; name/inferred selection preserves discovery and ambiguity semantics. Every returned source is still hash-rechecked. Live path enumeration is retained; no source-content cache or background watcher was added.
- Fixed the repeatable latency tool to select its explicitly requested production state via the supported environment setting, rather than sending a test-only --state-root override to the CLI. It now retains individual latency samples.
- Zero production Vault writes and no policy/context-registration/service changes this round. The existing stable CLI loads canonical code on the next invocation. Recovery, original modules, identity-checked benchmark runner, raw comparison JSON and source hashes: ~/.codex/backups/router-performance-20261025-105442/.

Index refresh remains a separate cost: full activation still uses two verified scans. A later incremental design must prove detection of external edits, moves, additions/deletions and recovery before skipping those scans. Scope reads solve the measured high-frequency Context bottleneck without weakening that invariant.

## 2026-10-25 — verified incremental index activation

- Full regression: **440 passed in 98.31s**. All scripts/tests pass ruff and compileall; targeted diff whitespace checks pass. Twenty-two new cases cover external changes (including unchanged mtime/size), CJK/FTS consistency, six donor-corruption classes, receipt failures/copy races, activation failures, concurrent sync, full rebuild, no-inheritance strategy and deterministic tied candidates.
- Baseline measured separately: live source scans 8.627/7.635s and fresh build 10.802s. Subsequent warmed samples vary; these are not combined into a guaranteed latency estimate.
- First delta attempt did not improve build cost (5.812 vs full 5.775s) and was not accepted as a performance result. Whole-file immutable receipts removed repeated inherited-token validation while preserving complete source hashing and actual copied-byte verification. Final isolated same-snapshot build sample: full **5.664s**, delta **2.096s**, ~63% lower; all four logical tables equal, predecessor bytes unchanged, 1 updated / 2,369 inherited notes. Later duplicate integrity work was removed without changing the normal data path; no broader speed claim is based on that unmeasured micro-change.
- Complete process comparison over byte-identical temporary Vault copies, alternating order: before 14.547/27.179s, after 13.012/17.421s. Two samples per mode show variability, not a p95/SLA. Both pairs have the same source generation and identical results for five representative queries.
- Bounded scan samples: one directory task 6.416/5.245s vs four tasks 4.793/4.723s. Results/errors match serial coverage; handles are not shared across tasks.
- Publication moved after the second complete source scan. Build, active pointer and pruning are serialized under one index lock. SQLite handles close explicitly; tests exposed/fixed Windows handle retention that blocked candidate replacement. Index.*.verified.json receipts are reconstructible projection caches, excluded by the existing cache backup rule and pruned with their database.
- Production stable CLI sync succeeded with 2,370 notes, no skipped decode/I/O, build_mode=incremental, 3 changed / 2,367 unchanged, no fallback. Active generation b365337e9999a134c4fb657de7551a6eab698ff974f5e34b2857739ac41304ec. The actual database matches its byte receipt. Post-activation operating-guide and NVDA searches returned stale=false evidence.
- write_policy remains append-create and capture_mode explicit; no transaction/persona verification changes, production test captures or service restart. Updated only the authorized Personal_System operating guide in the Vault, with its prior bytes archived.
- Recovery source copies, baseline/failed experiment/final comparison reports, reproducible temporary-Vault runners, previous registry and final code hashes: ~/.codex/backups/incremental-index-20261025-111011/. Detailed contract and measurements: docs/incremental-index-2026-10-25.zh.md.

Whole-source reads remain required twice per refresh. Reducing them needs a separately proven external-change journal and gap/recovery behavior; no mtime-only skip or background watcher is implied by incremental database updates.

## 2026-10-25 — read-only completion diagnosis

No feature/config/source-Vault changes or refreshes. Audit sampled 2,370 notes with one changed Biography annual source; project catalog last synced 2026-09-21. Eleven Contexts, five maintenance policies, no applied Context events; ordinary maintenance runs exist but no successful combined-cycle baseline. Two v2 knowledge pages are stale/scope-changed. Full review --no-record reports 2,844 items, mostly structural diagnostics. Ductor is configured enabled and has a live process; retained exact retrieval logs contain five historical zero-hit invocations, insufficient evidence of successful nonempty conversational injection. Two live exact-id briefs returned fresh evidence in 0.829/0.853s. Current three core source hashes match the 440-test activation record; ruff scripts/tests/integrations passes. No new full regression was run.

Assessment: usable local Beta foundation; knowledge evolution/learning and production-grade release/operational evidence remain incomplete. Detailed assessment and priorities: docs/completion-diagnosis-2026-10-25.zh.md.

## 2026-10-25 — maintenance audit and checkpoint completeness

- Baseline full regression: **440 passed in 115.85s**, Ruff passed. Existing uncommitted changes were preserved.
- Reproduced a real source-checkpoint gap with an isolated fixture: the canonical PowerShell .ps1 adapter was omitted, leaving only MANIFEST.json. Added allowlisting confined to integrations/powershell/*.ps1; secrets.env and a root .ps1 remain excluded. The test verifies archive inclusion and byte-identical isolated restore. Final full regression: **441 passed in 107.55s**; Ruff scripts/tests/integrations passed.
- Clarified current-plan precedence over dated historical counts/decisions and the installed README's launcher/historical-code boundary. No historical scripts were removed or patched.
- Complete pre-change Vault diagnosis: 2,370 Markdown notes, 869 assets, zero skipped I/O/decoding; 6,283 broken occurrences over 2,325 targets, 20 ambiguous occurrences over 7 targets, 1,539 orphans. Source-byte duplicate analysis found 132 groups and 289 extra copies. These are review signals, not authorized deletion candidates.
- Read-only review: 2,844 items, basis.complete=true, incomplete=false; display limited to 10. Source content is live, so old audit counts are not current acceptance targets.
- Ran maintain --with-tick: validated=true, status=attention, run e7f08f9e854546b2a82cdc72437caff8; complete-cycle success baseline now exists. This does not imply any fact was reviewed or all KR-N3 criteria passed.
- Refreshed project catalog: 29 projects, 224 documents, 14 dirty worktrees. Three cap notices cover kept 20 of 26/23/50 candidates, hence 39 candidate documents omitted. This is bounded catalog coverage, not a complete repository scan.
- Reproduced a separate unresolved defect: quality_analyzer.context_quality_queue labels an explicitly excluded path as a Context member because it uses scopes only, ignoring include/exclude/tags. No corresponding defect is asserted for contexts review. This remains backlog, not a completed repair.
- Eight existing Vault document amendments were prepared with original bytes, exact diff and hashes; not applied because saved append-create does not authorize replacement. No write-policy escalation, bulk reorganization, deletion, service restart or remote publication.

Technical dispositions and next steps: [maintenance audit](docs/maintenance-audit-2026-10-25.zh.md). Local evidence/recovery is in the timestamped private audit backup; local paths are retained in the Vault maintenance record rather than this portable report.

## 2026-10-25 — data-foundation contract and first governance batch

- User confirmed prioritizing source-data governance over research synthesis. Added `docs/data-layer-contract.zh.md`; updated current plan, task queue and design/readme entrypoints. Earlier KR-N1–N5 plans remain labelled history. Registered Contexts, existing knowledge pages and domain schemas were not retired or migrated; CCS page evolution is no longer a core acceptance gate.
- Added a standalone read-only `scripts/data_inventory.py`, reusing safe source scanning without Context or registry access. Two live scans agreed on 2,066 Markdown paths/hashes, no skipped I/O/decoding. The tool reports its rule version, complete manifest, bounded candidates, omissions and source-set verification; matching scans are not claimed to be an atomic snapshot. Top-level simple scalar parsing is explicitly not YAML validation.
- Inventory: 151 same-identifier candidate groups; 855 structured-record candidates, 71 template candidates, 4 raw-capture candidates, 5 navigation candidates, 1,131 prose/unknown. These are role/identity hints, not defects or deletion decisions. Domain ID candidates are separated by schema/type; legal backup/snapshot identities remain preserved. Two notes contain inline Base64 image payloads. Zero repeated top-level keys/unparsed identity fields in this limited parser, not a certification of metadata validity.
- Three-family live pilot inspected an existing idea stream, a prose note whose only image reference has no local attachment, and a Frontmatter-based object record. Preserved raw input, object identity, financial/verification state and unknowns. Prepared a source-hashed governance checklist rather than synthesizing a research page or backfilling IDs.
- Reproduced escaped table alias links leaving `.md` in normalized path targets. Fixed the shared resolver; same-scan, note-target-only comparison against the backed-up original corrects exactly eight existing PRD references without editing those source notes. Diagnostic version increased 2 -> 3 so review deltas cannot mistake rule changes for source repairs. Graph projections now carry resolution version 2; missing/mismatched versions are not reused. Ordinary reads omit old graph affinity; explicit graph queries may rebuild externally.
- Final full suite: **454 passed in 119.16s**; Ruff over scripts/tests/integrations passed. Tests cover namespace separation, duplicate fields, unparsed metadata, complete counts under bounded display, changing/unreadable verification scans, read-only CLI/no implicit directories, table aliases/assets/headings and rejection of old graph-rule caches. Existing tests and working-tree changes were preserved. A prior full run passed before the additional graph-cache protection; the final count above is authoritative for this batch.
- Created only `200_Area/Knowledge_Data_Governance.md` using broker plan `999f7e5f3d8243b99b3fa0a4d2ba332d`, with expected missing target and stable intent key. Stable CLI returned committed + successful incremental indexing, 2,067 notes and no skips. Source SHA-256: `2f45a46822fed642daf519dafbca6ea8f7d4f1e6019f9b8fc4bd65cdc65740f8`. No saved-policy elevation, bulk source edits, moves, deletions, service restart, Git commit/push or research publication.
- Post-write verification: the created file matches the frozen draft; all seven internal links resolve and stable-CLI retrieval returns the same hash with stale=false. Against the pre-create inventory there is one added source, no removals, and three changed existing sources (Inbox cockpit, project cockpit and task note). Those concurrent changes are not operations in this batch's broker plan and are not counted as governance repairs; no whole-Vault byte-invariance claim is made. Original three pilot-source versions remain represented by their saved hashes.

Remaining: known quality context_queue include/exclude/tags membership defect; missing attachment recovery, explicit snapshot/source relationships and reviewed existing-note amendments. These are pending data-governance work, not automatically repaired by inventory or a changed diagnostic count. Private source manifests, before-copies, comparison evidence, pilot hashes and JUnit output are retained outside the Vault; the Vault governance entry records their local location.

## 2026-10-26 — filtered quality membership and source-governance follow-through

- Baseline full suite before source changes: **454 passed in 143.33s**. Preserved the existing dirty working tree and backed up the specific source/doc files. No installed sibling modules were patched.
- Fixed `quality.context_queue` scope-only attribution by reusing shared scopes/include/exclude/tags membership. The CLI supplies its existing source scan and full doctor occurrence stream; grouped targets are attributed across all actual problem sources, not the first path or the capped 20-source sample. A same-named relative link that resolves elsewhere cannot become an in-scope problem. Repository-relative paths are not treated as Vault paths.
- Queue schema 2 / membership_rules_version 2 discloses membership_complete, membership_basis, per-Context source counts and unverified Context ids. Missing required tags, truncated fallback samples or an inconsistent occurrence report cannot return a false complete classification. All original signals and full counts remain available; no source repair or state promotion follows from a reclassification.
- Same-scan live comparison: **2,070 Markdown notes, 3,754 identical signals**. Old buckets 2,524 maintained / 1,230 unscoped become 2,512 / 1,242 with complete membership. The 12 reassigned groups are a rules correction, not 12 repaired notes. Stable-CLI quality also returned queue schema 2 and membership_complete=true; exit 1 denotes findings, not an operational error. No corresponding defect is asserted for contexts review.
- Final full suite: **461 passed in 131.07s**; Ruff scripts/tests/integrations passed. New cases cover filters/nested tags, late in-scope occurrences beyond the display sample, relative-target success elsewhere, missing tags under zero display budget, excluded-path certainty, repository separation and incomplete source reports. CLI acceptance uses real registered normalized filters. One initial new fixture used unnormalized uppercase tags in an internal canonical-definition call; corrected the fixture. An initial test command named a nonexistent file and ran no tests; neither is represented as a passing run.
- Searched the scoped Documents and Codex backup trees by target filename and four existing ZIP member inventories; no matching image found, no filesystem/archive read errors in the recorded scan. Excluded dependency/Git-object directories are recorded; renamed assets, cloud/external disks and Git object history are not ruled out. No synthetic image, OCR or replacement research prose was created.
- Identity follow-up retained 151 candidate groups: 134 one-nonhistorical-plus-historical, 14 historical-only, 3 multiple-nonhistorical, based on path hints; 146 business-ID namespaces and 5 ULID groups. These classifications do not establish authoritative lineage or permit deletion. Source and consumer evidence is retained privately. Built-in Templates is configured/enabled; two fixed-ULID template lines have exact broker replace previews, left uncommitted under append-create. Existing person/month records and business IDs remain unchanged.
- Inspected Ownly's configured WYQD root and known source directory-prefix consumers; equivalence with the installed build and exhaustive consumer coverage remain unverified. An explicit-project-root official CLI summary succeeded (35 objects). Saved CLI defaults point at an absent old checkout; configuration was not changed. No Ownly objects, account values, snapshots or backups were modified.
- Committed a prefix-preserving missing-asset annotation to the original note through plan `67872097dc4a4c4e8bc496e5d206efa2`; stable CLI returned successful indexing. Before hash `41c1f9d408e5fe67f711bb98272a3917b8032588d5493e4a394e90e17ed0b4ee`, after hash `231886f4aa1c6afc0800340fc25fcb33a6a88ce21e981417b8a8dc2dc93e4bd0`. The image reference and complete old byte prefix are retained; recovery remains pending.

Private comparison/manifest/asset-search/consumer evidence, exact template patches, broker plans and JUnit results live outside the Vault; their location and the current dispositions are appended to the single existing Vault governance entry. No write-policy elevation, bulk migration/deletion, service restart, research publication, Git commit or push. Quality membership defect is resolved; missing image and reviewed template replacements remain open.

Post-write verification: governance append plan `d78bc4e023c248268e4d2bda4a41bf15` committed and indexed successfully (2,070 notes). Both modified notes match their frozen after-hashes and retain the complete expected old-byte prefixes. Source manifests show no additions/removals, those two intended modifications and one separately observed task-note change, not attributed to these broker plans. Stable-CLI retrieval of the missing-asset annotation returned its expected new hash with stale=false. The two template files are unchanged; no recovery of the missing image is claimed.
