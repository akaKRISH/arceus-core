# Graph Retrieval Evaluation — Phase 3.1

- **Experiment ID:** `ARCEUS-GRAPH-RETRIEVAL-002`
- **Benchmark version:** `graph-retrieval-v2`
- **Fixture version:** `synthetic-fs-v2`
- **Frozen configuration:** `graph-retrieval-v2-frozen-1`
- **Seeds:** 5 development + 10 held-out (listed in §2)
- **Run date:** 2026-10-23 (artifact generated 2026-10-23T09:36:27Z)
- **Code state:** baseline commit `cd350b2`; the v2 harness, tests, report, and artifact are uncommitted working-tree changes on top of it
- **Machine-readable artifact:** [graph_retrieval_evaluation_v2.json](graph_retrieval_evaluation_v2.json)
- **Previous experiment:** [graph_retrieval_evaluation.md](graph_retrieval_evaluation.md) (`graph-retrieval-v1`), preserved unchanged
- **Environment:** Windows 11 Home 10.0.26300, Python 3.13.15, 12 logical CPUs, ARCEUS 0.1.0. No model, GPU inference, network access, or external service was used.

## 1. Research question and claims

The original hypothesis was:

> **H1:** For filesystem navigation tasks that require structural relationships, graph-assisted retrieval improves answer success and/or evidence quality compared with path-only retrieval.

Phase 3 supported that hypothesis on one fixture. Phase 3.1 asks whether the advantage survives **(a)** a stronger non-graph baseline that reconstructs relationships from indexed path strings and **(b)** directory layouts generated from seeds that were not used to tune the implementation.

Three claims are kept separate:

| Claim | Comparison | What it would establish |
|---|---|---|
| C1 — capability | `graph_assisted` vs `path_only` | Structural questions can be answered at all, rather than only guessed from search hits. |
| C2 — unique retrieval information | `graph_assisted` vs `path_derived` | The graph API provides information or evidence that string processing over the same metadata cannot reconstruct. |
| C3 — held-out persistence | Same comparisons on 10 frozen held-out seeds | The result is not specific to the development layouts. |

The falsification conditions were written before the held-out run: C2 is rejected if `path_derived` matches graph-assisted success and evidence on every task; C3 is not supported if the held-out pattern materially differs from development. A 100% score on a finite synthetic suite is not proof of generalization.

## 2. Fixtures and task design

### Frozen seeds

- **Development:** `20261023`, `20261024`, `20261025`, `20261026`, `20261027`
- **Held-out:** `731001`, `731002`, `731003`, `731004`, `731005`, `731006`, `731007`, `731008`, `731009`, `731010`

These lists are defined in `evals/config.py`. The held-out list was fixed before the held-out run; no retrieval logic was changed after that run.

### Per-seed variation

All generated fixtures are synthetic and temporary. Each seed varies:

- top-level names, archive/year names, project name, other-project name, photo trip name, and mixed/nested names;
- the project root (`projects`, `work`, `ventures`, or `studio`);
- the source-directory name (`src`, `source`, `app`, or `code`);
- whether an extra package directory exists, changing the depth of `main.py` and the expected module children;
- the test-directory name (`tests`, `spec`, or `checks`);
- repeated report/notes/README/settings names and their placements;
- extension mix and which files are direct children versus nested;
- filler group count and filler file count.

Observed ranges across the 15 seeds:

| Property | Range |
|---|---:|
| Present files | 106–163 |
| Directories | 27–32 |
| Present index entries | 135–193 |
| Maximum path depth | 4–5 |
| Filler files | 80–140 |
| SQLite database size | 151,552–176,128 bytes |
| First-scan duration | 0.0092–0.0277 s |

The fixture has two files that are indexed, deleted on disk, and then observed as `missing` by a second scan. Every seed therefore exercises reconciled index state, not only a freshly scanned tree.

### Tasks

Each seed has the same **35-task category definition**, giving 525 task instances across 15 seeds:

| Category | Per seed | Total | Requirement |
|---|---:|---:|---|
| `exact_name` | 4 | 60 | One unambiguous filename |
| `partial_name` | 4 | 60 | Substring lookup, including a 3-match prefix and README/notes cases |
| `ambiguous_name` | 4 | 60 | Same name in multiple directories; complete set required |
| `children` | 4 | 60 | Direct children of docs, module, empty-with-nested-empty, and project root |
| `subtree` | 4 | 60 | Bounded descendants; depths vary with nesting |
| `ancestors` | 3 | 45 | Ancestor directory chain and parent edges |
| `extension_in_directory` | 4 | 60 | Direct files of one extension in one directory |
| `nested_path` | 3 | 45 | Exact root-relative path resolution |
| `no_result` | 3 | 45 | Absent token, absent extension, absent prefix |
| `missing_entry` | 2 | 30 | Indexed paths observed missing after deletion |

