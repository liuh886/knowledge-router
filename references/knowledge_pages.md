# Knowledge pages

A **page** is a durable, revisable statement of what is currently believed about one research topic
or one project. It is built on a Context: the Context decides *which notes count*, the page records
*what those notes currently support*.

Pages are derived artifacts. They live in an external knowledge area beside the Vault
(`<vault>_knowledge/` by default, `--out-root` to change), exactly like compiled cards. Writing a
page **never** modifies the Vault, needs no write policy, and never rewrites human prose. Only a
reviewed proposal would ever enter the Vault, and that goes through the ordinary capture broker.

## Why the format is strict

The only thing that makes a page trustworthy is the difference it preserves between a **sourced
claim** and an **inference**. A page that mixes them is worse than no page, because a later reader
cannot tell which sentences a document actually supports. So the format is deliberately narrow:
every item must declare what it is, and a claim that cites a note which does not exist is refused
outright — that is indistinguishable from a fabricated citation.

The tool never writes the prose. An agent (or a person) authors the claims; the tool validates the
citations against the Vault, records their checksums, renders the page, and keeps a manifest.

## Draft format

Markdown with two recognised sections. Every list item is one entry, and every entry must carry a
marker:

```markdown
## 当前认识
- C1. 结论文字 [src: 200_Area/211_Research/MOC CCS.md#L7-L30]
- C2. 两处独立来源互相矛盾 [src: a.md#L3-L4, b.md#L9-L12] [conflict]
- C3. 还没有出处的判断 [inference]

## 未决问题
- Q1. 还需要确认什么 [open]
```

| Marker | Meaning | Rules |
|---|---|---|
| `[src: path#Lx-Ly]` | Sourced claim | ≥1 citation; every path must exist; lines must be inside the note. Multiple citations are comma-separated. Omit `#Lx-Ly` to cite the whole note. |
| `[src: ...] [conflict]` | Sources disagree | ≥2 citations. Use only when the cited notes really do conflict. |
| `[inference]` | Not supported by a cited source | Must cite nothing. Rendered and returned as `inference` so it can never be read as sourced. |
| `[open]` | Open question | Belongs under `未决问题`; must cite nothing. |

Rejected rather than guessed at:

- an item with no marker;
- an item inside an unrecognised section;
- `[conflict]` with fewer than two sources;
- `[inference]` combined with `[src: ...]`;
- a citation to a note that does not exist, or to lines beyond the end of the note;
- duplicate item ids, or a page with no claims at all.

## Commands

Publish (validator + renderer; writes only the external area):

```bash
python scripts/knowledge_router.py contexts page publish research:ccs \
  --content-file draft.md --vault NAME [--name FILE.md] [--out-root DIR] [--overwrite]
```

Publishing again bumps the version *only* with `--overwrite`; without it an existing page is left
alone. The version, the Context revision, the definitions fingerprint and the scoped source
snapshot are all recorded.

Read the pack (what an agent calls to continue working):

```bash
python scripts/knowledge_router.py contexts get research:ccs --budget-tokens 1200 --vault NAME
```

The pack reports purpose, scope, claims, which are sourced and which are inference, the open
questions, coverage (source count, drift), and its own estimated token cost. Claims that do not fit
the budget are omitted and **counted** (`claims_omitted_by_budget`), so a caller is never handed a
silent truncation.

Expand one claim to the sources as they are *now*:

```bash
python scripts/knowledge_router.py contexts expand research:ccs C1 --depth brief --vault NAME
```

A citation comes back `current`, `changed` or `missing`. `changed`/`missing` means the page is stale,
not that the claim is wrong.

Inspect published pages:

```bash
python scripts/knowledge_router.py contexts page list --vault NAME
python scripts/knowledge_router.py contexts page show research:ccs --vault NAME
```

## Staleness

A page is stale when, compared with what it recorded:

- a **scoped** source was added, removed or edited (notes outside the Context's scope are ignored —
  an unrelated note elsewhere is not a change to this page);
- a **cited** source changed or disappeared;
- the page file itself is gone;
- the Context definitions or scope changed, so the page no longer covers the same sources
  (`scope_changed`).

Drift is computed over the Context's *current* scoped set, so a newly added in-scope source makes an
existing page stale. A page that only refreshed its already-recorded dependencies would let new
material never reach an existing conclusion.

## Limits

- The token budget is an **estimate** (CJK characters as one token, others as one quarter), not a
  tokenizer. It over-estimates Latin text on purpose.
- Re-scoping a Context marks its page `scope_changed`; it does not rewrite the page or decide which
  claims survive.
- A page is a record of a Context's sources, not a guarantee that its claims are true. `inference`
  claims are labelled, never promoted.
- Nothing here schedules anything. Publishing and reading are explicit actions.
