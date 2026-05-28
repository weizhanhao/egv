---
name: egv-verify
description: Verify AI code changes with the EGV (Evidence-Grounded Verification) AI test team. Runs an 8-agent verification pipeline — Regression Auditor, Critical Path Sentinel, Contract Sentinel, Performance Watch, Security Scout, A11y Auditor, plus Core Flow Cartographer and QA Lead synthesizer — against a diff, reads/writes a per-project persistent Model Keeper, and produces a 6-dimensional verdict (PASS/FAIL/WARN) with calibrated confidence. Each agent has persistent identity, accumulated memory, and grows with the project. Includes lifecycle commands for brainstorm/requirements/design-review/reflection phases.
origin: EGV
---

# EGV Verify — AI Code Change Verification Team

A persistent, named AI test team that lives with each project from day one. The team grows with the codebase — every run feeds back into a per-project Model Keeper that accumulates project-specific knowledge.

## The 7 EGV Principles

1. **Skeptical Default** — AI's "looks fine" doesn't count. Every claim needs an execution artifact.
2. **Persistent External Mind** — Project knowledge lives outside any AI context.
3. **Blast Radius First** — Measure what could be affected before deciding what to verify.
4. **Evidence Over Verdict** — Every conclusion cites the proof artifact.
5. **Multi-Perspective Disagreement Is Signal** — When agents disagree, escalate.
6. **Calibrated Uncertainty** — Confidence is earned, capped at 0.95.
7. **Accumulated Learning** — Every run feeds back; the 100th run is sharper than the 1st.

## The 8-agent team

| # | Agent | Role |
|---|-------|------|
| 1 | 🧠 Model Keeper | Persistent project knowledge (infrastructure) |
| 2 | 🎯 Core Flow Cartographer | Maintains critical_flows list, participates in brainstorm |
| 3 | 💥 Regression Auditor | Blast radius analysis (weighted coverage × diff) |
| 4 | 🛡️ Critical Path Sentinel | Always-runs core flow tests |
| 5 | 🔒 Contract Sentinel | Type/schema breakage (`tsc --noEmit`) |
| 6 | ⚡ Performance Watch | Rolling p50 baselines on critical flow duration |
| 7 | 🔐 Security Scout | Secret regex + dangerous-pattern scan of diff |
| 8 | ♿ A11y Auditor | Heuristic accessibility scan of UI file diffs |
| 9 | 🎓 QA Lead | Orchestrator — 6-way verdict synthesis |

## When to invoke

- After AI makes non-trivial code changes — before `git push` / PR creation
- When user asks "is this safe to ship?" or "what could this change break?"
- At lifecycle points: feature brainstorm, requirements review, design review, post-merge reflection

## Quick start

### Setup for a new project

1. Create the Model Keeper at `<project_root>/.egv/project-keeper.json`. Template structure:
   ```json
   {
     "schema_version": "1.0.0",
     "project_name": "myproject",
     "project_root": "/abs/path/to/project",
     "framework": "vitest+playwright",
     "framework_config": {
       "test_runner": "vitest",
       "coverage_command": "yarn test:coverage --run",
       "coverage_output_path": "coverage/coverage-final.json",
       "test_file_pattern": "**/*.test.{ts,tsx}",
       "typecheck_command": "yarn test:typecheck",
       "sentinel_test_command": "yarn test --run {test_path}"
     },
     "critical_flows": [
       {"name": "core-flow-1", "test_path": "tests/core1.test.ts", "owning_code_paths": ["src/core.ts"]}
     ],
     "team_identity": {"project": "myproject", "team_founded_at": "2026-05-28T00:00:00Z", "agents": {}},
     "file_importance_weights": {
       "patterns": [
         {"glob": "**/*.d.ts", "weight": 0.0, "reason": "type-only"},
         {"glob": "**/*.snap", "weight": 0.0, "reason": "auto-generated"},
         {"glob": "**/types.ts", "weight": 0.0, "reason": "type-only"}
       ],
       "default": 1.0
     },
     "learned_patterns": [],
     "lifecycle_artifacts": {"ideas_under_consideration": [], "design_reviews_completed": [], "post_merge_reflections": [], "synthesis_proposals": []},
     "known_blind_spots": [],
     "run_history": [],
     "flow_baselines": {},
     "created_at": "2026-05-28T00:00:00Z",
     "last_updated": "2026-05-28T00:00:00Z"
   }
   ```

