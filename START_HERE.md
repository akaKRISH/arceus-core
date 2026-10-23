# START HERE — Project ARCEUS

**Implementation status (2026-10-23):** the first milestone (metadata-only filesystem index, conservative reconciliation, search) and the structural graph query layer are implemented and tested on Windows 11. See `README.md` for verified commands and limitations, and `reports/2026-10-23_filesystem_baseline.md` for measurements. The steps below remain the intended human workflow for the later phases.

This is the master guide. Follow the phases in order. **Do not start by training a model or building a desktop UI.** The first goal is a useful, testable local tool that knows where files are without copying their contents.

## What ARCEUS is

ARCEUS is a local-first cognitive-agent research project. It will gradually combine a compact local model, a structured view of the computer environment, provenance-aware memory, reusable verified skills, bounded planning, and outcome checks. The long-term motivation is more general and resource-efficient intelligence. **AGI/ASI, novelty, and performance gains are not assumed or claimed.** They must be treated as research hypotheses.

## The destination for the first version

The first working version should:

1. Index only folders the user explicitly selects.
2. Store file-system metadata and relationships in SQLite, not copies of file contents.
3. Search file names and paths, then show the real location so the operating system can open the original file.
4. Handle nested folders, rename/delete changes, permission errors, and symlinks safely.
5. Have automated tests and basic measurements for scan time, query latency, and database size.
6. Work without an LLM, network connection, GPU, or cloud API.

## Step-by-step from a clean Windows PC

### Step 1 — Download and extract

Download `arceus_research_starter_kit.zip`. In PowerShell, change the ZIP path below if it is in a different folder:

```powershell
New-Item -ItemType Directory -Force "$HOME\Projects" | Out-Null
Expand-Archive -Path "$HOME\Downloads\arceus_research_starter_kit.zip" -DestinationPath "$HOME\Projects" -Force
Set-Location "$HOME\Projects\arceus-core"
code .
```

If the `code` command is unavailable, open VS Code manually and choose **File → Open Folder** → `Projects\arceus-core`.

### Step 2 — Audit the machine before installing anything

Run these commands in PowerShell and keep the output for the environment report:

```powershell
Get-Location
py -0p
git --version
nvidia-smi
```

`nvidia-smi` may be unavailable if there is no compatible NVIDIA GPU/driver. That does not block the first milestone. For this phase, Python and Git are sufficient. Node.js and Rust are **not** required until a later desktop-UI phase.

The planned Python version is 3.12. Check it with:

```powershell
py -3.12 --version
```

If that command is missing, install Python 3.12 from the official Python website, then reopen PowerShell and check again. Do not upgrade/downgrade system Python blindly; use the `py -3.12` launcher for this project.

