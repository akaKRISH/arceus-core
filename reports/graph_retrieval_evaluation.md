# Graph Retrieval Evaluation — Phase 3

- **Experiment ID:** `ARCEUS-GRAPH-RETRIEVAL-001`
- **Benchmark version:** `graph-retrieval-v1`
- **Fixture version:** `synthetic-fs-v1`
- **Seed:** `20261023`
- **Date:** 2026-10-23
- **Code state:** baseline commit `cd350b2`; the evaluation harness and this report are working-tree changes on top of it.
- **Machine-readable artifact:** [graph_retrieval_evaluation_v1.json](graph_retrieval_evaluation_v1.json)
- **Environment:** Windows 11 (10.0.26300), Python 3.13.15, 12 logical CPUs, ARCEUS 0.1.0. No model, network, GPU, or external service was used.

## 1. Research question and hypothesis

**H1:** For filesystem navigation tasks that require structural relationships, graph-assisted retrieval improves answer success and/or evidence quality compared with path-only retrieval.

This was written down before running the benchmark. The falsification condition was: if graph-assisted retrieval does not improve success or evidence on relationship tasks, or if it regresses anywhere, H1 is not supported for this suite. No novelty or generalized-intelligence claim follows from a win on a synthetic fixture.

## 2. Fixture and task design

The fixture is generated in a fresh temporary directory from a fixed seed:

- 28 directories and 146 present files (including 120 filler files in ten `bulk/group-*` directories);
- nested project, document, photo, mixed-extension, duplicate-name, unique-name, and empty-directory structures;
- two files (`docs/obsolete-draft.txt`, `projects/arceus/src/removed.py`) are created, indexed, deleted on disk, and observed as `missing` by a second scan.

The resulting index contains 174 present entries and 2 missing entries. The two missing rows are intentional evidence for the `missing_entry` tasks. Ground truth is derived from an independent in-memory fixture model, not from the production graph or search code. The fixture generator, task model, and evaluator share no retrieval logic.

The suite has **35 tasks in 10 categories**, equal weight per task:

| Category | Tasks | What is required |
|---|---|---|
| `exact_name` | 4 | Unique filename lookup |
| `partial_name` | 4 | Substring lookup; some have several valid matches |
| `ambiguous_name` | 4 | Same filename in different directories; return the complete set |
| `children` | 4 | Direct children of a directory |
| `subtree` | 4 | Bounded descendants at depth ≤ N |
| `ancestors` | 3 | Ancestor directory chain plus parent→child evidence |
| `extension_in_directory` | 4 | Direct files of a given extension, scoped to one directory |
| `nested_path` | 3 | Canonical resolution of a known nested path |
| `no_result` | 3 | Return nothing for absent names/tokens/extensions |
| `missing_entry` | 2 | Identify an indexed path observed as `missing` |

Expected targets are exact root-relative paths; expected evidence is a set of relationship or status labels:

- `contains:<parent>|<child>` for observed structural relations (the registered root is written as `.`);
- `indexed:<path>` for a metadata search hit;
- `missing:<path>` for a stale row observed absent.

## 3. Baselines and method

Both systems receive the **same 35 task objects**, the same fixture, the same index, and the same ground truth.

- **Path-only baseline (`path_only`)** uses only the production `search()` API with its documented filters (root, extension, include-missing). It never calls the graph API. For structural tasks it receives the same natural query text (for example, the directory basename) and returns search hits.
- **Graph-assisted (`graph_assisted`)** uses `search()` for name/path tasks and the production graph API for structural tasks: `resolve_path`, `children`, `subtree`, `ancestors`, `lookup_entry`. Its evidence is built from the returned relationships and status labels.

For every task/system pair the harness performs one warm-up retrieval, then five timed repetitions. Correctness is taken from the final result; the target/evidence signature is compared across all repetitions and no nondeterminism was observed. Retrieval latency excludes fixture creation, scanning, and report writing. A fixed seed, fixed task IDs, and task-level macro averaging prevent one task with many targets from dominating the aggregate.

The run is deterministic. Repeated non-timing outputs were checked by a test that runs the full harness twice and compares every task's targets, evidence, and success.