2. Commit `.egv/project-keeper.json` to the project's git repo. The team is now shared across all developers.

### Verify a diff

```bash
# EGV uses the `claude` CLI directly — no separate API key needed.
# Make sure you've got Claude Code installed and `claude --version` works.

# Verify a commit SHA or staged diff
python3 ~/.claude/skills/egv-verify/lib/run-egv.py <commit-sha-or-HEAD> --project-root /path/to/project

# Optional: enable Layer 2 (LLM-driven test synthesis for uncovered lines)
python3 ~/.claude/skills/egv-verify/lib/run-egv.py <sha> --project-root /path/to/project --enable-layer2
```

Output: a phase-1/phase-2/QA-Lead verdict summary and a verdict.md report in `reports/<run_id>/` containing the full team conversation.

### Lifecycle commands

```bash
# Brainstorm — team asks structured questions about a new idea
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py brainstorm \
    --project-root /path/to/project --idea "Add real-time collaboration"

# Requirements — annotate a doc with testability gaps
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py requirements \
    --project-root /path/to/project --path docs/spec.md

# Design review — Contract Sentinel reviews API/schema design
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py design-review \
    --project-root /path/to/project --path docs/api-design.md

# Reflect — team surfaces patterns from accumulated run history
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py reflect \
    --project-root /path/to/project

# Learn — pattern detector proposes Auditor weight tunings
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py learn \
    --project-root /path/to/project --mode propose
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py learn \
    --project-root /path/to/project --mode list-pending
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py learn \
    --project-root /path/to/project --mode approve --pattern-id <id>

# Layer 2 review — list/approve/reject pending synthesis proposals
python3 ~/.claude/skills/egv-verify/lib/egv_cli.py layer2-review \
    --project-root /path/to/project --mode list
```

## Prerequisites

EGV v3 is a REAL LLM agent team — every run, all 8 agents use Claude to reason about your code. This is mandatory.

- **Claude Code installed** (provides the `claude` CLI). If you can run `claude --version`, you're set.
- Python 3.10+
- Test framework that emits Istanbul-format coverage JSON (Vitest, Jest, etc.)
- `tsc` for Contract Sentinel (optional — skip if not TypeScript)
- `tsx` (for running the TypeScript blast-radius computer) — auto-installed via npx

