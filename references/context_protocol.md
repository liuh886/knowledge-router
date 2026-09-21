# Context protocol — local implementation

The generic Context layer adds a functional map above existing evidence and capture
services. Daily, Project, Contact, Research, Trip and user-defined types share the
same definition and impact resolver. No workflow Markdown is executed as code.

## Current interface

`contexts brief NAME_OR_ID --query QUESTION --vault NAME [--json]` reads current scoped
sources and sources referenced by applied events for that Context. It selects bounded
source windows with literal query terms, verifies their hashes after scanning, and omits
sources that changed or became unreadable. No question produces an overview prioritizing
explicit task/decision/progress passages. Modification time breaks ties; it is not event time.
Unknown/ambiguous Context names fail rather than selecting an arbitrary scope. Linked event
expansion currently uses up to 100 historical references and discloses truncation.

This is an extractive reference brief, not an authoritative merged current-state projection.
No query matches means an explicit evidence gap. `history` now re-reads each referenced
source and distinguishes unchanged, changed and unavailable; append-only growth may make
a receipt stale even when the original event remains present.

For explicitly selected Git project evidence, add `--project PROJECT_ID` and a nonempty
`--query`. The id must exist in the selected Vault's attached project catalog; unknown ids
fail. No same-name joins or all-project fallback are performed. The selection is per-request,
unless a reviewed definition supplies exact `project_ids`; in that case the saved relationship
is explicit and versioned. Repository evidence is returned separately in
`project_evidence`, retaining canonical repository identity, path, lines, hash, revision,
metadata and staleness. `--limit` bounds each source type separately.

A reviewed definition can instead include `project_ids`, an exact list of catalog identities:

```json
{
  "id": "project:router",
  "type": "project",
  "name": "Knowledge Router",
  "purpose": "project progress",
  "scopes": ["100_Project/Knowledge Router"],
  "project_ids": ["knowledge-router"]
}
```

Registration verifies every id against the attached read-only catalog and stores the binding in
the checksummed definition revision. A Context brief with a query automatically searches those
reviewed projects and reports `origin: reviewed_binding`; an explicit `--project` remains a
one-call override. Missing or moved repositories and changed documents are exposed as stale
evidence, not silently rebound.

For compatibility, `evidence_gap` still describes Vault evidence only. Opt-in results add
`project_evidence_gap` and `combined_evidence_gap`; repository matches do not fill a missing
Vault state declaration. A corrupt/missing catalog is an operational error, not an empty
project evidence result. Human output labels stale repository excerpts as historical evidence.

September 10 acceptance: 117 Router tests passed (115.02 seconds), including scoped
repository selection and stale-source rendering. A live broad project:main brief with
the ductor catalog exceeded 128 seconds and was deliberately cancelled; process exit
was verified. A subsequent 25-second timed stack trace located the wait in parent-directory
opening during the Vault scan. A fail-fast probe identified a sharing violation at
`100_Project/2601_ESG30/ESG30`. Native tests reproduced the conflict caused by requesting
DELETE access alongside an ordinary directory reader. The final implementation uses
LIST_DIRECTORY + READ_ATTRIBUTES while still denying delete sharing, and tests reject
ancestor renames and pre-existing DELETE-capable handles. Attribute-only opens failed the
safety test and were not retained. The same live joint brief completed in 12.05 seconds
after batching sibling reads, returning two Vault and two repository excerpts with no
stale flags. This removes the
pathological wait but does not establish an interactive latency SLA; broad live scanning
still needs a measured budget.
Final regression after the batched-scan change: 122 tests passed (89.06 seconds),
including append/create protection, directory races, recovery and the new native sharing tests.

`contexts discover --vault NAME --json` returns a bounded candidate map, type counts,
unclassified source count and missing/empty scope signals. `--details` returns all
candidates. Discovery scans current readable sources, not a potentially stale cache.

`contexts plan "今天和张一帆讨论 Knowledge Router 项目，下周跟进" --vault NAME --json`
returns all matching Contexts, source samples, alias ambiguity, and follow-up detection.
Samples establish scope membership; they are not claimed to be relevant historical
answers. Names require explicit matching; no relationship is invented from co-occurrence.

`contexts links NAME_OR_ID --vault NAME [--rebuild] [--json]` reads one Context's resolved
wikilink neighbourhood. It reuses the durable external `link_graph.json` projection (rebuilding
it only when absent or when `--rebuild` is given) and reports topic anchors (MOC/index notes),
related Contexts ranked by crossing links with scope overlap excluded, hub notes by degree and
cross-scope bridge notes. Markdown and attachment targets resolve by path, stem and frontmatter
alias; attachment references become `asset_links` and never become note edges. Only the external
projection is written; the Vault is not modified, and no relationship is inferred from
co-occurrence. `definitions_stale` is reported when the registered definition baseline predates
the graph's source generation.