## 4. Aggregate results

| Metric (denominator) | Path-only | Graph-assisted | Difference |
|---|---:|---:|---:|
| Exact task success (35 tasks) | **23/35 · 65.7%** | **35/35 · 100.0%** | +12 tasks · +34.3 pp |
| Top-1 accuracy (12 single-target tasks) | 11/12 · 91.7% | 12/12 · 100.0% | +1 task |
| Target precision, macro (33 non-empty tasks) | 0.7847 | 1.0000 | +0.2153 |
| Target recall, macro (33 non-empty tasks) | 0.9062 | 1.0000 | +0.0938 |
| Target F1, macro (33 non-empty tasks) | 0.8204 | 1.0000 | +0.1796 |
| Evidence precision, macro (33 evidence tasks) | 0.5312 | 1.0000 | +0.4688 |
| Evidence recall, macro (33 evidence tasks) | 0.5312 | 1.0000 | +0.4688 |
| Evidence F1, macro (33 evidence tasks) | 0.5312 | 1.0000 | +0.4688 |
| No-result false-positive rate (3 tasks) | 0/3 · 0.0% | 0/3 · 0.0% | 0 |
| Retrieval latency median / p95 (175 samples) | 0.238 / 0.355 ms | 0.127 / 0.287 ms | not a performance claim |
| Deterministic across repetitions | yes | yes | — |

There were **12 improvements, 0 regressions, and 23 ties**. Every path-only success is also a graph-assisted success.

## 5. Results by category

| Category | Tasks | Path success | Graph success | Path target F1 | Graph target F1 | Path evidence F1 | Graph evidence F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `exact_name` | 4 | 4 | 4 | 1.000 | 1.000 | 1.000 | 1.000 |
| `partial_name` | 4 | 4 | 4 | 1.000 | 1.000 | 1.000 | 1.000 |
| `ambiguous_name` | 4 | 4 | 4 | 1.000 | 1.000 | 1.000 | 1.000 |
| `children` | 4 | **0** | **4** | 0.610 | 1.000 | 0.000 | 1.000 |
| `subtree` | 4 | **0** | **4** | 0.753 | 1.000 | 0.000 | 1.000 |
| `ancestors` | 3 | **0** | **3** | 0.000 | 1.000 | 0.000 | 1.000 |
| `extension_in_directory` | 4 | 3 | 4 | 0.950 | 1.000 | 0.000 | 1.000 |
| `nested_path` | 3 | 3 | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| `no_result` | 3 | 3 | 3 | n/a | n/a | n/a | n/a |
| `missing_entry` | 2 | 2 | 2 | 1.000 | 1.000 | 1.000 | 1.000 |

### Where the graph improved retrieval

The gains are confined to relationship queries:

- **Direct children.** For `docs`, the path-only system returned the directory itself, all descendants in the archive subtree, and the direct children. The graph returned exactly `docs/archive`, `docs/budget-2026.xlsx`, `docs/notes.md`, and `docs/report.txt`. Path-only cannot express "parent equals this directory" through the current search API, so it also failed the one single-target children task (`empty-dir`).
- **Bounded subtrees.** For `docs/archive` depth 2, path-only returned the archive root and included descendants without applying the depth limit. The graph applied the depth bound and excluded the root.
- **Ancestor chains.** For `projects/arceus/src/main.py`, path-only returned two files (`main.py` and `test_main.py`) instead of the ancestor directories. The graph returned `projects`, `projects/arceus`, and `projects/arceus/src`, plus parent→child evidence through to the file.
- **Directory-scoped extensions.** For `mixed` + `py`, path-only returned `mixed/alpha.py`, `mixed/beta.py`, and the nested `mixed/nested/gamma.py`, because its search could only use a path substring. The graph returned only the direct `.py` files.
- **Evidence provenance.** Even on the three extension tasks where path-only guessed the target set correctly, it emitted only `indexed:<path>` labels, so its structural evidence score was zero. The graph emitted the observed `contains:<directory>|<file>` edges.

### Where the graph did not help

Path-only was already sufficient for 23/35 tasks:

