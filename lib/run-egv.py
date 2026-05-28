#!/usr/bin/env python3
"""run-egv.py — EGV v3 REAL agent team orchestrator.

Every verification run is a three-phase team meeting:

  Phase 1 — Independent investigation
    Each of the 6 specialist agents (Auditor, Sentinel, Contract, Performance,
    Security, A11y) runs its sensors AND consults Claude with role prompt +
    accumulated identity. The result is a structured finding: verdict,
    narrative, recommendations.

  Phase 2 — Team review
    Each agent gets shown ALL six phase-1 findings. They respond as teammates:
    STAND BY / ESCALATE / DE-ESCALATE / CROSS-CONCERN. This is the real team
    part — verdicts can move based on cross-cutting signal.

  Phase 3 — QA Lead synthesis
    The QA Lead (also LLM) reads both phases and produces the final verdict
    with narrative explaining how the team converged (or didn't).

LLM is MANDATORY. Missing `claude` CLI = clear FATAL error, exit 2.
Uses `claude -p` (non-interactive) which reads existing Claude Code auth.
No SDK or separate API key required.

Usage:
  python3 run-egv.py <commit-sha-or-diff-path> \\
      --project-root /path/to/project [--selected-flows name1,name2]

Output: reports/<run_id>/{verdict.md, verdict.json, <agent>.json, <agent>-review.json}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_identity import read_agent_identity, write_agent_identity
from llm import (
    HAIKU,
    SONNET,
    LLMAgent,
    LLMResult,
    LLMUnavailableError,
    require_claude_cli,
)
from model_keeper import (
    keeper_path_for_project_root,
    load_model_keeper,
    now_iso,
    save_model_keeper,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Agent role registry — used by phase-2 team review
# ---------------------------------------------------------------------------

AGENT_ROLES: dict[str, tuple[str, str, str, str]] = {
    # role_key: (display_role_name, identity_key, model, role_prompt)
    "auditor": (
        "Regression Auditor",
        "regression-auditor",
        SONNET,
        "You are the Regression Auditor for this project's EGV team. You judge "
        "whether code changes are safe based on coverage x diff data. You speak "
        "in concrete terms about specific files and modules. You measure blast "
        "radius before claiming safety.",
    ),
    "sentinel": (
        "Critical Path Sentinel",
        "critical-path-sentinel",
        HAIKU,
        "You are the Critical Path Sentinel. You run the project's real "
        "core-flow tests and report on pass/fail/missing. You care about which "
        "user-visible behaviors are verified end-to-end.",
    ),
    "contract": (
        "Contract Sentinel",
        "contract-sentinel",
        HAIKU,
        "You are the Contract Sentinel. You watch for type/schema breakage. "
        "Errors INSIDE the diff are blocking; pre-existing errors are noise.",
    ),
    "perf": (
        "Performance Watch",
        "performance-watch",
        HAIKU,
        "You are Performance Watch. You compare run timings against rolling "
        "p50 baselines and flag regressions >= 25%. You don't get excited "
        "about noise on tiny baselines.",
    ),
    "security": (
        "Security Scout",
        "security-scout",
        HAIKU,
        "You are the Security Scout. Regex caught what regex catches. Look at "
        "diff additions for things regex CAN'T catch: subtle injection patterns, "
        "unsafe deserialization, JWT misuse, weak crypto. Be specific.",
    ),
    "a11y": (
        "A11y Auditor",
        "a11y-auditor",
        HAIKU,
        "You are the A11y Auditor. You scan UI diff additions for common "
        "accessibility anti-patterns. You stay advisory — cap your verdict at "
        "WARN unless something genuinely breaks assistive tech.",
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_run_id(commit_or_diff: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = re.sub(r"[^a-zA-Z0-9]", "-", commit_or_diff)[:20]
    return f"{stamp}__{tag}"


def get_diff(arg: str, project_root: Path) -> Path:
    if os.path.isfile(arg):
        return Path(arg).resolve()
    diff_path = Path(f"/tmp/egv-diff-{arg}.patch")
    subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "diff",
            "--unified=0",
            "--no-color",
            f"{arg}~1..{arg}",
        ],
        check=True,
        stdout=diff_path.open("w"),
    )
    return diff_path


def _make_weight_lookup(weights: dict):
    import fnmatch

    patterns = weights.get("patterns", [])
    default = weights.get("default", 1.0)

    def _glob_match(path: str, glob: str) -> bool:
        if "{" in glob and "}" in glob:
            prefix, rest = glob.split("{", 1)
            alts_str, suffix = rest.split("}", 1)
            for alt in alts_str.split(","):
                if fnmatch.fnmatch(path, prefix + alt + suffix):
                    return True
            return False
        return fnmatch.fnmatch(path, glob)

    def lookup(path: str) -> float:
        for p in patterns:
            if _glob_match(path, p["glob"]):
                return float(p["weight"])
        return float(default)

    return lookup


def _llm_result_to_dict(r: LLMResult) -> dict:
    return {
        "verdict": r.verdict,
        "narrative": r.narrative,
        "recommendations": r.recommendations,
        "confidence_basis": r.confidence_basis,
    }


# ---------------------------------------------------------------------------
# Phase 1 — Independent investigation (one function per agent)
# ---------------------------------------------------------------------------


def _make_agent(role_key: str) -> LLMAgent:
    role_name, _identity_key, model, prompt = AGENT_ROLES[role_key]
    return LLMAgent(role_name=role_name, role_prompt=prompt, model=model)


def run_regression_auditor(
    diff_path: Path,
    coverage_path: Path,
    project_root: Path,
    run_dir: Path,
    model_keeper: dict,
) -> dict:
    print(
        f"[auditor] Running coverage-diff on {diff_path.name}...",
        file=sys.stderr,
    )

    agent_record = read_agent_identity(model_keeper, "regression-auditor")
    agent_record["total_invocations"] += 1

    weights = model_keeper.get(
        "file_importance_weights", {"patterns": [], "default": 1.0}
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as wf:
        json.dump(weights, wf)
        weights_path = wf.name

    blast_path = run_dir / "blast-radius.json"

    result = subprocess.run(
        [
            "npx",
            "--yes",
            "tsx@4",
            str(SKILL_ROOT / "lib" / "coverage-diff.ts"),
            str(diff_path),
            str(coverage_path),
            str(project_root),
            "--weights",
            weights_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    blast_path.write_text(result.stdout)
    blast = json.loads(result.stdout)
    summary = blast["summary"]

    coverage_ratio = summary.get(
        "weighted_coverage_ratio", summary["coverage_ratio"]
    )
    age = blast["confidence_inputs"].get("coverage_data_age_seconds") or 99999
    data_freshness = 1.0 if age < 3600 else 0.7
    test_pass_rate = 1.0
    confidence = round(
        min(0.95, coverage_ratio * test_pass_rate * data_freshness), 2
    )

    weighted_prod_lines = summary.get(
        "weighted_production_changed_lines", summary["production_changed_lines"]
    )
    weighted_covered_lines = summary.get(
        "weighted_covered_changed_lines", summary["covered_changed_lines"]
    )
    weighted_uncovered = weighted_prod_lines - weighted_covered_lines

    if weighted_uncovered > 0:
        det_verdict = "WARN"
    elif weighted_prod_lines == 0:
        det_verdict = "PASS"
        confidence = 0.95
    else:
        det_verdict = "PASS"

    weight_for = _make_weight_lookup(weights)
    warnings: list[str] = []
    for f in blast["files"]:
        if f["is_test_file"]:
            continue
        w = weight_for(f["file"])
        if w == 0.0:
            continue
        if f["coverage_status"] in ("uncovered", "not_in_coverage"):
            warnings.append(
                f"{f['file']}: {len(f['uncovered_lines'])} changed lines have no test coverage"
            )
        elif (
            f["coverage_status"] == "partially_covered"
            and len(f["uncovered_lines"]) > 0
        ):
            warnings.append(
                f"{f['file']}: {len(f['uncovered_lines'])} of {len(f['changed_lines'])} changed lines uncovered"
            )

    report = {
        "agent": "regression-auditor",
        "run_id": run_dir.name,
        "verdict": det_verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"weighted_coverage_ratio={coverage_ratio:.2f} x test_pass_rate={test_pass_rate} x "
            f"data_freshness={data_freshness} -> "
            f"{coverage_ratio * test_pass_rate * data_freshness:.2f} (capped at 0.95)"
        ),
        "blast_radius": {
            "total_changed_lines": summary["total_changed_lines"],
            "production_changed_lines": summary["production_changed_lines"],
            "test_changed_lines": summary["test_changed_lines"],
            "covered_changed_lines": summary["covered_changed_lines"],
            "uncovered_changed_lines": summary["uncovered_changed_lines"],
            "files": [
                {
                    "file": f["file"],
                    "status": f["coverage_status"],
                    "is_test_file": f["is_test_file"],
                    "uncovered_line_count": len(f["uncovered_lines"]),
                }
                for f in blast["files"]
            ],
        },
        "evidence": [str(blast_path), str(coverage_path)],
        "warnings": warnings,
        "generated_at": now_iso(),
    }

    # Phase 1 LLM investigation — MANDATORY
    auditor_agent = _make_agent("auditor")
    phase1 = auditor_agent.investigate(
        deterministic_data={
            "deterministic_verdict": det_verdict,
            "deterministic_confidence": confidence,
            "blast_radius": report["blast_radius"],
            "warnings": warnings,
            "summary": (
                f"{summary['production_changed_lines']} prod lines changed, "
                f"{summary['covered_changed_lines']} covered, "
                f"{coverage_ratio:.0%} ratio"
            ),
        },
        identity_record=agent_record,
    )
    report["verdict"] = phase1.verdict
    report["confidence"] = round(min(report["confidence"], 0.85), 2)
    report["llm_phase1"] = _llm_result_to_dict(phase1)

    auditor_path = run_dir / "regression-auditor.json"
    auditor_path.write_text(json.dumps(report, indent=2))
    print(
        f"[auditor] phase1 verdict={phase1.verdict} -- {phase1.narrative[:120]}",
        file=sys.stderr,
    )

    return {
        "deterministic": report,
        "phase1": phase1,
        "agent_role": "Regression Auditor",
    }


def run_critical_path_sentinel(
    model_keeper: dict,
    project_root: Path,
    run_dir: Path,
    selected_flows: list | None = None,
) -> dict:
    print("[sentinel] Running critical path tests...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "critical-path-sentinel")
    agent_record["total_invocations"] += 1

    flows = model_keeper["critical_flows"]
    if selected_flows is not None:
        flows = [f for f in flows if f["name"] in selected_flows]

    results = []
    for flow in flows:
        test_path = flow.get("test_path")
        if not test_path:
            results.append(
                {
                    "flow_name": flow["name"],
                    "test_path": None,
                    "status": "missing",
                    "duration_ms": 0,
                    "evidence_path": None,
                    "failure_excerpt": "test_path not configured in Model Keeper",
                }
            )
            continue

        flow_log = run_dir / f"flow-{flow['name']}.log"
        start = time.time()
        sentinel_cmd_template = model_keeper.get("framework_config", {}).get(
            "sentinel_test_command", "yarn test:app --run {test_path}"
        )
        sentinel_cmd = sentinel_cmd_template.format(test_path=test_path)
        rc = subprocess.call(
            sentinel_cmd,
            shell=True,
            cwd=str(project_root),
            stdout=flow_log.open("w"),
            stderr=subprocess.STDOUT,
        )
        duration_ms = int((time.time() - start) * 1000)

        if rc == 0:
            results.append(
                {
                    "flow_name": flow["name"],
                    "test_path": test_path,
                    "status": "passed",
                    "duration_ms": duration_ms,
                    "evidence_path": str(flow_log),
                    "failure_excerpt": None,
                }
            )
            print(
                f"[sentinel] PASS {flow['name']} ({duration_ms}ms)",
                file=sys.stderr,
            )
        else:
            log_tail = flow_log.read_text().split("\n")[-30:]
            excerpt = "\n".join(log_tail)
            results.append(
                {
                    "flow_name": flow["name"],
                    "test_path": test_path,
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "evidence_path": str(flow_log),
                    "failure_excerpt": excerpt[:500],
                }
            )
            print(f"[sentinel] FAIL {flow['name']} (rc={rc})", file=sys.stderr)

    verifiable = [r for r in results if r["status"] != "missing"]
    passed = [r for r in results if r["status"] == "passed"]
    failed = [r for r in results if r["status"] == "failed"]
    missing = [r for r in results if r["status"] == "missing"]

    verified_pass_ratio = (
        (len(passed) / len(verifiable)) if verifiable else 0.0
    )
    unverifiable_penalty = (
        (len(missing) / len(results)) if results else 0.0
    )
    confidence = round(
        min(0.95, verified_pass_ratio * (1 - 0.5 * unverifiable_penalty)), 2
    )

    if failed:
        det_verdict = "FAIL"
    elif missing:
        det_verdict = "WARN"
    elif passed:
        det_verdict = "PASS"
    else:
        det_verdict = "WARN"

    report = {
        "agent": "critical-path-sentinel",
        "run_id": run_dir.name,
        "verdict": det_verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"verified_pass_ratio={verified_pass_ratio:.2f} x "
            f"(1 - 0.5 x unverifiable_penalty={unverifiable_penalty:.2f}) = "
            f"{verified_pass_ratio * (1 - 0.5 * unverifiable_penalty):.2f} (capped at 0.95)"
        ),
        "flows_total": len(results),
        "flows_verified": len(passed),
        "flows_unverifiable": len(missing),
        "flows_failed": len(failed),
        "results": results,
        "evidence": [r["evidence_path"] for r in results if r["evidence_path"]],
        "generated_at": now_iso(),
    }

    sentinel_agent = _make_agent("sentinel")
    phase1 = sentinel_agent.investigate(
        deterministic_data={
            "deterministic_verdict": det_verdict,
            "flows_total": len(results),
            "flows_passed": len(passed),
            "flows_failed": len(failed),
            "flows_missing": len(missing),
            "results_summary": [
                {
                    "name": r["flow_name"],
                    "status": r["status"],
                    "duration_ms": r["duration_ms"],
                    "failure_excerpt": (r.get("failure_excerpt") or "")[:240],
                }
                for r in results
            ],
        },
        identity_record=agent_record,
    )
    report["verdict"] = phase1.verdict
    report["llm_phase1"] = _llm_result_to_dict(phase1)

    sentinel_path = run_dir / "critical-path-sentinel.json"
    sentinel_path.write_text(json.dumps(report, indent=2))
    print(
        f"[sentinel] phase1 verdict={phase1.verdict} -- {phase1.narrative[:120]}",
        file=sys.stderr,
    )

    return {
        "deterministic": report,
        "phase1": phase1,
        "agent_role": "Critical Path Sentinel",
    }


def run_contract_sentinel(
    diff_path: Path,
    project_root: Path,
    run_dir: Path,
    model_keeper: dict,
) -> dict:
    print("[contract] Running typecheck...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "contract-sentinel")
    agent_record["total_invocations"] += 1

    typecheck_cmd = model_keeper.get("framework_config", {}).get(
        "typecheck_command", "yarn test:typecheck"
    )

    log_path = run_dir / "contract-typecheck.log"
    rc = subprocess.call(
        typecheck_cmd,
        shell=True,
        cwd=str(project_root),
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
    )
    log_text = log_path.read_text()

    error_pattern = re.compile(
        r"^(?P<file>[^\s(]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>TS\d+):\s+(?P<msg>.+)$",
        re.MULTILINE,
    )
    all_errors = [
        {
            "file": m.group("file"),
            "line": int(m.group("line")),
            "code": m.group("code"),
            "msg": m.group("msg"),
        }
        for m in error_pattern.finditer(log_text)
    ]

    diff_text = diff_path.read_text()
    diff_files: set[str] = set()
    for line in diff_text.split("\n"):
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            diff_files.add(m.group(1))
    errors_in_diff = [
        e
        for e in all_errors
        if any(
            e["file"].endswith(df) or df.endswith(e["file"]) for df in diff_files
        )
    ]

    if rc != 0 and not all_errors:
        det_verdict = "FAIL"
        confidence = 0.0
        warnings_list = [
            "Typecheck exited non-zero but produced no parseable errors. Manual investigation required."
        ]
    elif errors_in_diff:
        det_verdict = "WARN"
        confidence = round(
            min(
                0.95,
                len(errors_in_diff) / max(1, len(all_errors)) * 0.95,
            ),
            2,
        )
        warnings_list = [
            f"{e['file']}:{e['line']} -- {e['code']}: {e['msg'][:120]}"
            for e in errors_in_diff[:5]
        ]
    elif all_errors:
        det_verdict = "WARN"
        confidence = 0.7
        warnings_list = [
            f"{len(all_errors)} pre-existing TS errors in repo (not caused by this diff)"
        ]
    else:
        det_verdict = "PASS"
        confidence = 0.95
        warnings_list = []

    report = {
        "agent": "contract-sentinel",
        "run_id": run_dir.name,
        "verdict": det_verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"errors_in_diff={len(errors_in_diff)} / total_errors={len(all_errors)}, "
            f"verdict={det_verdict}"
        ),
        "errors_in_diff_files": errors_in_diff[:20],
        "total_typecheck_errors": len(all_errors),
        "evidence": [str(log_path)],
        "warnings": warnings_list,
        "generated_at": now_iso(),
    }

    contract_agent = _make_agent("contract")
    phase1 = contract_agent.investigate(
        deterministic_data={
            "deterministic_verdict": det_verdict,
            "typecheck_exit_code": rc,
            "errors_in_diff_count": len(errors_in_diff),
            "errors_in_diff_sample": errors_in_diff[:10],
            "preexisting_errors_total": len(all_errors),
            "diff_files_count": len(diff_files),
        },
        identity_record=agent_record,
    )
    report["verdict"] = phase1.verdict
    report["llm_phase1"] = _llm_result_to_dict(phase1)

    contract_path = run_dir / "contract-sentinel.json"
    contract_path.write_text(json.dumps(report, indent=2))
    print(
        f"[contract] phase1 verdict={phase1.verdict} -- {phase1.narrative[:120]}",
        file=sys.stderr,
    )

    return {
        "deterministic": report,
        "phase1": phase1,
        "agent_role": "Contract Sentinel",
    }


def run_performance_watch(
    sentinel_det: dict,
    run_dir: Path,
    model_keeper: dict,
) -> dict:
    print(
        "[perfwatch] Comparing flow durations to baselines...",
        file=sys.stderr,
    )

    agent_record = read_agent_identity(model_keeper, "performance-watch")
    agent_record["total_invocations"] += 1

    baselines = model_keeper.setdefault("flow_baselines", {})
    SLOWDOWN_THRESHOLD = 1.25

    slowdowns: list[dict] = []
    updated_baselines: list[str] = []
    for r in sentinel_det.get("results", []):
        if r.get("status") != "passed":
            continue
        flow_name = r["flow_name"]
        duration = r["duration_ms"]
        base = baselines.get(flow_name)
        if base is None or base.get("n_observations", 0) == 0:
            baselines[flow_name] = {
                "p50_ms": duration,
                "n_observations": 1,
                "last_updated": now_iso(),
            }
            updated_baselines.append(flow_name)
        else:
            p50 = base["p50_ms"]
            if duration >= p50 * SLOWDOWN_THRESHOLD:
                slowdowns.append(
                    {
                        "flow_name": flow_name,
                        "duration_ms": duration,
                        "baseline_p50_ms": p50,
                        "slowdown_pct": round(
                            (duration - p50) / p50 * 100, 1
                        ),
                    }
                )
            n = base["n_observations"]
            new_p50 = int(p50 * 0.8 + duration * 0.2)
            baselines[flow_name] = {
                "p50_ms": new_p50,
                "n_observations": n + 1,
                "last_updated": now_iso(),
            }
            updated_baselines.append(flow_name)

    if slowdowns:
        det_verdict = "WARN"
        confidence = 0.85
        warnings_list = [
            f"{s['flow_name']}: {s['duration_ms']}ms vs baseline {s['baseline_p50_ms']}ms (+{s['slowdown_pct']}%)"
            for s in slowdowns
        ]
    elif not updated_baselines:
        det_verdict = "PASS"
        confidence = 0.0
        warnings_list = ["No passing flows to measure"]
    else:
        det_verdict = "PASS"
        confidence = 0.9
        warnings_list = []

    report = {
        "agent": "performance-watch",
        "run_id": run_dir.name,
        "verdict": det_verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"{len(updated_baselines)} flow(s) measured, {len(slowdowns)} slowdown(s) detected "
            f"(threshold: {int((SLOWDOWN_THRESHOLD-1)*100)}% slower than rolling p50)"
        ),
        "slowdowns": slowdowns,
        "baselines_updated": updated_baselines,
        "threshold_pct": int((SLOWDOWN_THRESHOLD - 1) * 100),
        "evidence": [],
        "warnings": warnings_list,
        "generated_at": now_iso(),
    }

    perf_agent = _make_agent("perf")
    phase1 = perf_agent.investigate(
        deterministic_data={
            "deterministic_verdict": det_verdict,
            "slowdowns": slowdowns,
            "baselines_updated_count": len(updated_baselines),
            "threshold_pct": int((SLOWDOWN_THRESHOLD - 1) * 100),
        },
        identity_record=agent_record,
    )
    report["verdict"] = phase1.verdict
    report["llm_phase1"] = _llm_result_to_dict(phase1)

    perf_path = run_dir / "performance-watch.json"
    perf_path.write_text(json.dumps(report, indent=2))
    print(
        f"[perfwatch] phase1 verdict={phase1.verdict} -- {phase1.narrative[:120]}",
        file=sys.stderr,
    )

    return {
        "deterministic": report,
        "phase1": phase1,
        "agent_role": "Performance Watch",
    }


# Regex patterns kept here for the security/a11y deterministic sensors.
SECRET_PATTERNS = [
    (re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), "stripe-key"),
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"), "openai-key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-access-key-id"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "github-personal-token"),
    (
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        "github-fine-grained-token",
    ),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
        "private-key-block",
    ),
    (
        re.compile(
            r"(?i)(?:password|passwd|pwd|api[_-]?key|api[_-]?secret|access[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
        "hardcoded-credential",
    ),
]

DANGEROUS_PATTERNS = [
    (
        re.compile(r"\beval\s*\("),
        "eval-call",
        "Dynamic code execution -- review for injection risk",
    ),
    (
        re.compile(r"new\s+Function\s*\("),
        "function-ctor",
        "Function constructor evaluates strings as code",
    ),
    (
        re.compile(r"child_process\.exec\s*\([^)]*\+"),
        "exec-string-concat",
        "Concatenated shell exec -- command injection risk",
    ),
    (
        re.compile(r"dangerouslySetInnerHTML"),
        "dangerously-set-inner-html",
        "Bypasses React XSS protection -- verify sanitization",
    ),
    (
        re.compile(r"document\.write\s*\("),
        "document-write",
        "document.write is XSS-prone",
    ),
    (
        re.compile(r"v-html\s*="),
        "vue-v-html",
        "Vue v-html bypasses XSS protection",
    ),
]

A11Y_PATTERNS = [
    (
        re.compile(r"<img\b(?![^>]*\balt\s*=)[^>]*/?>"),
        "img-no-alt",
        "<img> without alt attribute",
    ),
    (
        re.compile(r"<button\b(?![^>]*aria-label)[^>]*>\s*</button>"),
        "empty-button-no-label",
        "Empty <button> without aria-label",
    ),
    (
        re.compile(
            r"<input\b(?![^>]*\b(?:aria-label|aria-labelledby)\s*=)[^>]*/?>"
        ),
        "input-no-aria-label",
        "<input> with no aria-label (verify <label htmlFor=...> elsewhere)",
    ),
    (
        re.compile(r"<div\b[^>]*\bonClick\s*="),
        "div-onclick-no-role",
        "<div onClick> without role=button or tabIndex",
    ),
    (
        re.compile(r"<a\b[^>]*href\s*=\s*[\"']#[\"']"),
        "anchor-href-hash",
        '<a href="#"> is not a real link',
    ),
    (
        re.compile(r"tabIndex\s*=\s*[\"']?[1-9]"),
        "tabindex-positive",
        "Positive tabIndex creates unpredictable focus order",
    ),
]


def run_security_scout(
    diff_path: Path,
    run_dir: Path,
    model_keeper: dict,
) -> dict:
    print(
        "[security] Scanning diff for security concerns...",
        file=sys.stderr,
    )

    agent_record = read_agent_identity(model_keeper, "security-scout")
    agent_record["total_invocations"] += 1

    diff_text = diff_path.read_text()
    additions: list[tuple[str, str]] = []
    current_file: str | None = None
    for line in diff_text.split("\n"):
        m_file = re.match(r"^\+\+\+ b/(.+)$", line)
        if m_file:
            current_file = m_file.group(1)
            continue
        if (
            line.startswith("+")
            and not line.startswith("+++")
            and current_file
        ):
            additions.append((current_file, line[1:]))

    secrets_found: list[dict] = []
    dangerous_found: list[dict] = []
    for file, line in additions:
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(line):
                secrets_found.append(
                    {"file": file, "label": label, "excerpt": line[:80]}
                )
                break
        for pattern, label, desc in DANGEROUS_PATTERNS:
            if pattern.search(line):
                dangerous_found.append(
                    {
                        "file": file,
                        "label": label,
                        "description": desc,
                        "excerpt": line[:80],
                    }
                )
                break

    dep_files_changed = [
        line
        for line in diff_text.split("\n")
        if re.match(
            r"^\+\+\+ b/.+(?:package\.json|yarn\.lock|package-lock\.json)$",
            line,
        )
    ]

    if secrets_found:
        det_verdict = "FAIL"
        confidence = 0.9
    elif dangerous_found:
        det_verdict = "WARN"
        confidence = 0.7
    elif dep_files_changed:
        det_verdict = "WARN"
        confidence = 0.6
    else:
        det_verdict = "PASS"
        confidence = 0.85

    warnings_list: list[str] = []
    for s in secrets_found[:5]:
        warnings_list.append(
            f"CRITICAL: secret pattern '{s['label']}' detected in {s['file']}"
        )
    for d in dangerous_found[:5]:
        warnings_list.append(
            f"{d['file']}: {d['label']} -- {d['description']}"
        )
    if dep_files_changed and not secrets_found and not dangerous_found:
        warnings_list.append(
            "Dependency file(s) changed -- recommend running `npm audit` or `yarn audit` manually"
        )

    report = {
        "agent": "security-scout",
        "run_id": run_dir.name,
        "verdict": det_verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"secrets={len(secrets_found)}, dangerous_patterns={len(dangerous_found)}, "
            f"dep_files_changed={len(dep_files_changed)}"
        ),
        "secrets_found": secrets_found,
        "dangerous_patterns_found": dangerous_found,
        "dependency_files_changed": dep_files_changed,
        "evidence": [str(diff_path)],
        "warnings": warnings_list,
        "generated_at": now_iso(),
    }

    sec_agent = _make_agent("security")
    diff_sample_lines = [
        line
        for line in diff_text.split("\n")
        if line.startswith("+") and not line.startswith("+++")
    ][:50]
    phase1 = sec_agent.investigate(
        deterministic_data={
            "deterministic_verdict": det_verdict,
            "secrets_found": secrets_found,
            "dangerous_patterns_found": dangerous_found,
            "dependency_files_changed_count": len(dep_files_changed),
            "diff_additions_sample": "\n".join(diff_sample_lines),
        },
        identity_record=agent_record,
    )
    report["verdict"] = phase1.verdict
    report["llm_phase1"] = _llm_result_to_dict(phase1)

    sec_path = run_dir / "security-scout.json"
    sec_path.write_text(json.dumps(report, indent=2))
    print(
        f"[security] phase1 verdict={phase1.verdict} -- {phase1.narrative[:120]}",
        file=sys.stderr,
    )

    return {
        "deterministic": report,
        "phase1": phase1,
        "agent_role": "Security Scout",
    }


def run_accessibility_auditor(
    diff_path: Path,
    run_dir: Path,
    model_keeper: dict,
) -> dict:
    print(
        "[a11y] Scanning diff for accessibility concerns...", file=sys.stderr
    )

    agent_record = read_agent_identity(model_keeper, "a11y-auditor")
    agent_record["total_invocations"] += 1

    diff_text = diff_path.read_text()
    issues: list[dict] = []
    ui_addition_sample: list[str] = []
    current_file: str | None = None
    for line in diff_text.split("\n"):
        m_file = re.match(r"^\+\+\+ b/(.+)$", line)
        if m_file:
            current_file = m_file.group(1)
            continue
        if (
            line.startswith("+")
            and not line.startswith("+++")
            and current_file
        ):
            if not re.search(
                r"\.(tsx|jsx|vue|html|svelte|astro)$", current_file
            ):
                continue
            content = line[1:]
            if len(ui_addition_sample) < 40:
                ui_addition_sample.append(f"{current_file}: {content[:120]}")
            for pattern, label, desc in A11Y_PATTERNS:
                if pattern.search(content):
                    issues.append(
                        {
                            "file": current_file,
                            "label": label,
                            "description": desc,
                            "excerpt": content.strip()[:80],
                        }
                    )
                    break

    if issues:
        det_verdict = "WARN"
        confidence = min(0.85, 0.5 + 0.05 * len(issues))
    else:
        det_verdict = "PASS"
        confidence = 0.7

    warnings_list = [
        f"{i['file']}: {i['label']} -- {i['description']}" for i in issues[:10]
    ]

    report = {
        "agent": "a11y-auditor",
        "run_id": run_dir.name,
        "verdict": det_verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"heuristic_issues={len(issues)} found in UI-file additions; "
            "PASS confidence capped at 0.7 because heuristics cannot prove absence of all a11y bugs"
        ),
        "issues_found": issues[:50],
        "total_issues_found": len(issues),
        "evidence": [str(diff_path)],
        "warnings": warnings_list,
        "generated_at": now_iso(),
    }

    a11y_agent = _make_agent("a11y")
    phase1 = a11y_agent.investigate(
        deterministic_data={
            "deterministic_verdict": det_verdict,
            "issues_found": issues[:30],
            "total_issues_found": len(issues),
            "ui_additions_sample": "\n".join(ui_addition_sample),
        },
        identity_record=agent_record,
    )
    report["verdict"] = phase1.verdict
    report["llm_phase1"] = _llm_result_to_dict(phase1)

    a11y_path = run_dir / "a11y-auditor.json"
    a11y_path.write_text(json.dumps(report, indent=2))
    print(
        f"[a11y] phase1 verdict={phase1.verdict} -- {phase1.narrative[:120]}",
        file=sys.stderr,
    )

    return {
        "deterministic": report,
        "phase1": phase1,
        "agent_role": "A11y Auditor",
    }


# ---------------------------------------------------------------------------
# Phase 2 — Team review
# ---------------------------------------------------------------------------


def run_team_review(
    role_key: str,
    phase1: dict,
    model_keeper: dict,
    run_dir: Path,
) -> dict:
    role_name, identity_key, model, prompt = AGENT_ROLES[role_key]
    print(
        f"[team-review] {role_name} reviewing team findings...",
        file=sys.stderr,
    )

    agent = LLMAgent(role_name=role_name, role_prompt=prompt, model=model)
    agent_record = read_agent_identity(model_keeper, identity_key)

    my_p1: LLMResult = phase1[role_key]["phase1"]
    team_findings: dict[str, LLMResult] = {
        AGENT_ROLES[k][0]: phase1[k]["phase1"] for k in phase1
    }

    review = agent.team_review(my_p1, team_findings, agent_record)

    review_path = run_dir / f"{role_key}-review.json"
    review_path.write_text(
        json.dumps(
            {
                "agent_role": role_name,
                "phase2_verdict": review.verdict,
                "phase2_narrative": review.narrative,
                "phase2_recommendations": review.recommendations,
                "phase2_confidence_basis": review.confidence_basis,
            },
            indent=2,
        )
    )

    # Update agent identity with FINAL (phase-2) finding
    agent_record["recent_findings"].append(
        {
            "run_id": run_dir.name,
            "verdict": review.verdict,
            "confidence": None,
            "key_observation": review.narrative[:200],
        }
    )
    write_agent_identity(model_keeper, identity_key, agent_record)

    print(
        f"[team-review] {role_name} -> {review.verdict}: {review.narrative[:120]}",
        file=sys.stderr,
    )
    return {"phase2": review, "role_name": role_name}


# ---------------------------------------------------------------------------
# Phase 3 — QA Lead synthesis
# ---------------------------------------------------------------------------


def synthesize_verdict_v3(
    phase1: dict,
    phase2: dict,
    run_dir: Path,
    model_keeper: dict,
) -> dict:
    print("[qa-lead] Synthesizing team verdict...", file=sys.stderr)

    final_verdicts = {
        role: phase2[role]["phase2"].verdict for role in phase2
    }
    rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
    final_v = max(final_verdicts.values(), key=lambda v: rank[v])
    full_agreement = len(set(final_verdicts.values())) == 1

    qa_agent = LLMAgent(
        role_name="QA Lead",
        role_prompt=(
            "You are the QA Lead synthesizing 6 specialist agents' two-phase findings. "
            "Phase 1 was independent investigation; Phase 2 was team review. "
            "Your job: produce a final synthesis that explains WHAT THE TEAM CONCLUDED. "
            "Reference specific agents by role. If they disagreed in phase 1 and converged "
            "in phase 2, explain HOW the team converged. If disagreement persists, identify "
            "the most signal-rich one."
        ),
        model=SONNET,
    )
    qa_record = read_agent_identity(model_keeper, "qa-lead")
    qa_record["total_invocations"] += 1

    team_summary_for_qa = {
        role: {
            "phase1": {
                "verdict": phase1[role]["phase1"].verdict,
                "narrative": phase1[role]["phase1"].narrative,
            },
            "phase2": {
                "verdict": phase2[role]["phase2"].verdict,
                "narrative": phase2[role]["phase2"].narrative,
            },
        }
        for role in phase1
    }

    qa_result = qa_agent.investigate(
        deterministic_data={
            "deterministic_final_verdict": final_v,
            "final_verdicts": final_verdicts,
            "full_agreement": full_agreement,
            "team_two_phase_summary": team_summary_for_qa,
        },
        identity_record=qa_record,
    )

    qa_record["recent_findings"].append(
        {
            "run_id": run_dir.name,
            "verdict": qa_result.verdict,
            "confidence": None,
            "key_observation": qa_result.narrative[:200],
        }
    )
    write_agent_identity(model_keeper, "qa-lead", qa_record)

    # QA Lead can stand by, escalate, or de-escalate — but final must be at least max(team)
    if rank.get(qa_result.verdict, 0) < rank[final_v]:
        # Don't allow QA to de-escalate below the team max — surface that
        final_verdict = final_v
    else:
        final_verdict = qa_result.verdict

    print(
        f"[qa-lead] {qa_result.verdict} (team_max={final_v}) -- "
        f"{qa_result.narrative[:180]}",
        file=sys.stderr,
    )

    result = {
        "run_id": run_dir.name,
        "final_verdict": final_verdict,
        "qa_lead_phase3_verdict": qa_result.verdict,
        "team_max_verdict": final_v,
        "qa_lead_synthesis": qa_result.narrative,
        "qa_lead_recommendations": qa_result.recommendations,
        "qa_lead_confidence_basis": qa_result.confidence_basis,
        "qa_lead_cost_usd": qa_result.cost_usd,
        "agreement": full_agreement,
        "phase1_verdicts": {
            role: phase1[role]["phase1"].verdict for role in phase1
        },
        "phase2_verdicts": final_verdicts,
        "generated_at": now_iso(),
    }
    (run_dir / "verdict.json").write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Verdict markdown rendering
# ---------------------------------------------------------------------------


def write_verdict_md_v3(
    verdict: dict,
    phase1: dict,
    phase2: dict,
    run_dir: Path,
    target: str,
) -> None:
    lines: list[str] = []
    lines.append(f"# EGV Team Verdict -- {target}")
    lines.append("")
    lines.append(f"Run ID: `{verdict['run_id']}`")
    lines.append(f"Generated: {verdict['generated_at']}")
    lines.append("")
    lines.append(f"## Final: **{verdict['final_verdict']}**")
    lines.append("")
    lines.append(f"Agreement across team: {'YES' if verdict['agreement'] else 'NO'}")
    lines.append("")
    lines.append("## QA Lead synthesis")
    lines.append("")
    lines.append(verdict["qa_lead_synthesis"])
    if verdict.get("qa_lead_recommendations"):
        lines.append("")
        lines.append("### Recommendations")
        for r in verdict["qa_lead_recommendations"]:
            lines.append(f"- {r}")
    lines.append("")
    lines.append("## Team conversation")
    lines.append("")
    for role_key in ("auditor", "sentinel", "contract", "perf", "security", "a11y"):
        if role_key not in phase1:
            continue
        role_name = AGENT_ROLES[role_key][0]
        p1: LLMResult = phase1[role_key]["phase1"]
        p2: LLMResult = phase2[role_key]["phase2"]
        lines.append(f"### {role_name}")
        lines.append("")
        lines.append(f"**Phase 1 (independent):** `{p1.verdict}`")
        lines.append("")
        lines.append(f"> {p1.narrative}")
        lines.append("")
        if p1.recommendations:
            for r in p1.recommendations:
                lines.append(f"- {r}")
            lines.append("")
        lines.append(f"**Phase 2 (after team review):** `{p2.verdict}`")
        lines.append("")
        lines.append(f"> {p2.narrative}")
        lines.append("")
        if p2.recommendations:
            for r in p2.recommendations:
                lines.append(f"- {r}")
            lines.append("")
    md_path = run_dir / "verdict.md"
    md_path.write_text("\n".join(lines))
    print(f"[qa-lead] verdict.md written to {md_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="Commit SHA or path to a diff file")
    parser.add_argument("--project", default="excalidraw")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--selected-flows", default=None)
    parser.add_argument("--enable-layer2", action="store_true")
    args = parser.parse_args()

    # Verify `claude` CLI is available BEFORE doing any work
    try:
        require_claude_cli()
    except LLMUnavailableError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.project_root:
        project_root_arg = Path(args.project_root).resolve()
        keeper_path = keeper_path_for_project_root(project_root_arg)
        if not keeper_path.exists():
            print(
                f"FATAL: keeper not found at {keeper_path}", file=sys.stderr
            )
            print(
                f"Run `python3 lib/onboard-project.py {project_root_arg}` first, "
                "or create the keeper manually.",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        legacy_path = (
            SKILL_ROOT / "model-keeper" / "projects" / f"{args.project}.json"
        )
        if legacy_path.exists():
            print(
                f"[WARN] Using legacy keeper path: {legacy_path}. "
                "Pass --project-root instead.",
                file=sys.stderr,
            )
            keeper_path = legacy_path
        else:
            inferred_root = SKILL_ROOT.parent / args.project
            keeper_path = keeper_path_for_project_root(inferred_root)
            if not keeper_path.exists():
                print(
                    f"FATAL: no keeper found. Tried legacy ({legacy_path}) "
                    f"and v1 inferred ({keeper_path}). Pass --project-root explicitly.",
                    file=sys.stderr,
                )
                sys.exit(2)

    model_keeper = load_model_keeper(keeper_path)

    pending_proposals = [
        p
        for p in model_keeper.get("lifecycle_artifacts", {}).get(
            "synthesis_proposals", []
        )
        if not p.get("human_reviewed", False)
    ]
    if pending_proposals:
        print(
            f"[orchestrator] WARNING: {len(pending_proposals)} unreviewed Layer 2 proposal(s) in keeper.",
            file=sys.stderr,
        )

    project_root = Path(model_keeper["project_root"])
    coverage_path = (
        project_root / model_keeper["framework_config"]["coverage_output_path"]
    )
    if not coverage_path.exists():
        print(
            f"FATAL: coverage file not found at {coverage_path}",
            file=sys.stderr,
        )
        print(
            f"Run `yarn test:coverage --run` in {project_root} first.",
            file=sys.stderr,
        )
        sys.exit(2)

    run_id = make_run_id(args.target)
    run_dir = SKILL_ROOT / "reports" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[orchestrator] run_id={run_id}", file=sys.stderr)

    diff_path = get_diff(args.target, project_root)
    selected = args.selected_flows.split(",") if args.selected_flows else None

    # ------------- Phase 1 -------------
    print(
        "[team] === PHASE 1: Independent investigation ===", file=sys.stderr
    )
    phase1: dict[str, dict] = {}
    phase1["auditor"] = run_regression_auditor(
        diff_path, coverage_path, project_root, run_dir, model_keeper
    )
    if args.enable_layer2:
        from layer2 import synthesize_proposals

        layer2_result = synthesize_proposals(
            project_root, run_dir / "blast-radius.json"
        )
        print(
            f"[layer2] Generated {layer2_result['proposals_generated']} stub proposal(s)",
            file=sys.stderr,
        )
        model_keeper = load_model_keeper(keeper_path)

    phase1["sentinel"] = run_critical_path_sentinel(
        model_keeper, project_root, run_dir, selected_flows=selected
    )
    phase1["contract"] = run_contract_sentinel(
        diff_path, project_root, run_dir, model_keeper
    )
    phase1["perf"] = run_performance_watch(
        phase1["sentinel"]["deterministic"], run_dir, model_keeper
    )
    phase1["security"] = run_security_scout(diff_path, run_dir, model_keeper)
    phase1["a11y"] = run_accessibility_auditor(
        diff_path, run_dir, model_keeper
    )

    # ------------- Phase 2 -------------
    print("[team] === PHASE 2: Team review ===", file=sys.stderr)
    phase2: dict[str, dict] = {}
    for role_key in (
        "auditor",
        "sentinel",
        "contract",
        "perf",
        "security",
        "a11y",
    ):
        phase2[role_key] = run_team_review(
            role_key, phase1, model_keeper, run_dir
        )

    # ------------- Phase 3 -------------
    print("[team] === PHASE 3: QA Lead synthesis ===", file=sys.stderr)
    verdict = synthesize_verdict_v3(phase1, phase2, run_dir, model_keeper)

    # Sum cost across all LLM calls (6 phase-1 + 6 phase-2 + 1 QA Lead)
    total_cost = 0.0
    for role_key, p1_data in phase1.items():
        p1 = p1_data.get("phase1")
        if p1 is not None and hasattr(p1, "cost_usd"):
            total_cost += p1.cost_usd
    for role_key, p2_data in phase2.items():
        p2 = p2_data.get("phase2")
        if p2 is not None and hasattr(p2, "cost_usd"):
            total_cost += p2.cost_usd
    total_cost += float(verdict.get("qa_lead_cost_usd", 0.0))
    verdict["total_cost_usd"] = round(total_cost, 4)
    # Persist the updated verdict.json with cost field
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))

    write_verdict_md_v3(verdict, phase1, phase2, run_dir, args.target)

    save_model_keeper(keeper_path, model_keeper)
    print(
        f"[orchestrator] Model Keeper updated at {keeper_path}",
        file=sys.stderr,
    )

    print("\n=== EGV Team Verdict ===")
    print(f"Target: {args.target}")
    print(f"Final: {verdict['final_verdict']}")
    print(f"Agreement: {'YES' if verdict['agreement'] else 'NO'}")
    print("\nPhase 1 (independent):")
    for role, v in verdict["phase1_verdicts"].items():
        print(f"  {role}: {v}")
    print("\nPhase 2 (after team review):")
    for role, v in verdict["phase2_verdicts"].items():
        print(f"  {role}: {v}")
    print(f"\nQA Lead synthesis: {verdict['qa_lead_synthesis'][:300]}")
    print(f"\nTotal cost this run: ${total_cost:.4f}")
    print(f"\nReport: {run_dir / 'verdict.md'}")


if __name__ == "__main__":
    main()