Ground truth comes from an independent fixture model, not from `search`, `graph`, or any retriever. Before evaluation, the harness checks every expected target and evidence label against the generated filesystem: present/missing status, parent relation, parent directory existence, and evidence vocabulary. A failure aborts the seed rather than producing a score.

The path-only system also has a genuine limitation by design: it receives the query text and the documented extension filter, but cannot express “direct children of this directory,” a depth bound, or an ancestor chain. Every unsupported task remains in the denominator and is reported as a failure.

## 3. Systems and fairness controls

All three systems receive the same `RetrievalSpec`: task id, category, kind, natural query text, and structured parameters (directory, depth, extension, or path). The spec contains **no expected targets or evidence**. Scoring is done separately against `BenchmarkTask` ground truth.

| System | Implementation | Structural information used |
|---|---|---|
| `path_only` | Production `search()` with root, extension, and include-missing filters | None; no directory enumeration, depth, or ancestry |
| `path_derived` | One read-only `SELECT` of scalar metadata (`rel_path`, `entry_type`, `extension`, `status`) plus string filters | Parent by last `/`, depth by segment count, descendants by prefix, directory scope by parent equality, extension by scalar field |
| `graph_assisted` | Production `resolve_path`, `lookup_entry`, `children`, `subtree`, `ancestors` | Indexed parent keys and observed relationships, with graph provenance |

The `path_derived` implementation is in `evals/path_derived.py`; its metadata interface is in `evals/index_view.py`. It does not import `arceus_core.graph`, `arceus_core.paths`, or a copy of the production graph functions. A test runs every task with the graph functions monkeypatched to raise, and the baseline still completes. It also receives no expected answers, and a test asserts that the spec type has no expected fields.

The path-derived system is not a weaker straw man:

- it loads the complete scalar index for the root, not just the top search hits;
- it can enumerate every direct child and every bounded descendant;
- it reconstructs exactly the same target sets and evidence labels as the graph system on this tree-shaped index;
- the only intentional asymmetry is that it does not use the stored `parent_rel_path` column.

That last point is the core of C2: if string-derived parent/depth relations match the graph’s observed relations, the graph API is not contributing unique retrieval information on these tasks.

### Measurement procedure

For every seed:

1. Generate a fresh fixture and verify its model against disk.
2. Register it as an isolated root and scan it.
3. Delete the two planned files and scan again.
4. Verify present/missing entry counts.
5. Validate every task’s ground truth against disk.
6. Run every system on the same task specs.
7. Warm up once, then take 3 timed repetitions per task/system.
8. Compare target/evidence/notes signatures across repetitions.
9. Score correctness, aggregate by category and split, and clean up the temporary workspace.

All 15 seeds were deterministic across repetitions for all three systems. There were no retrieval errors, unsupported-task notes, or empty answers.

## 4. Metrics and denominators

- **Success:** exact set equality over targets; denominator = all task instances (525; 350 held-out).
- **Target precision/recall/F1 macro:** one score per task, then unweighted mean; denominator = tasks with non-empty expected targets (480; 320 held-out). No-result tasks are excluded rather than counted as zero.
- **Evidence precision/recall/F1 macro:** same task-level macro definition; denominator = tasks with non-empty expected evidence (480; 320 held-out).
- **Top-1:** denominator = tasks with exactly one expected target (180; 120 held-out). This includes exact lookups, nested paths, missing entries, and one single-child case.
- **No-result false-positive rate:** denominator = tasks whose `kind` is `no_result` (45; 30 held-out). A children task that legitimately expects nothing would not enter this denominator.
- **Latency:** median and p95 over 1,575 timed samples per system (1,050 held-out). Warm-up is excluded. This is exploratory: repeated timings are not independent task observations, and no CPU pinning or thermal control was used.
- **Paired comparisons:** per `(seed, task)` success changes; 525 pairs. These are descriptive counts, not a significance test.

No confidence intervals, p-values, or population-level inference are reported. The suite is finite and fixed; a 100% result is not evidence of universal generalization.

## 5. Aggregate results

### All 15 seeds (525 task instances)