**No separate API key required** — EGV calls `claude -p` (non-interactive print mode), which uses your existing Claude Code authentication (subscription or API key, whatever you've already configured for Claude Code).

**No Python SDK install required** — EGV uses subprocess, bypasses PEP 668.

## Cost

Each verification run makes ~13 Claude calls (6 phase-1 + 6 phase-2 + 1 QA Lead). Cost is tracked per run and printed in the summary. Typical run: **~$0.05-0.20** with Haiku for most agents and Sonnet for the two "thinking" roles (Auditor + QA Lead).

If you're on a Claude Pro/Max subscription, this comes out of your usage allowance — no extra billing.

## How the team collaborates each run

EGV v3 runs every verification as a three-phase team process:

### Phase 1: Independent investigation
Each of 6 specialist agents (Auditor, Sentinel, Contract Sentinel, Performance Watch, Security Scout, A11y Auditor) does their work independently:
1. Their sensor (regex, coverage, typecheck, tests) gathers deterministic data
2. They consult their accumulated identity (recent findings, tenure on this project)
3. They call Claude with their role-specific prompt + sensor data + identity history
4. They produce a structured finding: verdict, narrative, recommendations

### Phase 2: Team review
Each agent now sees ALL the team's phase-1 findings. They get a chance to:
- STAND BY their phase-1 verdict if it still holds
- ESCALATE if another agent's finding makes them upgrade concern
- DE-ESCALATE if another agent's finding reframes their own as a false positive
- Raise CROSS-CUTTING concerns no single agent flagged

### Phase 3: QA Lead synthesis
The QA Lead reads both phases for every agent. The final verdict and the team summary come from QA Lead's reasoning over the dialogue, not a switch statement.

This is what "real agent team" means here: every run, the team thinks together. Not 6 functions producing 6 reports — 6 specialists arguing toward a verdict, with the QA Lead synthesizing.

## Multi-user workflow

The Model Keeper at `<project_root>/.egv/project-keeper.json` is committed to the project's git repo. This means:
- Alice's `propose` of a learned_pattern → push → Bob's next run reads it
- Critical flow promotions, weight adjustments, lifecycle artifacts are all shared
- `team_identity.agents.*.total_invocations` aggregates across the entire developer team
- New team members inherit the full Model Keeper on first clone

Conflicts during git pull: writes are mostly additive (append to run_history, recent_findings, etc.), so git's text merge handles ~95% cleanly. The remaining ~5% are standard merge conflicts resolved like any other code conflict.

## Architecture

```
egv-verify/
├── SKILL.md                      this file
├── README.md                     installation + usage
├── agents/                       agent role prompts (for reference)
│   ├── regression-auditor.md
│   └── critical-path-sentinel.md
└── lib/
    ├── model_keeper.py           v1 schema, provenance, save validation
    ├── agent_identity.py         8 agent identities with tenure scoring
    ├── coverage-diff.ts          weighted blast radius computation
    ├── run-egv.py                main orchestrator — dispatches all agents
    ├── lifecycle.py              brainstorm/requirements/design-review/reflect
    ├── learning.py               pattern detection + approval gate
    ├── layer2.py                 E2E synthesis (stub + optional LLM)
    ├── egv_cli.py                unified CLI: 7 subcommands
    ├── migrate-v0-to-v1.py
    ├── run-coverage.sh
    ├── requirements.txt          (anthropic SDK for Layer 2 LLM)
    └── test_*.py / test_*.sh     30 tests, all passing
```

## Verdict semantics

| Agent | What it checks | Verdict basis |
|-------|---------------|---------------|
| Regression Auditor | Existing tests cover changed code | Weighted coverage ratio of changed prod lines |
| Critical Path Sentinel | Core user flows still work | All critical_flow tests pass |
| Contract Sentinel | Public API / type contracts intact | `tsc --noEmit` exit code + error correlation |
| Performance Watch | No flow timing regression | Rolling p50 vs current run, ≥25% slowdown = WARN |
| Security Scout | No secrets, no danger patterns | Regex scan of `+` diff additions |
| A11y Auditor | UI changes don't break a11y | Heuristic regex on UI file additions (v3: axe-core) |
| QA Lead | Combine all signals | min(confidences) × agreement_factor |

QA Lead synthesis rules:
- All 6 agree → final verdict = agreed verdict, confidence = min(confidences)
- Any FAIL → final = FAIL
- Any disagreement → final confidence × 0.6
- Security FAIL always wins
- Performance / A11y WARN can upgrade PASS → WARN

## Honest scope notes (v3 follow-ups)

- **Layer 2 LLM**: requires `anthropic` SDK in a venv + `ANTHROPIC_API_KEY`. Falls back to stub generation if unavailable.
- **A11y Auditor**: heuristic regex scaffold. Real WCAG checking needs axe-core via Playwright MCP (v3).
- **Sentinel parallelization**: flows currently run serially (v3).
- **CI/CD integration**: no GitHub Actions / GitLab CI integration yet (v3).

These boundaries are sharp, not fuzzy. Each is a documented work item.
