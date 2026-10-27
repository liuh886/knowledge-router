# KnowledgeRouter Documentation & Research Index

Welcome to the **KnowledgeRouter** technical documentation and research repository.

---

## 📚 Table of Contents

Current architecture (0.2): [DESIGN](../DESIGN.md) and
[ADR-001: unified memory workflows](decisions/001-unified-memory-workflows.zh.md).
The September reviews below are historical evidence; their test counts and runtime states are
not the current release acceptance record. See [EVALUATE](../EVALUATE.md) for new verification.

Operational incident: [2026-10-24 capture/index hardlinks](incidents/2026-10-24-capture-hardlinks.zh.md).

### 0. Plan
* **[Data-layer contract](data-layer-contract.zh.md)**: Current foundation boundary, identity/provenance/time semantics, role-aware governance and the three-family pilot. Supersedes research-page evolution as the core acceptance goal.
* **[Maintenance audit, 2026-10-25](maintenance-audit-2026-10-25.zh.md)**: Technical debt, conflicting rules, redundant installations, historical files, verified fixes and the remaining acceptance queue. Historical diagnostic counts are dated evidence, not current acceptance targets.
* **[Development plan](development-plan.zh.md)**: Where the project stands, the decisions already taken,
  the remaining delivery items with their acceptance conditions, the stated non-goals and the known
  unverified limits. Authoritative for the plan; `TASKS.md` stays the implementation record.

### 1. Core Architecture & Specification
* **[Symbiotic Memory Vault Specification (共生记忆 Vault 规格说明书)](symbiotic-memory-vault-spec.zh.md)**: Product goals, 6 cognitive slices (focus, idea, memo, todo, contact, ownly), decoupling and CLI interaction protocols.
* **[System Design Specification](../DESIGN.md)**: Full architectural blueprint, data flow contracts, and subsystem definitions.
* **[Evaluation Criteria & Quality Benchmarks](../EVALUATE.md)**: Evaluation fixtures, search latency thresholds, and accuracy metrics.
* **[Roadmap & Tasks](../TASKS.md)**: Development phases and milestone tracking.

### 2. Deep Dives & Industry Research
* **[Andrej Karpathy's LLM Wiki Paradigm](research/karpathy_llm_wiki_paradigm.md)**:
  - Theoretical analysis of the "Compile, Don't Retrieve" knowledge philosophy.
  - Architectural breakdown of LLMs acting as compilers vs traditional RAG.
  - Ecosystem survey of community implementations.
* **[Deep Dive: NicholasSpisak/second-brain](research/nicholasspisak_second_brain_deep_dive.md)**:
  - Technical teardown of the `NicholasSpisak/second-brain` project.
  - The 4 core skills: `/second-brain`, `/second-brain-ingest`, `/second-brain-query`, `/second-brain-lint`.
  - Strengths, critical limitations on existing vaults, and how `KnowledgeRouter` builds upon and surpasses it.
* **[Community Real-World Practice & Lessons Learned](research/community_experiences_and_feedback.md)**:
  - Practical feedback, consensus, breakthroughs, and pitfalls from Reddit (`r/ObsidianMD`, `r/LocalLLaMA`) and Hacker News.
  - How the community tackled "AI slop inflation", "flat wiki collapse", and token costs.

### 3. Agent Skills & Protocols
* **[4D Cognitive Routing Protocol](../references/routing_protocol.md)**: Formal specification of the 4-dimensional intent resolution engine.
* **[Safety & Mutation Contract](../references/safety_contract.md)**: Non-destructive mutation rules protecting human-authored Markdown.
* **[Runtime Layout](../references/runtime_layout.md)** and **[Architecture](../references/architecture.md)**: Where external state lives and which modules own it.

### 4. Product Reviews (Chinese)

Dated review notes behind the current direction. They are kept as a record of what was verified
when, including the findings that were later fixed and the limits that remain.

* **[Development assessment](development-assessment-2026-09-22.zh.md)**: First independent review of
  the installed integration and the two-corpus picture. Contains local machine paths, so it is not
  distributed.
* **[Reassessment](development-reassessment-2026-09-22.zh.md)**: Re-check after the S1/S2 batch; lists
  the four reproduced boundary defects that were subsequently fixed.
* **[Update review](update-review-2026-09-22.zh.md)**: Review of the fixes above, which reproduced six
  further defects in the derived-artifact mechanisms; all six were fixed afterwards.
* **[Product and architecture strategy](product-and-architecture-strategy-2026-09-22.zh.md)**:
  Competitive capability comparison, the split query path, and the missing dependency record for
  derived artifacts.
* **[Context base landscape and roadmap](context-base-landscape-and-roadmap-2026-09-22.zh.md)**:
  Supersedes the strategy note. Reframes the goal as a user-owned, source-grounded, continuously
  revisable context base, and sets the P0-P4 order plus the acceptance story.

* **[Context performance slice](context-performance-2026-10-25.zh.md)**: Measured exact-id scoped reads, evidence parity, and the separate incremental-index consistency gate.

* **[Verified incremental index](incremental-index-2026-10-25.zh.md)**: Source/hash completeness, immutable byte receipts, failure recovery, measured gains and remaining scan cost.
