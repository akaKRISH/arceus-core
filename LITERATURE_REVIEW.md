# Literature Review and Reference Queue

This is a starting bibliography, not a completed systematic review. Read the primary source, write what it contributes, identify the baseline it uses, record limitations, and map it to an ARCEUS design decision. Do not infer novelty merely because this project uses different names or combines multiple concepts.

## Agents and reusable skills

- **Voyager — An Open-Ended Embodied Agent with Large Language Models (2023):** proposes an automatic curriculum, an evolving executable skill library, and feedback/self-verification in Minecraft. Relevant to skill storage, composition, and skill transfer; not direct evidence for filesystem agents. [Paper](https://arxiv.org/abs/2305.16291) · [Project](https://voyager.minedojo.org/)

## Memory and long-horizon context

- **MemGPT — Towards LLMs as Operating Systems (2023):** hierarchical/virtual context management and tool-mediated control flow for long interactions and document tasks. Relevant to memory tiers and explicit context management. [Paper](https://arxiv.org/abs/2310.08560) · [Research page](https://research.memgpt.ai/)
- **LongMemEval (2024):** benchmark for long-term interactive memory, including information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention. Relevant to measuring memory updates and stale knowledge. [Paper](https://arxiv.org/abs/2410.10813)
- **Graphiti (open source):** temporal context graph system for agents, combining graph relationships with other retrieval approaches. Useful as a design comparison; evaluate current implementation and claims independently. [Repository](https://github.com/getzep/graphiti) · [Project page](https://www.getzep.com/platform/graphiti/)

## Computer-use evaluation

- **OSWorld:** benchmark for open-ended computer tasks in real desktop/web applications with execution-based evaluation. The project site announced OSWorld 2.0 on 26 June 2026; use the current version and its current evaluation instructions if this later becomes relevant. OSWorld is broader and more expensive than the initial ARCEUS filesystem suite. [Official site](https://os-world.github.io/) · [Original paper](https://arxiv.org/abs/2404.07972)

## Software engineering tasks

- **SWE-bench (2023):** evaluates language models on real GitHub issues in software repositories. Relevant later if ARCEUS is evaluated on code-agent work; not a substitute for file-navigation or OS-task benchmarks. [Paper](https://arxiv.org/abs/2310.06770) · [Project](https://www.swebench.com/)

## Core storage/search documentation

- **Python `sqlite3` documentation (Python 3.12):** documents the standard-library SQLite interface and transaction behavior. [Docs](https://docs.python.org/3.12/library/sqlite3.html)
- **SQLite FTS5:** optional SQLite full-text search virtual table. Use it only when the kind of indexed text and measured search behavior warrant it; ordinary filename/path indexes are enough for the first implementation. [Docs](https://www.sqlite.org/fts5.html)
- **llama.cpp:** a possible local inference runtime to evaluate after hardware/model requirements are measured. It is not a mandatory first-phase dependency. [Repository](https://github.com/ggml-org/llama.cpp)

## Review worksheet (complete for every paper/tool)

| Field | What to record |
|---|---|
| Citation/version/date | Primary source and revision date |
| Problem | Specific task addressed |
| Method | What it actually does |
| Baseline | What it compares against |
| Metrics | Success, latency, cost, safety, etc. |
| Main result | Quote or paraphrase carefully with a citation |
| Limitations | Scale, domain, dependence on model/API, reproducibility |
| ARCEUS implication | Adopt, adapt, compare, or avoid |
| Gap hypothesis | What remains unanswered, not just what is absent from our prototype |

## Novelty check before any research claim

Search current papers and systems for all close variants of: filesystem graph for agents; local computer-use agents; episodic/semantic agent memory; procedural skill libraries; tool-routing under latency/compute budget; postcondition verification; local model efficiency. Record negative matches and near-duplicates. If a similar mechanism already exists, narrow the contribution to a measurable comparison or a genuinely different algorithm.
