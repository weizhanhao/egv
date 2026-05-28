# Session State — 2026-05-28

## Where we are

POC for **EGV (Evidence-Grounded Verification)** — an AI test team methodology.

Vision recap: 9-agent team built around a persistent Model Keeper. v0 ships 4 agents:
1. 🧠 Model Keeper (file-based JSON knowledge base)
2. 🎯 Core Flow Cartographer (hand-curated `critical_flows` for v0)
3. 💥 Regression Auditor (blast radius via coverage × diff)
4. 🛡️ Critical Path Sentinel (always-runs core flows)
+ 🎓 QA Lead is the orchestrator (SKILL.md itself)

## Completed in this session (2026-05-28)

### Phase 1 ✅
- Cloned excalidraw to `poc/excalidraw/` (depth=200)
- Ran `yarn install` — succeeded
- Identified 3 target commits mapping to 3 core failure modes:
  - `c09e170b feat(editor): deselect on esc` — **Dynamic dispatch** (event handler → cross-file state)
  - `2dfcc6f0 chore: Remove startBoundElement from state` — **Shared state side effects**
  - `0457ac90 fix(editor): handle invalid points on restore` — **Utility fanout**

### Phase 2 ✅
All v0 skill files written:
- `SKILL.md` — orchestrator workflow + EGV 7 principles enacted
- `model-keeper/projects/excalidraw.json` — 8 critical flows + framework config
- `agents/regression-auditor.md` — auditor role prompt
- `agents/critical-path-sentinel.md` — sentinel role prompt
- `lib/coverage-diff.ts` — TypeScript blast-radius computer
- `lib/run-coverage.sh` — bash coverage runner wrapper

### Technical smoke test ✅
Ran `coverage-diff.ts` on real diff (commit 0457ac90) + synthetic Istanbul coverage. Output verified correct:
- Real diff parsed: 3 files, 183 changed lines extracted
- Coverage attribution: 92/183 lines covered, ratio 50% (matches manual verification)
- Per-file classification correct (partially_covered / not_in_coverage)
- Confidence inputs populated (data freshness, coverage availability)

## NOT yet done (Phase 3 / Phase 4 — next session)

### Phase 3: real end-to-end run
1. Run `yarn test:coverage` on excalidraw at HEAD — produces real `coverage/coverage-final.json` (estimated 5-10 min runtime)
2. For each of the 3 target commits:
   - Checkout the parent commit
   - Apply only the changes from the target commit (cherry-pick)
   - Run skill end-to-end (Regression Auditor + Critical Path Sentinel + QA Lead synthesis)
   - Capture report
3. Compare skill output to "ground truth":
   - What did the commit's own test changes target? (the author's expert opinion)
   - What did the PR description say was affected?
   - Did our Critical Path Sentinel correctly run only paths that matter?

### Phase 4: POC verdict
Score each of the 3 failure modes against:
- ✅/❌/⚠️ — did the skill produce a correct, actionable report?
- Score each of EGV's 7 principles — did v0 actually enact them?
- Verdict: enter v0 spec OR adjust methodology OR kill the project

## Known issues / open questions for next session

1. **Vitest coverage scope**: excalidraw is a monorepo. `yarn test:coverage` might cover only the root or all workspaces? Need to verify. If only root, may need per-package coverage runs.
2. **Test file coverage**: Test files themselves usually aren't included in coverage; coverage-diff.ts correctly identifies these as `not_in_coverage`. For EGV's report, test file changes should be **excluded from blast-radius risk** since they're not production code. Need to add a filter.
3. **Subagent dispatch in real run**: SKILL.md says to dispatch Regression Auditor and Critical Path Sentinel as subagents via the Agent tool. In Phase 3, decide: use real subagents (more faithful, more cost) or role-rotate within the orchestrator (faster POC).
4. **Critical Path Sentinel test selection**: For excalidraw, running all 8 core flow tests is fast. For larger projects, may need a strategy.

## File map

```
poc/
├── excalidraw/                       # target (cloned)
└── egv-skill/                        # the skill being POCed
    ├── SKILL.md                      # orchestrator + EGV principles
    ├── SESSION_STATE.md              # this file
    ├── agents/
    │   ├── regression-auditor.md
    │   └── critical-path-sentinel.md
    ├── lib/
    │   ├── coverage-diff.ts
    │   └── run-coverage.sh
    ├── model-keeper/
    │   └── projects/
    │       └── excalidraw.json
    └── reports/                      # populated during Phase 3 runs
```

## Resume command for next session

```bash
ls /Users/weizhanhao/fortest/poc/egv-skill/
cat /Users/weizhanhao/fortest/poc/egv-skill/SESSION_STATE.md

cd /Users/weizhanhao/fortest/poc/excalidraw
yarn test:coverage --run
```