| Metric | `path_only` | `path_derived` | `graph_assisted` |
|---|---:|---:|---:|
| Exact task success | **338/525 · 64.4%** | **525/525 · 100.0%** | **525/525 · 100.0%** |
| Top-1 | 165/180 · 91.7% | 180/180 · 100.0% | 180/180 · 100.0% |
| Target precision, macro | 0.7852 | 1.0000 | 1.0000 |
| Target recall, macro | 0.9062 | 1.0000 | 1.0000 |
| Target F1, macro | 0.8215 | 1.0000 | 1.0000 |
| Evidence precision, macro | 0.5312 | 1.0000 | 1.0000 |
| Evidence recall, macro | 0.5312 | 1.0000 | 1.0000 |
| Evidence F1, macro | 0.5312 | 1.0000 | 1.0000 |
| No-result false positives | 0/45 | 0/45 | 0/45 |
| Median latency | 0.266 ms | 0.524 ms | 0.190 ms |
| p95 latency | 0.674 ms | 1.152 ms | 0.489 ms |
| Deterministic | yes | yes | yes |

### Development and held-out splits

| Split | System | Success | Target F1 | Evidence F1 | Median / p95 |
|---|---|---:|---:|---:|---:|
| Development (175) | `path_only` | 111/175 · 63.4% | 0.821 | 0.531 | 0.243 / 0.430 ms |
| Development (175) | `path_derived` | 175/175 · 100% | 1.000 | 1.000 | 0.501 / 0.700 ms |
| Development (175) | `graph_assisted` | 175/175 · 100% | 1.000 | 1.000 | 0.162 / 0.249 ms |
| Held-out (350) | `path_only` | 227/350 · 64.9% | 0.822 | 0.531 | 0.304 / 0.748 ms |
| Held-out (350) | `path_derived` | 350/350 · 100% | 1.000 | 1.000 | 0.609 / 1.192 ms |
| Held-out (350) | `graph_assisted` | 350/350 · 100% | 1.000 | 1.000 | 0.205 / 0.526 ms |

`path_only` scored 22 or 23 out of 35 on every seed, including all 10 held-out seeds. Both structural systems scored 35/35 on every seed.

### Category results, held-out seeds

| Category | Tasks | `path_only` success | `path_derived` | `graph_assisted` | Path target F1 | Path evidence F1 |
|---|---:|---:|---:|---:|---:|---:|
| `exact_name` | 40 | 40 | 40 | 40 | 1.000 | 1.000 |
| `partial_name` | 40 | 40 | 40 | 40 | 1.000 | 1.000 |
| `ambiguous_name` | 40 | 40 | 40 | 40 | 1.000 | 1.000 |
| `children` | 40 | **0** | **40** | **40** | 0.579 | 0.000 |
| `subtree` | 40 | **0** | **40** | **40** | 0.812 | 0.000 |
| `ancestors` | 30 | **0** | **30** | **30** | 0.000 | 0.000 |
| `extension_in_directory` | 40 | **27** | **40** | **40** | 0.935 | 0.000 |
| `nested_path` | 30 | 30 | 30 | 30 | 1.000 | 1.000 |
| `no_result` | 30 | 30 | 30 | 30 | n/a | n/a |
| `missing_entry` | 20 | 20 | 20 | 20 | 1.000 | 1.000 |

The all-seed category table has the same shape: children 0/60, subtree 0/60, ancestors 0/45, extension 38/60, everything else 100% for `path_only`; both structural systems are 100% in every category.

## 6. Paired comparisons

On the held-out split:

| Comparison | Improvements | Regressions | Ties (both success) | Ties (both failure) |
|---|---:|---:|---:|---:|
| `path_derived` vs `path_only` | +123 | 0 | 227 | 0 |
| `graph_assisted` vs `path_only` | +123 | 0 | 227 | 0 |
| `graph_assisted` vs `path_derived` | **0** | **0** | **350** | **0** |

On all 15 seeds:

| Comparison | Improvements | Regressions | Ties (both success) | Ties (both failure) |
|---|---:|---:|---:|---:|
| `path_derived` vs `path_only` | +187 | 0 | 338 | 0 |
| `graph_assisted` vs `path_only` | +187 | 0 | 338 | 0 |
| `graph_assisted` vs `path_derived` | **0** | **0** | **525** | **0** |

There is no task instance on which `path_only` beat either structural system. There is also no task instance on which the graph system beat the path-derived system.

## 7. Where the structural systems improved retrieval

### Direct children

A held-out example (`seed 731001`, `children-01-docs`) expected four direct children:

```text
heron-yarrow/budget-xenon-cedar.xlsx
heron-yarrow/juniper-amber
heron-yarrow/notes-moss-jasper.md
heron-yarrow/report-dune.txt
```