A Context definition may narrow membership with optional `include`, `exclude` and `tags`.
`include`/`exclude` are Vault-relative path prefixes (a note must match an `include` when one
is present and must not match any `exclude`); `tags` requires the note's frontmatter `tags`/`tag`
to contain one of the values, with nested tags matching their parent (`Contact/Person` satisfies
`Contact`). Filter values are validated at registration, applied consistently to discovery,
briefs, drift bindings and `contexts links`, and never rewrite a note. Briefs also re-rank
in-scope passages toward notes linked from MOC anchors when a stored `link_graph.json` exists;
the result reports `graph_affinity_used`, and ranking is unchanged when no graph is stored.

`contexts compile NAME_OR_ID --vault NAME [--limit N] [--json]` folds one Context into a
compiled-card scaffold: recorded state, open follow-ups, topic anchors, bounded cited evidence
and a Markdown template. It is read-only (`writes_performed=0`) and produces no synthesized
judgement or current-state claim. With `--stage --content-file PATH [--target REL.md]` the
reviewed card is frozen as an external broker plan (never a Vault write); the target directory
must already exist, and the copy is written only by `capture --commit --plan-id <id>` under an
`append-create` policy. Suggested targets default to `<first-directory-scope>/_compiled/<name>.md`.
Follow-up dates are deliberately unresolved in M1 and no reminder is scheduled.

Both commands accept `--definitions PATH` for an explicit JSON declaration:

```json
{
  "schema_version": 1,
  "contexts": [
    {
      "id": "project:router",
      "type": "project",
      "kind": "instance",
      "name": "Knowledge Router",
      "purpose": "维护本地 Context 管理器开发的目标、进度与决策",
      "scopes": ["Projects/Router", "Research/Context"],
      "aliases": ["knowledge-router"]
    }
  ]
}
```

Scope paths are relative to the selected Vault. Declared scopes with no readable
Markdown yield a signal rather than creating paths. Types are open strings; adding
a type requires no new routing branch. Declarations override same-type inferred
bindings at the same scope. Declarations are input data, not write authorization.

## Persistent functional definitions

Optional per-definition `maintenance` policy:

```json
{"enabled": true, "review_interval_days": 7, "check_source_changes": true}
```

This is an example, not a global default. `contexts due --vault NAME --json` evaluates
only explicitly enabled policies. It reports source drift, missing evidence, unresolved
state and overdue review with stable item ids and bounded output. The initial review anchor
is registration time; a verified `reviewed_at` state declaration replaces that anchor.
The timestamp must include a timezone and cannot be in the future relative to evaluation.
Ordinary source edits never count as review. Verified `lifecycle=retired` skips the policy.
No rule is automatically assigned to old notes and no notification is sent by this command.

`contexts register --definitions PATH --vault NAME --json` saves the complete declared
definition set under the existing external Vault state root. Inferred candidates are
not automatically promoted. The first registration expects revision 0; updates require
`--expected-revision N`. A stale revision fails rather than overwriting another agent's
update. Each previous definition set and its source baseline remains in the same atomic,
checksummed, Vault-bound version history. Unknown/empty scopes cannot be registered.

Subsequent discover/plan commands automatically use the saved definitions. Explicit
`--definitions` overrides only that invocation. Output identifies the definition origin
and revision, so consumers know whether they used saved or provisional data.

`contexts check --vault NAME --json` compares current evidence to the saved baseline,
reporting added, removed and changed sources with bounded samples. A renamed source
appears as removed from the old binding; the system does not guess the replacement.
Routine edits are reported as changes, not asserted to be defects. Checking never
rebases the baseline. Re-registering a reviewed complete set creates the next baseline.
The baseline is the readable source snapshot at registration, not a transactional
freeze of the live Vault. A concurrent note edit appears on the next check.

`quality --vault NAME --json` adds a bounded `context_queue` when reviewed definitions exist.
It prioritizes broken references, evidence gaps and orphan candidates inside maintained scopes,
keeps unscoped additions visible, and labels archive/backup/snapshot paths only with a
`historical_hint`. The queue is explainable and read-only; no repair, deletion or automatic
reclassification occurs.

## Boundaries and next work

### Unified scheduler poll

`contexts tick --vault NAME --json` combines configured maintenance candidates and due
follow-ups, including Context ids found in applied event receipts even when their original
definition is no longer registered. Failures are retained while later Contexts are checked.
`--max-contexts` bounds work (default 100); limit overflow or nested truncated scans sets
scan_incomplete and exit code 2. `--limit` only bounds returned samples. A stable fingerprint
covers collected items/errors without check time, but is not a sent/acknowledged marker.

The initial local scheduler is a Codex thread heartbeat (automation id `knowledge-router`),
configured daily at 09:00 local Asia/Shanghai. It invokes maintenance (external index refresh
and source diagnostics) followed by tick,
keeps unchanged/non-actionable results quiet and reports meaningful changes/errors. This is
not a Ductor in-process observer. A real scheduled trigger was observed September 9;
actionable notification delivery now has a locally verifiable sink, while remote transport
delivery remains a separate adapter acceptance gate.

