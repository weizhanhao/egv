# EGV — Evidence-Grounded Verification

> A persistent, named AI test team that lives with your project from day one.

EGV is an AI testing methodology + 8-agent team architecture. Unlike a CI tool that runs at PR time, EGV agents are **always present** — they participate in brainstorming, review requirements, evaluate designs, run verification at commit time, and reflect post-merge. They accumulate per-project knowledge via a git-tracked Model Keeper.

[![Test count](https://img.shields.io/badge/tests-30%2B%20passing-brightgreen)]() [![Agents](https://img.shields.io/badge/agents-8-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]()

---

## Why EGV exists

AI-generated code has two root failure modes:
1. **No execution grounding** — AI predicts behavior instead of running it
2. **No persistent system model** — AI rebuilds context every session

EGV addresses both with: (1) tooling that forces evidence-backed verification, and (2) a persistent per-project knowledge base shared across the developer team.

## Quick install

```bash
# Clone + install to ~/.claude/skills/egv-verify
git clone https://github.com/YOUR-GITHUB-USER/egv-verify ~/egv-verify-src
cd ~/egv-verify-src
bash install.sh
```

Verify install:
```bash
bash ~/egv-verify-src/install.sh --check
```

## Use it on your project

```bash
cd <your_project>

# 1. Create the Model Keeper for this project
python3 ~/.claude/skills/egv-verify/lib/egv-init.py

# 2. Edit .egv/project-keeper.json — set framework_config + critical_flows for your project

# 3. Commit it to git (so your team shares the same EGV brain)
git add .egv && git commit -m "chore: initialize EGV"

# 4. Run a verification
python3 ~/.claude/skills/egv-verify/lib/run-egv.py HEAD --project-root .
```

Output: a 6-line verdict summary with PASS/FAIL/WARN + calibrated confidence per agent.

## The team

| # | Agent | Role |
|---|-------|------|
| 1 | 🧠 Model Keeper | Persistent project knowledge (infrastructure) |
| 2 | 🎯 Core Flow Cartographer | Maintains critical_flows, participates in brainstorm |
| 3 | 💥 Regression Auditor | Blast radius analysis (weighted coverage × diff) |
| 4 | 🛡️ Critical Path Sentinel | Always-runs core flow tests |
| 5 | 🔒 Contract Sentinel | Type/schema breakage (`tsc --noEmit`) |
| 6 | ⚡ Performance Watch | Rolling p50 baselines on critical flow duration |
| 7 | 🔐 Security Scout | Secret regex + dangerous-pattern scan |
| 8 | ♿ A11y Auditor | Heuristic accessibility scan of UI file diffs |
| 9 | 🎓 QA Lead | Orchestrator — 6-way verdict synthesis |

## The 7 EGV Principles

1. **Skeptical Default** — AI's "looks fine" doesn't count
2. **Persistent External Mind** — Knowledge lives outside any AI context
3. **Blast Radius First** — Measure before testing
4. **Evidence Over Verdict** — Every conclusion cites the proof
5. **Multi-Perspective Disagreement Is Signal** — Don't average; escalate
6. **Calibrated Uncertainty** — Confidence is earned, capped at 0.95
7. **Accumulated Learning** — The 100th run is sharper than the 1st

## Lifecycle commands

The team is involved in every phase:

```bash
# Brainstorm a new feature — 3 agents ask structured questions
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py brainstorm \
    --project-root . --idea "Add real-time collaboration"

# Review a requirements doc — Sentinel flags testability gaps
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py requirements \
    --project-root . --path docs/spec.md

# Design review — Contract Sentinel checks API/schema risks
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py design-review \
    --project-root . --path docs/api-design.md

# Post-merge reflection
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py reflect --project-root .

# Pattern detection (Principle 7 — learn from accumulated history)
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py learn \
    --project-root . --mode propose
```

## Prerequisites

- Python 3.10+
- Test framework that emits Istanbul-format coverage JSON (Vitest, Jest, etc.)
- `tsc` for Contract Sentinel (optional)
- `tsx` for the TypeScript blast-radius computer (auto via `npx`)
- Optional: `anthropic` SDK + `ANTHROPIC_API_KEY` for Layer 2 LLM completion

## Multi-user — team brain via git

The Model Keeper lives at `<project_root>/.egv/project-keeper.json` and is committed to your project's git. This means:
- Your `learn` propose → push → teammate's pull → teammate's next run reads it
- `total_invocations` aggregates across the entire developer team
- New team members inherit the full Model Keeper on first clone

Git conflicts on `.egv/project-keeper.json` are mostly additive (~95% auto-merge cleanly); the rest are normal merge conflicts.

## Uninstall

```bash
bash ~/egv-verify-src/uninstall.sh
```

## How EGV compares to existing tools

- **`jest --findRelatedTests` / `nx affected`**: identify tests for changed files. Don't validate "untouched things stay untouched" or have a disagreement signal.
- **Code review LLMs**: static review. Don't run tests, don't compute blast radius, don't have persistent project memory.
- **Meticulous.ai**: replays prod traffic; requires recorded sessions. EGV doesn't.

The wedge: **blast-radius computation + behavioral verification + persistent per-project model + explicit disagreement signaling**, in one workflow.

## State

- v0: POC (2 agents) → see `POC_VERDICT.md`
- v1: 5 agents + agent identity + multi-user git → see `docs/v1_VERDICT.md`
- v2: 8 agents + lifecycle commands + learning loop + Layer 2 scaffold → see `docs/v2_VERDICT.md`
- v3 (planned): real axe-core integration, Sentinel parallelization, CI/CD integration

## License

MIT — see LICENSE.

## Acknowledgements

Built methodology-first. The "team that grows with the project" framing came from a real conversation about why AI code feels unreliable — the answer wasn't "more tests" but "a team that has been on the project for 6 months".