Official references: [Python 3.12 documentation](https://docs.python.org/3.12/) · [Git downloads](https://git-scm.com/downloads) · [Python SQLite interface](https://docs.python.org/3.12/library/sqlite3.html).

### Step 3 — Ask your coding agent to inspect, not code

Open `prompts/00_environment_audit.md` and give its full contents to your coding agent while its working directory is `Projects\arceus-core`.

The first run is inspection-only. The agent must report the OS, Python, Git, RAM/GPU information available, current files, Git status, and any blockers. It must not install software, download model weights, modify files, or create a remote repository during this audit.

Review the report. Resolve any obvious missing requirement before continuing. Do not paste credentials, API keys, or private file contents into the agent.

### Step 4 — Establish the project scaffold

Give the agent `prompts/01_project_foundation.md`. It will add the minimal Python package, configuration, `.gitignore`, and test setup. Have the agent show its plan first. Review the plan before allowing edits.

After the scaffold is written, create a virtual environment and install the project in editable mode:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest -q
```

If PowerShell blocks activation, use a process-only policy for the current terminal (not a permanent machine-wide change):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Do not continue until the basic test suite passes or the agent documents the exact blocker.

### Step 5 — Build the smallest real feature

Give the agent `prompts/02_filesystem_index.md`. It must implement a metadata-only directory scanner and SQLite persistence, then filename/path search. Start with a **test fixture folder created by the tests**, not your whole home directory or entire drive.

Do not add a language model, embeddings, browser access, computer control, automatic startup, filesystem watcher, database server, or GUI in this phase.

### Step 6 — Review every change

Run:

```powershell
git status --short
git diff --check
python -m pytest -q
```

Read `git diff` and verify that no database, `.venv`, model file, private path listing, or secret was staged. Ask the agent to report what it tested and what it did not test. Do not accept claims like “cross-platform tested” if tests only ran on Windows.

### Step 7 — Commit the documentation and foundation

Only after inspecting status and tests, stage the intended source/docs files—not your entire computer or generated index. For the initial documentation commit, one safe pattern is:

```powershell
git add START_HERE.md README.md PRODUCT_VISION.md ARCHITECTURE.md RESEARCH_PLAN.md LITERATURE_REVIEW.md IMPLEMENTATION_ROADMAP.md REPOSITORY_STRUCTURE.md SECURITY_PRIVACY.md EXPERIMENT_PROTOCOL.md AGENT_RULES.md AGENT_START_PROMPT.md FIRST_SPRINT_CHECKLIST.md CONTRIBUTING.md DECISIONS.md prompts .gitignore
git diff --cached --check
git status --short
git commit -m "docs: establish ARCEUS research plan"
```

If `.git` does not exist, initialize it after the audit with `git init -b main`. Never reinitialize a folder that already has a repository. If Git reports an identity error, configure your own name/email after checking the message; do not copy a guessed identity. Do not create a GitHub remote until you explicitly decide to publish.

### Step 8 — Continue one milestone at a time

Use `IMPLEMENTATION_ROADMAP.md` and give your coding agent exactly one prompt at a time, in this order:

1. `prompts/03_graph_search.md`
2. `prompts/04_local_model_adapter.md`
3. `prompts/05_bounded_agent_loop.md`
4. `prompts/06_memory_skills.md`
5. `prompts/07_benchmark_and_BCR.md`
6. `prompts/08_desktop_ui.md` — only after the core interfaces stabilize.

Never give an agent the instruction “build the whole thing” and leave it unattended. Require a plan, bounded change, tests, diff review, and completion report for every phase.

## Rough architecture

- **Python core:** scanner, search, graph queries, planning, evaluation, model adapter.
- **SQLite:** compact metadata, relationships, approved memory, experiments.
- **Local model:** optional and added only after the deterministic baseline works.
- **Policy/tool gateway:** validates all proposed actions; the model cannot bypass it.
- **Desktop UI:** later; Tauri/React/Rust are not first-sprint dependencies.

## Stop / proceed gates

| Gate | Proceed only when… |
|---|---|
| Environment | Actual versions and machine capabilities are recorded. |
| Foundation | Package installs and tests run in a clean virtual environment. |
| Filesystem MVP | Index/search, persistence, error handling, and reconciliation are tested. |
| Graph | Relationships are evidence-based and queryable; no unsupported semantic edges are invented. |
| Model | The user has chosen a model that fits actual RAM/VRAM; weights are not downloaded without consent. |
| Agent loop | Read-only actions, limits, cancellation, and verification work. |
| Memory/skills | Data has provenance, invalidation, and measurable benefit versus a no-memory baseline. |
| Research claim | Results are reproducible and compared against reasonable baselines. |

## Important realities

- A 16 GB system RAM claim does not tell us how large a model will run well; GPU VRAM, model quantization, context length, runtime overhead, and latency matter.
- ARCEUS is not training a foundation model from scratch. First, it is research on the system around a model: environment awareness, retrieval, memory, planning, routing, and verification.
- The project name is a working codename inspired by fictional media. It is not affiliated with or endorsed by Nintendo, Game Freak, or The Pokémon Company. Check naming rights before commercial branding.
- Do not describe a working demo as AGI. Report the actual tasks it can and cannot complete.