- exact, partial, and ambiguous name lookup: search is the appropriate capability and both systems tied at 1.0 target and evidence F1;
- nested path resolution: a full relative-path search hit the single expected row;
- no-result: both systems abstained with no false positives;
- missing entries: path-only used the documented `include_missing` filter, so it matched the graph's status result;
- three extension tasks where the directory basename happened to be unique enough to avoid non-direct descendants.

There were **no cases where graph-assisted retrieval performed worse** on target success in this suite.

## 6. Confounders and limitations

- **The comparison is capability-shaped.** The graph system receives structured inputs (directory path, depth, extension scope) while the path-only system receives natural query text and may only use the existing search filters. The result shows that graph operations answer structural questions that the search-only interface cannot answer. It is not evidence that graph ranking is better for arbitrary search queries.
- **Small, synthetic, single-seed suite.** 35 fixed tasks, one fixture seed, English-like names, 174 indexed entries, and one Windows machine. No confidence intervals are reported because the tasks are fixed and the sample is too small for a meaningful population estimate. Any claim of generalization would require held-out and independently generated tasks.
- **Evidence scoring is schema-aligned.** Expected structural evidence uses the graph's observed relationship vocabulary. The path-only baseline is not permitted to invent structural claims, so this metric measures provenance capability, not a general retrieval-quality difference.
- **Task mix determines the aggregate.** 15/35 tasks are structural. A different mix would produce a different aggregate; therefore per-category results are the primary result and the aggregate is secondary.
- **Latency is exploratory.** The graph system was slightly faster in this run, but timings are sub-millisecond and were not designed as a performance experiment. No CPU pinning, thermal control, or confidence interval was used; do not infer a general latency advantage.
- **No model or content retrieval.** The experiment says nothing about LLM-assisted behavior, memory, skills, or semantic search.
- **Single implementation.** Both systems were implemented for this evaluation; an independent implementation might expose different failure modes.
- **The fixture ground truth and scanner were tested separately** (135 passing tests at run time, 2 skipped symlink-creation tests), but this does not eliminate evaluator design error.

## 7. Conclusion

**H1 is supported narrowly, not generally.** On this deterministic synthetic suite, graph-assisted retrieval solved 35/35 tasks versus 23/35 for path-only, with all 12 gains concentrated in children, subtree, ancestor, and directory-scoped extension tasks. It produced no target regressions, and its evidence matched every expected structural relation. Path-only already solved every name/path task, the no-result cases, the missing-entry cases, and most directory-scoped extension cases.

This is enough to meet the Phase 3 roadmap gate for an explicit-ground-truth comparison: the structural graph adds a measurable capability over path-only retrieval. It is not evidence of improved general navigation, and it does not justify a claim of novelty. The benchmark must now be strengthened before the result is treated as a stable research finding.

## 8. Reproduction

From the repository root, with the project virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m evals.run_benchmark --repeats 5 --seed 20261023 `
  --output reports/graph_retrieval_evaluation_v1.json
python -m pytest
```

The benchmark creates its synthetic fixture and SQLite index in a temporary directory, deletes them after the run, and writes only the JSON artifact. It needs no network or model weights. The default run uses three repetitions; the committed artifact was produced with five. The CLI prints the summary table reproduced above. The full per-task targets, evidence, latency, and success flags are in the JSON artifact.

The evaluator tests cover fixture determinism and on-disk consistency, independent ground truth, metric denominators, no-result handling, missing-status tasks, retrieval-error containment, evidence attribution, artifact validity, non-timing repeatability, and temporary-directory cleanup:

```powershell
python -m pytest tests/test_evals.py
```

## 9. Recommended next research step

Do a **baseline-strengthening experiment before adding a model**: generate multiple fixture seeds and a held-out task split, then add a third baseline that uses path-only search plus explicit string post-filtering over returned relative paths (direct-parent filtering, depth counting, extension scoping) without calling the graph API. If that strong string baseline closes the gap, the graph's measured advantage is mostly an interface artifact; if it does not, the structural index has value beyond string manipulation. Only after that comparison should Phase 4 (local model adapter) begin, and no model should be loaded without a separate hardware/model decision.
