# CooperBench

> **Branch `harbor-parity`** — the original side of the parity experiment for the Harbor adapter
> `cooperbench-autoadapter`. It is upstream `main` at `63b9d44` plus this note,
> `scripts/harbor_parity_daytona.py` and the parity subset `dataset/subsets/harbor_parity.json`.
> Prompts and agents are unchanged. One evaluator bug fix (git hooks, below) is applied here and in
> the Harbor adapter alike. See [Harbor parity](#harbor-parity) below.


[![arXiv](https://img.shields.io/badge/arXiv-2601.13295-b31b1b.svg)](https://arxiv.org/abs/2601.13295)
[![Website](https://img.shields.io/badge/Website-cooperbench.com-blue.svg)](https://cooperbench.com)
[![Dataset](https://img.shields.io/badge/HuggingFace-Dataset-yellow.svg)](https://huggingface.co/datasets/CodeConflict/cooperbench-dataset)
[![PyPI](https://img.shields.io/pypi/v/cooperbench.svg)](https://pypi.org/project/cooperbench/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**Can AI agents work together as teammates?** CooperBench is the first benchmark designed to measure how well AI agents can cooperate when handling individual tasks with potential conflicts.

We find that **coordinating agents perform much worse than a single agent** given the same total workload. This coordination deficit presents a fundamental barrier to deploying AI systems that can work alongside humans or other agents.

## Installation

```bash
pip install cooperbench
```

For development:

```bash
git clone https://github.com/cooperbench/CooperBench.git
cd CooperBench
pip install -e ".[dev]"
```

### Requirements

- Python 3.12+
- **Execution Backend** (choose one):
  - [Modal](https://modal.com) (default, cloud-based)
  - [GCP](https://cloud.google.com) (Google Cloud Platform)
  - Docker (local execution)
- Redis (for inter-agent communication in coop mode)

### Setup

#### Option 1: Modal (Default)

1. **Modal**: Sign up at [modal.com](https://modal.com) and run `modal setup`
2. **Redis**: Run locally with `docker run -p 6379:6379 redis:7` or use a cloud provider
3. **LLM API keys**: Set in `.env` file:

```bash
ANTHROPIC_API_KEY=your_key
OPENAI_API_KEY=your_key
GEMINI_API_KEY=your_key
```

#### Option 2: GCP (Recommended for Scale)

**Prerequisites**: Install [gcloud CLI](https://cloud.google.com/sdk/docs/install) first
- macOS: `brew install google-cloud-sdk`
- Linux: `curl https://sdk.cloud.google.com | bash`

**Setup**:
```bash
# 1. Install GCP dependencies
pip install 'cooperbench[gcp]'

# 2. Run configuration wizard (handles authentication, project setup, validation)
cooperbench config gcp

# 3. You're ready to run experiments!
cooperbench run --backend gcp -s lite
```

**Also needed**: Redis and LLM API keys (same as Option 1)

See [GCP Setup Guide](docs/GCP_SETUP.md) for detailed instructions.

### Dataset

Download the benchmark dataset from HuggingFace into `./dataset`:

```bash
cooperbench prepare
```

## Quick Start

### CLI

Run agents on a task:

```bash
# Run cooperative agents (N peers, shared Redis messaging)
cooperbench run -n my-experiment -r llama_index_task -m gpt-4o

# Run solo agent (1 agent handling both features)
cooperbench run -n my-experiment -r llama_index_task -m gpt-4o --setting solo

# Run team mode (lead + members, shared task list + scratchpad)
cooperbench run -n my-experiment -r llama_index_task -m gpt-4o --setting team

# Evaluate results
cooperbench eval -n my-experiment
```

### Python API

```python
from cooperbench import run, evaluate

# Run agents
run(
    run_name="my-experiment",
    repo="llama_index_task",
    model_name="gpt-4o",
    setting="coop",  # or "solo", "team"
)

# Evaluate patches
evaluate(run_name="my-experiment")
```

## Settings: solo / coop / team

CooperBench supports three settings, selected via `--setting`:

- **`solo`** — one agent implements every feature in the task.  The
  agent works alone in a single container; no Redis, no git server.
- **`coop`** — N peer agents, each assigned one feature.  Containers
  talk via Redis (`coop-send` / `coop-recv` shell commands inside the
  container, auto-injected as user messages in the Python-loop
  adapters).  Optional `--git` enables a shared `team` git remote so
  agents can fetch/merge each other's branches.
- **`team`** — N agents organized as one **lead** + N-1 **members**,
  with a Redis-backed shared **task list** (atomic claim via
  `coop-task-claim`), a shared **scratchpad** volume mounted at
  `/workspace/shared` in every container, and role-specific system
  prompts.  The lead's prompt directs them to organize work via
  `coop-task-create` / `coop-task-list` before coding; members claim
  open tasks and report progress via `coop-task-update`.

Team-mode result.json includes a `metrics` block with coordination
indicators computed from the task-list audit log: `tasks_total`,
`tasks_done`, `unowned_at_end`, `time_to_first_claim_seconds`,
`claims_per_agent`, `updates_per_agent`.

All three settings are evaluated the same way (`cooperbench eval`):
per-feature tests against either the single agent's patch (solo) or
each agent's individual patch (coop / team).

## Running with Harbor

CooperBench is also available as a [Harbor](https://github.com/harbor-framework/harbor) adapter, which provides parallelized cloud execution on [Modal](https://modal.com) via Docker-in-Docker sandboxes, built-in oracle validation, and standardized result collection.

Requires Modal authentication (run `modal setup`) and an LLM API key.

### Install and Prepare Tasks

```bash
# Install Harbor
uv tool install harbor

# Clone Harbor and prepare the adapter
git clone https://github.com/harbor-framework/harbor.git
cd harbor/adapters/cooperbench
uv sync

# Generate flash subset (50 pairs) with openhands-sdk harness
uv run python -m cooperbench.main \
  --subset flash \
  --agent-harness openhands-sdk \
  --output-dir ../../datasets/cooperbench
```

### Run on Modal

```bash
cd ../..  # back to harbor root

# Set up .env with your API key
echo "GEMINI_API_KEY=your_key" > .env

# Run the flash subset (50 tasks, concurrency 10)
uv run harbor run -p datasets/cooperbench --agent nop -e modal \
  --env-file .env --n-concurrent 10 \
  --ae COOPERBENCH_MODEL=gemini/gemini-3-flash-preview

# Run oracle (validates infrastructure, expects 100% pass)
uv run harbor run -p datasets/cooperbench --agent oracle -e modal \
  --env-file .env --n-concurrent 28
```

See the [Harbor CooperBench adapter](https://github.com/harbor-framework/harbor/tree/main/adapters/cooperbench) for full documentation.

## CLI Reference

### `cooperbench config`

Configure execution backends (GCP, Modal, etc.).

```bash
# Configure GCP backend
cooperbench config gcp

# Skip validation tests for faster setup
cooperbench config gcp --skip-tests
```

See [GCP Setup Guide](docs/GCP_SETUP.md) for details.

### `cooperbench run`

Run agents on benchmark tasks.

```bash
cooperbench run -n NAME [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `-n, --name` | Experiment name (required) | - |
| `-r, --repo` | Filter by repository | all |
| `-t, --task` | Filter by task ID | all |
| `-f, --features` | Feature pair (e.g., `1,2`) | all pairs |
| `-m, --model` | LLM model | `gemini/gemini-3-flash-preview` |
| `-a, --agent` | Agent framework | `mini_swe_agent` |
| `-c, --concurrency` | Parallel tasks | `20` |
| `--setting` | `coop` or `solo` | `coop` |
| `--backend` | `modal`, `docker`, or `gcp` | `modal` |
| `--redis` | Redis URL | `redis://localhost:6379` |
| `--git` | Enable git collaboration | disabled |
| `--no-messaging` | Disable agent messaging | enabled |
| `--force` | Rerun existing results | skip |
| `--agent-config` | Path to agent config file | none |

**Agent Configuration**: Pass agent-specific parameters via a config file. CooperBench forwards the file path to your agent without parsing it.

### `cooperbench eval`

Evaluate completed runs.

```bash
cooperbench eval -n NAME [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `-n, --name` | Experiment name (required) | - |
| `-r, --repo` | Filter by repository | all |
| `-t, --task` | Filter by task ID | all |
| `-f, --features` | Feature pair (e.g., `1,2`) | all pairs |
| `-c, --concurrency` | Parallel evaluations | `10` |
| `--backend` | `modal`, `docker`, or `gcp` | `modal` |
| `--force` | Re-evaluate existing | skip |

## Experiment Settings

| Setting | Agents | Description |
|---------|--------|-------------|
| `coop` | 2 | Two agents with Redis messaging, each handles one feature |
| `solo` | 1 | Single agent handles both features sequentially |

## Dataset Structure

```
dataset/
  <repo_name>/
    task<id>/
      setup.sh          # Repository setup script
      run_tests.sh      # Test runner script
      feature1/
        feature.md      # Feature description
        feature.patch   # Golden implementation
        tests.patch     # Test cases
      feature2/
        ...
```

## Output Structure

Results are saved to `logs/`:

```
logs/<run_name>/<repo>/task<id>/features_<i>_<j>/
  agent1/
    trajectory.json     # Full agent trajectory
    patch.diff          # Generated patch
  agent2/
    ...
  eval.json             # Evaluation results
```

## Benchmark Statistics

| Metric | Value |
|--------|-------|
| Tasks | 652 |
| Repositories | 12 |
| Languages | Python, TypeScript, Go, Rust |

## Key Findings

1. **Agents perform worse together than alone** — GPT-5 and Claude Sonnet 4.5 achieve only 25% success with two-agent cooperation, roughly 50% lower than when a single agent handles both tasks.

2. **Communication reduces conflicts but not failures** — Agents spend up to 20% of their budget on communication, reducing merge conflicts but not improving overall success.

3. **Three capability gaps underlie coordination failures**:
   - **Expectation failures (42%)** — agents fail to integrate partner state information
   - **Communication failures (26%)** — questions go unanswered, breaking decision loops
   - **Commitment failures (32%)** — agents break promises or make unverifiable claims

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run integration tests (requires Modal)
pytest tests/ -v --run-modal

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/cooperbench/
```

## Citation

```bibtex
@article{cooperbench2026,
  title={CooperBench: Why Coding Agents Cannot be Your Teammates Yet},
  author={Khatua*, Arpandeep and Zhu*, Hao and Tran†, Peter and Prabhudesai†, Arya
          and Sadrieh†, Frederic and Lieberwirth†, Johann K. and Yu, Xinkai
          and Fu, Yicheng and Ryan, Michael J. and Pei, Jiaxin and Yang, Diyi},
  journal={arXiv preprint},
  year={2026},
  url={https://arxiv.org/abs/2601.13295},
  note={*Equal contribution (Stanford) · †Equal contribution (SAP Labs)}
}
```

## License

MIT


## Harbor parity

How the original-side numbers of the Harbor parity experiment are produced.

- Code: this branch = upstream `main` at `63b9d44d9f39a02fccf5bf0052db48a917a011fd` plus one evaluator
  fix: `_setup_branches` (`src/cooperbench/eval/sandbox.py`) sets `core.hooksPath=/dev/null` before
  committing the agents' patches. Without it, react-hook-form's husky pre-commit hook (`pnpm type` =
  `tsc --noEmit`, then lint-staged) runs inside the evaluator; for `react_hook_form_task/task85` the
  gold `combined.patch` fails `tsc`, the commit is rejected, the merged diff is empty and every pair
  scores 0 even for the reference solution. The Harbor adapter applies the same line.
- Setting: `coop`, agent `mini_swe_agent_v2` with its default `coop.yaml`, messaging on, git off,
  `--backend docker`, auto-evaluation on (all upstream defaults).
- Model: `openai/deepseek-v4.1-flash` through an OpenAI-compatible endpoint
  (`OPENAI_API_KEY`, `OPENAI_BASE_URL`).
- Two dependency pins: `litellm==1.108.3`, the version the Harbor side runs (Harbor 0.25.0 needs
  litellm >= 1.92, so the lock's 1.81.12 cannot be used there), and `mcp==2.3.0`, because
  litellm 1.108.3 imports `mcp.types.CacheableResult` in `completion()` and the lock's mcp 1.26.0
  does not have it. Same versions on both sides.
- Pairs: `dataset/subsets/harbor_parity.json`: 15 of the 652 coop pairs, stratified by repository
  (every one of the 12 repositories gets at least one pair, the rest follow each repository's share
  of pairs, largest remainder), drawn uniformly within each repository with seed 42. It covers all
  four languages (Python, Go, TypeScript, Rust). The Harbor adapter's `--split parity` is the same
  file.

- Images: the harness pulls `akhatua/cooperbench-<repo>:task<id>` by tag. The parity runs check
  after each shard that every pulled image has the digest pinned in
  `scripts/harbor_image_digests.json` (the same pins as the Harbor adapter's `image_digests.json`).
  dockerd uses `--registry-mirror=https://mirror.gcr.io` (Google's Docker Hub mirror, same digests)
  because Docker Hub's anonymous limit (100 pulls/hour per IP) is hit on shared cloud egress.

Changes on top of upstream `63b9d44` (`git log 63b9d44..harbor-parity`):

| Change | Files | Affects scoring |
|---|---|---|
| Evaluator: `git config core.hooksPath /dev/null` before committing patches | `src/cooperbench/eval/sandbox.py` (+3 lines) | Only where a repo's hooks reject the commit (react_hook_form task85: the gold patch scored 0) |
| Parity runner for Daytona | `scripts/harbor_parity_daytona.py`, `scripts/harbor_image_digests.json` | No |
| Parity pair subset | `dataset/subsets/harbor_parity.json` | No (selects pairs) |
| This documentation | `README.md` | No |

Command (any machine with Docker and Redis):

```bash
uv sync --frozen && uv pip install litellm==1.108.3 mcp==2.3.0
redis-server --daemonize yes
# --no-sync keeps the two pins (a plain `uv run` would sync back to the lock file)
uv run --frozen --no-sync cooperbench run --setting coop -a mini_swe_agent_v2 \
    -m openai/deepseek-v4.1-flash --subset harbor_parity --backend docker -n <run-name>
```

The parity runs used the runner, which does the same inside Daytona sandboxes from
`docker:28.3.3-dind` (dockerd + redis-server per sandbox, pairs sharded by task so each image is
pulled once per sandbox) and downloads `logs/<run-name>/`:

```bash
export DAYTONA_API_KEY=... OPENAI_API_KEY=... OPENAI_BASE_URL=...
python scripts/harbor_parity_daytona.py --run-name <run-name> \
    --pairs dataset/subsets/harbor_parity.json --commit <harbor-parity commit> \
    --shards <k> --concurrency 3 --cpu 12 --memory 36 --disk 30 --out <dir>
```

Each shard is one sandbox (12 CPUs, 36 GB for 3 concurrent pairs: 4 CPUs and 12 GB per pair, as
on the Harbor side). `--pairs` takes an upstream subset file or a list of
`{"repo", "task_id", "features"}`. The runner exits non-zero unless every shard's harness exited 0,
every pair has `result.json` and `eval.json`, and every image carries its pinned digest; the
logs are downloaded either way.