`path_only` returned eight entries: the directory itself, the archive directory, and report files at three depths, in addition to the correct direct children. It cannot express “parent equals this directory.” `path_derived` filtered on the string parent; `graph_assisted` used indexed parent rows. Both returned exactly the four expected entries.

### Bounded subtrees

For the same seed, `subtree-01-archive` expected the year directory and two reports, excluding the archive root. `path_only` included the archive root itself and ignored the depth bound. Both structural systems applied depth 2 and excluded the root.

### Ancestor chains

For `ancestors-01-main` (`projects/willow-glacier/src/lumen/main.py`), `path_only` returned only the file itself. The expected target was the four ancestor directories, plus parent-child evidence through the file. Both structural systems returned the four directories and the complete evidence chain.

For `ancestors-02-report`, `path_only` returned the three reports with that name; the expected answer was `docs`, `archive`, and `year` directories.

### Directory-scoped extensions

`path_only` succeeded 38/60 overall and 27/40 held-out on extension tasks. It fails when a matching file exists deeper in the subtree or inside an extra package directory. A held-out example: `extension-01-mixed-py` expected only `mixed/alpha.py` and `mixed/beta.py`, but `path_only` also returned `mixed/nested/gamma.py`. `extension-03-module-py` failed on 7/15 seeds where a package directory made `main.py` a nested child rather than a direct file.

`extension-02-project-md` (project README) and `extension-04-trip-jpg` (three direct photos) have no nested same-extension competitors in any seed, so `path_only` passed them. The category-level result is therefore conditional on layout, not a uniform graph win.

## 8. Where the graph did not help

- **Name lookup, partial names, and ambiguous names:** search is already the correct tool. All three systems scored 100% in these categories on all seeds.
- **Nested path resolution:** a full relative-path search found the exact row; graph resolution and string lookup tied.
- **No-result behavior:** all three abstained on all 45 cases; no false positives.
- **Missing entries:** documented `include_missing` search, scalar status lookup, and graph lookup all returned the same missing rows and labels.
- **Extension tasks without competing nested files:** `path_only` succeeded, so there was no gap to close.
- **Against `path_derived`:** the graph did not help at all. It matched targets and evidence on 525/525 task instances. This is the central negative result of Phase 3.1.

### The path-derived result

The path-derived baseline is not merely close; it is identical on every scored task:

- same success: 525/525 vs 525/525;
- same target macro precision/recall/F1: 1.0;
- same evidence macro precision/recall/F1: 1.0;
- zero improvements and zero regressions in the paired comparison.

Given a normalized tree index, the scalar fields `rel_path`, `entry_type`, `extension`, and `status` are sufficient to reconstruct every structural relation used by this benchmark. The production graph API expresses those relations more directly and with explicit provenance, but it did not provide additional answers or evidence on these fixtures.

## 9. Interpretation by claim

### C1 — Graph vs ordinary path-only: supported on this suite

The graph system answered every structural task that the path-only interface could not express. The addition is real at the interface/capability level: directory enumeration, bounded traversal, ancestor chains, and parent-scoped extension queries are not available through `search()` alone. The result persisted at nearly the same rate on held-out layouts (path-only 64.9% vs structural 100%), so the gap is not an artifact of one fixture.

It does not follow that the graph outperforms search for ordinary name/path queries; on those tasks it tied. The 35-task mix deliberately over-represents structural queries relative to a typical search workload.

### C2 — Graph vs path-derived: not supported

The graph system did **not** outperform a strong non-graph structural baseline. Both achieved 525/525 success and 1.0 target and evidence F1. The fair conclusion is that the measured benefit over path-only comes from **having structural access at all**, not from graph-specific information that path strings cannot supply.

This does not make the graph API useless. It centralizes parent/ancestor semantics, uses the scanner’s observed parent keys rather than re-parsing paths at every call site, and exposes observed-vs-inferred provenance. Those are interface and maintainability properties. They were not measured as a controlled outcome in this experiment.

### C3 — Held-out persistence: supported for the structural gap, limited by design

The held-out split reproduced the development pattern: path-only 227/350 (64.9%) versus 350/350 for both structural systems, with all 123 held-out improvements in children, subtree, ancestors, and extension. Different names, depths, package presence, and placements did not change the category-level conclusion.

The held-out fixtures still share the same synthetic task grammar and tree-shaped index. This is evidence of layout robustness within that grammar, not evidence of generalization to real user directories, unusual filesystem semantics, larger trees, or natural-language task phrasing.

### Correctness, evidence, expressiveness, latency, maintainability

