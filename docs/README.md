# KnowledgeRouter Documentation & Research Index

Welcome to the **KnowledgeRouter** technical documentation and research repository.

---

## 📚 Table of Contents

### 1. Core Architecture & Specification
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
* **[4D Cognitive Routing Protocol](../../.codex/skills/knowledge-router/references/routing_protocol.md)**: Formal specification of the 4-dimensional intent resolution engine.
* **[Safety & Mutation Contract](../../.codex/skills/knowledge-router/references/safety_contract.md)**: Non-destructive mutation rules protecting human-authored Markdown.