### Follow-ups reuse state events

`contexts capture` supports `--followup-id ID --followup-title TITLE --due-at ISO_OFFSET_TIME`
with optional `--followup-status open|done|cancelled` and `--state-context LINKED_ID`.
These flags cannot be combined with generic state field/value flags. The resulting reserved
field `followup_ID` carries validated JSON in the canonical state declaration. Due times are
normalized to UTC while retaining an unambiguous instant; date-only or timezone-free values
are rejected by the storage schema. Capture's `--due-at` also accepts supported Chinese
relative expressions when `--timezone` names an installed IANA zone. Examples: 明天上午9点,
下周二14:30, 后天下午三点十五分. 下周 means next calendar week; a date without clock time,
unknown year, unsupported phrasing, DST gap or duplicate local hour requires clarification.
Preview includes original expression, reference instant, local time and normalized UTC due_at.
Only the normalized instant is frozen into the event. Existing event ids must be used for
later commit/retry; a new-day re-resolution is not allowed to silently change an existing key.

Use the same follow-up id and `--replaces` to revise, complete, cancel or reopen. Multiple
unsuperseded updates remain conflicts. Generic state capture cannot bypass reserved-field
validation. `contexts followups CONTEXT_ID [--due-only]` rebuilds the source-verified view,
includes unresolved follow-ups as needs_review, and reports state-scan truncation. No timer,
notification or independent task database is created by listing. `contexts deliver --sink
ABSOLUTE.jsonl` is an explicit second step: it records pending/delivered/failed/cancelled
receipts in external state and appends each notification once to the selected local sink.
Closed or replaced follow-ups cancel only undelivered intents; retries use the stable
notification id. The sink must be outside the Vault and Router state. This local sink does
not claim Telegram, Matrix or Ductor delivery.

### Explicit state declarations and corrections

`contexts capture` accepts `--state-field FIELD --state-value VALUE`, optionally
`--state-context LINKED_ID` (default primary Context) and repeated `--replaces EVENT_ID`.
The declaration is included visibly in the frozen Markdown payload and external event record.
It is not database-only truth. Replacements must reference applied events for the same
Context and field. Field `lifecycle` accepts `active` or `retired`; retirement records state
without deleting source notes or automatically suppressing subsequent explicit capture.

`contexts state CONTEXT_ID` rebuilds state from applied declarations. Event inspection after
commit and structured Context briefs include this recorded state. Source verification checks
the exact committed prefix and payload, allowing later append-only growth. Missing or changed
evidence gives an unavailable result; the system never resurrects an earlier superseded value.
Multiple unsuperseded declarations produce a conflict even if one was recorded later. Resolving
a conflict requires a new declaration naming the replaced events. This is an explicit state
model; prose inference, lifecycle task scheduling and automatic decisions are not implied.

## M2 event execution slice

`contexts capture TEXT --target RELATIVE.md --primary-context ID --context-id OTHER
--idempotency-key KEY --vault NAME --json` freezes an explicit target and Context link
set. Repeat `--context-id` for additional links. It does not write a Vault note.
Context ids come from discover; a missing/empty Context fails, and the target must be
inside the primary Context's scope. Caller selection resolves identity explicitly.

`contexts commit EVENT_ID --vault NAME --json` applies the frozen broker plan, records
the external Context links, and refreshes the retrieval index. It requires the saved
append-create policy and user-authorized capture. It never reclassifies the event.
`contexts event EVENT_ID` shows payload, links and receipts. `contexts history CONTEXT_ID`
lists applied event references; hashes describe source state at commit, not proof of
current freshness. Read source evidence before drawing current conclusions.

An index failure leaves `applied_pending_index`; retrying commit reuses broker idempotency.
A crash between broker application and the external receipt is reconciled by the broker
on retry. A changed request with the same key fails. Competing previews may leave an
unused external broker plan, but only the winning event plan is selected for commit.
Receipt publication requires local filesystem hard-link support and fails closed when
unavailable. Checksummed immutable records are published after their payload is fsynced.

Completion here means one canonical event + recorded Context links + refreshed retrieval
index. It does not mean a project-state brief has been synthesized, follow-ups scheduled,
or human-authored Contact/Project pages rewritten. Those remain subsequent work.

### Earlier M1 preview boundary

M1 executes `received → contexts_resolved → impact_plan_prepared`. It does not
persist an event, create a capture plan, schedule work, or claim maintenance complete.
It does not yet infer frontmatter aliases, semantic identities, source-of-truth
ownership, current project state, or non-ISO daily filenames. Inferred Contact note
identities require review because collections may contain non-person notes.

M2 now binds one event to a broker plan and external Context links. Keeping that
transaction separate avoids duplicating the fact into multiple human notes and
preserves existing compare-and-swap, recovery and append-only guarantees.

Performance claims for the existing FTS benchmark do not apply to Context discovery:
this version performs a live source scan and reports no millisecond retrieval SLA.