| Dimension | Finding |
|---|---|
| Correctness vs path-only | Clear improvement on structural categories; no regressions. |
| Evidence vs path-only | 0.531 → 1.000 macro F1; path-only emits only `indexed:` labels, not structural edges. |
| Correctness/evidence vs path-derived | No difference on 525/525 tasks. |
| Expressiveness | Both structural systems express the queries; path-only cannot. The graph API is the more direct interface. |
| Latency | Graph was faster in this run (median 0.190 ms vs 0.266 ms path-only and 0.524 ms path-derived), but path-derived reloads the full row set per query and timings were not controlled. Do not treat this as a general performance result. |
| Maintainability | Not measured. Graph uses observed parent keys and one provenance vocabulary; path-derived duplicates path assumptions at each operation. This is a reasoned expectation, not an experimental finding. |

## 10. Confounders and limitations

- **Path-derived is deliberately strong.** It receives the entire scalar index for the root and all structured task parameters. It is not restricted to search results. That is the point of the control, but it means the result speaks to information sufficiency, not to implementation effort.
- **The graph and path-derived views are equivalent on tree indexes.** The scanner stores `parent_rel_path` derived from the same hierarchy that the path string encodes. The benchmark contains no inconsistent, imported, or malformed parent rows, so it cannot distinguish graph provenance from string reconstruction.
- **Capability-shaped comparison.** `path_only` is not given directory/depth parameters; it is the ordinary search interface. C1 is therefore an interface comparison as much as an algorithm comparison.
- **Synthetic and small.** 525 task instances from 15 generated trees, 106–163 files each. No real files, permission-denied directories, Unicode-heavy names, case collisions, deep pathological trees, or large-scale indexes.
- **Fixed task grammar.** The same 35 task definitions are instantiated on every seed. Held-out variation is in names, nesting, package presence, and placements, not in task type or phrasing.
- **Metric vocabulary favors structural provenance.** Evidence labels (`contains:`, `indexed:`, `missing:`) are shared by the structural systems. Path-only cannot claim structural edges, so its evidence score is structurally bounded. The target metrics do not have this issue.
- **No statistical inference.** Paired counts are descriptive; tasks within a seed are correlated, and there is no confidence interval or significance test.
- **Latency is exploratory.** Sub-millisecond measurements on one Windows machine, with path-derived loading the full metadata view on each call. This is not a controlled benchmark of engine performance.
- **Windows-only.** No Linux or macOS execution. Cross-platform behavior remains unverified.
- **No model or content retrieval.** Nothing here evaluates LLM behavior, semantic search, memory, skills, routing, or real user tasks.

## 11. Reproduction

From the repository root, using the project virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1

# Full deterministic suite
python -m pytest

# Complete frozen experiment: 5 development + 10 held-out seeds, 3 repeats
python -m evals.run_benchmark

# Optional narrower runs
python -m evals.run_benchmark --suite development --repeats 1
python -m evals.run_benchmark --suite heldout
```

The default artifact is [graph_retrieval_evaluation_v2.json](graph_retrieval_evaluation_v2.json). The command creates every fixture and SQLite database under a temporary directory, validates ground truth against disk, writes only the artifact, prints the summary tables, and deletes the temporary workspace. It requires no network, model, database server, or user documents.

The artifact records the frozen seed lists, configuration version, per-seed fixture/index counts, per-task targets, evidence, scores, and notes. It contains no absolute paths, usernames, or machine identifiers.

## 12. Conclusion

**C1 is supported narrowly:** on this synthetic suite, graph-assisted retrieval solves structural tasks that ordinary path-only search cannot express, and the gap persists on 10 held-out layouts with no target regressions.

**C2 is rejected:** a non-graph baseline that reconstructs structure from indexed path strings matches the graph system exactly on all 525 task instances, including target and evidence F1. The graph API did not contribute unique retrieval information here.

**C3 is supported only within the synthetic grammar:** the structural gap persisted across held-out names, depths, and package layouts, but the experiment does not establish generalization to real filesystems or population-level performance.

The research hypothesis should therefore be revised. The defensible claim is **not** “graph retrieval is more accurate than structural path filtering.” It is:

> Structural access—whether through the graph API or an equivalent path-derived view—is necessary for the relationship tasks in this suite, while ordinary search alone is insufficient.

The graph layer remains a reasonable interface and provenance design, but Phase 4 should not be motivated by a measured graph-information advantage. Before moving to a model, the next deterministic step should test conditions where observed parent rows and path reconstruction can diverge, and measure scale/maintainability; otherwise the honest position is that the two structural implementations are informationally equivalent on the current evidence.

A 100% score on 525 finite synthetic instances is not a general intelligence, novelty, or real-world generalization claim.
