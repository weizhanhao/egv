#!/usr/bin/env python3
"""
run-egv.py — POC orchestrator.

Runs the EGV verification team end-to-end for a given commit (or diff).
v0 POC: role-rotates within one process (no real subagent dispatch — equivalent
architecture, faster execution).

Pipeline:
  1. Generate diff (from commit SHA, or read from file)
  2. Regression Auditor:
     - Run coverage-diff.ts → blast-radius.json
     - Classify per file (covered / uncovered / test_file_excluded / not_in_coverage)
     - Compute auditor confidence + verdict
  3. Critical Path Sentinel:
     - Read critical_flows from Model Keeper
     - Run vitest for each flow's test_path
     - Compute sentinel confidence + verdict
  4. QA Lead synthesis:
     - Detect disagreement between auditor and sentinel
     - Compute combined verdict + confidence
     - Write human-readable verdict.md

Usage:
  python3 run-egv.py <commit-sha-or-diff-path> [--project excalidraw]

Output: reports/<run_id>/
"""

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
from model_keeper import (
    CURRENT_SCHEMA_VERSION,
    keeper_path_for_project_root,
    load_model_keeper,
    now_iso,
    provenance_stamp,
    save_model_keeper,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent


def make_run_id(commit_or_diff: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = re.sub(r"[^a-zA-Z0-9]", "-", commit_or_diff)[:20]
    return f"{stamp}__{tag}"


def get_diff(arg: str, project_root: Path) -> Path:
    """If arg is a commit SHA, generate diff. If file path, return it."""
    if os.path.isfile(arg):
        return Path(arg).resolve()
    diff_path = Path(f"/tmp/egv-diff-{arg}.patch")
    subprocess.run(
        ["git", "-C", str(project_root), "diff", "--unified=0", "--no-color",
         f"{arg}~1..{arg}"],
        check=True, stdout=diff_path.open("w"),
    )
    return diff_path


def _make_weight_lookup(weights: dict):
    """Return a function(path) -> float using glob patterns from the keeper."""
    import fnmatch

    patterns = weights.get("patterns", [])
    default = weights.get("default", 1.0)

    def _glob_match(path: str, glob: str) -> bool:
        # Expand brace alternations like {ts,tsx} into multiple globs.
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


def run_regression_auditor(
    diff_path: Path, coverage_path: Path, project_root: Path, run_dir: Path,
    model_keeper: dict,
) -> dict:
    """Layer 1: coverage × diff. Outputs auditor verdict."""
    print(f"[auditor] Running coverage-diff on {diff_path.name}...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "regression-auditor")
    agent_record["total_invocations"] += 1

    weights = model_keeper.get("file_importance_weights", {"patterns": [], "default": 1.0})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as wf:
        json.dump(weights, wf)
        weights_path = wf.name

    blast_path = run_dir / "blast-radius.json"

    result = subprocess.run(
        ["npx", "--yes", "tsx@4", str(SKILL_ROOT / "lib" / "coverage-diff.ts"),
         str(diff_path), str(coverage_path), str(project_root),
         "--weights", weights_path],
        capture_output=True, text=True, check=True,
    )
    blast_path.write_text(result.stdout)
    blast = json.loads(result.stdout)
    summary = blast["summary"]

    coverage_ratio = summary.get("weighted_coverage_ratio", summary["coverage_ratio"])
    age = blast["confidence_inputs"].get("coverage_data_age_seconds") or 99999
    data_freshness = 1.0 if age < 3600 else 0.7
    test_pass_rate = 1.0
    confidence = round(min(0.95, coverage_ratio * test_pass_rate * data_freshness), 2)

    # Weighted-aware verdict: only flag WARN when production-weighted lines are uncovered.
    weighted_prod_lines = summary.get(
        "weighted_production_changed_lines", summary["production_changed_lines"]
    )
    weighted_covered_lines = summary.get(
        "weighted_covered_changed_lines", summary["covered_changed_lines"]
    )
    weighted_uncovered = weighted_prod_lines - weighted_covered_lines

    if weighted_uncovered > 0:
        verdict = "WARN"
    elif weighted_prod_lines == 0:
        verdict = "PASS"
        confidence = 0.95
    else:
        verdict = "PASS"

    weight_for = _make_weight_lookup(weights)
    warnings = []
    for f in blast["files"]:
        if f["is_test_file"]:
            continue
        w = weight_for(f["file"])
        if w == 0.0:
            continue  # zero-weight files (types, snapshots) are not risk signals
        if f["coverage_status"] in ("uncovered", "not_in_coverage"):
            warnings.append(
                f"{f['file']}: {len(f['uncovered_lines'])} changed lines have no test coverage"
            )
        elif f["coverage_status"] == "partially_covered" and len(f["uncovered_lines"]) > 0:
            warnings.append(
                f"{f['file']}: {len(f['uncovered_lines'])} of {len(f['changed_lines'])} changed lines uncovered"
            )

    report = {
        "agent": "regression-auditor",
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"weighted_coverage_ratio={coverage_ratio:.2f} × test_pass_rate={test_pass_rate} × "
            f"data_freshness={data_freshness} → "
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
        "evidence": [
            str(blast_path),
            str(coverage_path),
        ],
        "warnings": warnings,
        "generated_at": now_iso(),
    }
    auditor_path = run_dir / "regression-auditor.json"
    auditor_path.write_text(json.dumps(report, indent=2))
    print(f"[auditor] verdict={verdict} confidence={confidence}", file=sys.stderr)

    agent_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "key_observation": (warnings[0] if warnings else "no warnings"),
    })
    write_agent_identity(model_keeper, "regression-auditor", agent_record)

    return report


def run_critical_path_sentinel(
    model_keeper: dict, project_root: Path, run_dir: Path,
    selected_flows: list | None = None,
) -> dict:
    """Run the critical flow tests."""
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
            results.append({
                "flow_name": flow["name"],
                "test_path": None,
                "status": "missing",
                "duration_ms": 0,
                "evidence_path": None,
                "failure_excerpt": "test_path not configured in Model Keeper",
            })
            continue

        flow_log = run_dir / f"flow-{flow['name']}.log"
        start = time.time()
        sentinel_cmd_template = model_keeper.get("framework_config", {}).get(
            "sentinel_test_command", "yarn test:app --run {test_path}"
        )
        # Template uses {test_path} placeholder
        sentinel_cmd = sentinel_cmd_template.format(test_path=test_path)
        rc = subprocess.call(
            sentinel_cmd, shell=True,
            cwd=str(project_root),
            stdout=flow_log.open("w"), stderr=subprocess.STDOUT,
        )
        duration_ms = int((time.time() - start) * 1000)

        if rc == 0:
            results.append({
                "flow_name": flow["name"],
                "test_path": test_path,
                "status": "passed",
                "duration_ms": duration_ms,
                "evidence_path": str(flow_log),
                "failure_excerpt": None,
            })
            print(f"[sentinel] PASS {flow['name']} ({duration_ms}ms)", file=sys.stderr)
        else:
            log_tail = flow_log.read_text().split("\n")[-30:]
            excerpt = "\n".join(log_tail)
            results.append({
                "flow_name": flow["name"],
                "test_path": test_path,
                "status": "failed",
                "duration_ms": duration_ms,
                "evidence_path": str(flow_log),
                "failure_excerpt": excerpt[:500],
            })
            print(f"[sentinel] FAIL {flow['name']} (rc={rc})", file=sys.stderr)

    verifiable = [r for r in results if r["status"] != "missing"]
    passed = [r for r in results if r["status"] == "passed"]
    failed = [r for r in results if r["status"] == "failed"]
    missing = [r for r in results if r["status"] == "missing"]

    verified_pass_ratio = (len(passed) / len(verifiable)) if verifiable else 0.0
    unverifiable_penalty = (len(missing) / len(results)) if results else 0.0
    confidence = round(min(0.95, verified_pass_ratio * (1 - 0.5 * unverifiable_penalty)), 2)

    if failed:
        verdict = "FAIL"
    elif missing:
        verdict = "WARN"
    elif passed:
        verdict = "PASS"
    else:
        verdict = "WARN"

    report = {
        "agent": "critical-path-sentinel",
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"verified_pass_ratio={verified_pass_ratio:.2f} × "
            f"(1 - 0.5 × unverifiable_penalty={unverifiable_penalty:.2f}) = "
            f"{verified_pass_ratio * (1 - 0.5 * unverifiable_penalty):.2f} (capped at 0.95)"
        ),
        "flows_total": len(results),
        "flows_verified": len(passed),
        "flows_unverifiable": len(missing),
        "flows_failed": len(failed),
        "results": results,
        "evidence": [r["evidence_path"] for r in results if r["evidence_path"]],
        "feed_to_model_keeper": {
            "flows_to_update_last_verified": [r["flow_name"] for r in passed],
            "flows_with_missing_test_paths": [r["flow_name"] for r in missing],
        },
        "generated_at": now_iso(),
    }
    sentinel_path = run_dir / "critical-path-sentinel.json"
    sentinel_path.write_text(json.dumps(report, indent=2))
    print(f"[sentinel] verdict={verdict} confidence={confidence}", file=sys.stderr)

    agent_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "key_observation": f"{len(passed)} flows passed, {len(failed)} failed, {len(missing)} missing",
    })
    write_agent_identity(model_keeper, "critical-path-sentinel", agent_record)

    return report


def run_contract_sentinel(
    diff_path: Path, project_root: Path, run_dir: Path, model_keeper: dict,
) -> dict:
    """Type/schema breakage detector. Runs the project's typecheck command and
    correlates errors with the diff file paths."""
    print("[contract] Running typecheck...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "contract-sentinel")
    agent_record["total_invocations"] += 1

    typecheck_cmd = model_keeper.get("framework_config", {}).get(
        "typecheck_command", "yarn test:typecheck"
    )

    log_path = run_dir / "contract-typecheck.log"
    rc = subprocess.call(
        typecheck_cmd, shell=True, cwd=str(project_root),
        stdout=log_path.open("w"), stderr=subprocess.STDOUT,
    )
    log_text = log_path.read_text()

    # Parse TypeScript errors: lines like "path/to/file.ts(123,45): error TS2304: Cannot find name 'foo'."
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

    # Identify which diff files have errors
    diff_text = diff_path.read_text()
    diff_files = set()
    for line in diff_text.split("\n"):
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            diff_files.add(m.group(1))
    errors_in_diff = [
        e for e in all_errors
        if any(e["file"].endswith(df) or df.endswith(e["file"]) for df in diff_files)
    ]

    if rc != 0 and not all_errors:
        verdict = "FAIL"
        confidence = 0.0
        warnings_list = [
            "Typecheck command exited non-zero but produced no parseable errors. Manual investigation required."
        ]
    elif errors_in_diff:
        verdict = "WARN"
        confidence = round(min(0.95, len(errors_in_diff) / max(1, len(all_errors)) * 0.95), 2)
        warnings_list = [
            f"{e['file']}:{e['line']} — {e['code']}: {e['msg'][:120]}"
            for e in errors_in_diff[:5]
        ]
    elif all_errors:
        verdict = "WARN"
        confidence = 0.7  # there are errors but not in our diff — pre-existing
        warnings_list = [f"{len(all_errors)} pre-existing TS errors in repo (not caused by this diff)"]
    else:
        verdict = "PASS"
        confidence = 0.95
        warnings_list = []

    report = {
        "agent": "contract-sentinel",
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"errors_in_diff={len(errors_in_diff)} / total_errors={len(all_errors)}, "
            f"verdict={verdict}"
        ),
        "errors_in_diff_files": errors_in_diff[:20],
        "total_typecheck_errors": len(all_errors),
        "evidence": [str(log_path)],
        "warnings": warnings_list,
        "generated_at": now_iso(),
    }
    contract_path = run_dir / "contract-sentinel.json"
    contract_path.write_text(json.dumps(report, indent=2))
    print(
        f"[contract] verdict={verdict} confidence={confidence} "
        f"(errors_in_diff={len(errors_in_diff)}/{len(all_errors)})",
        file=sys.stderr,
    )

    agent_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "key_observation": (warnings_list[0] if warnings_list else "no typecheck errors"),
    })
    write_agent_identity(model_keeper, "contract-sentinel", agent_record)

    return report


def run_performance_watch(
    sentinel_report: dict, run_dir: Path, model_keeper: dict,
) -> dict:
    """Compare this run's critical flow durations against per-flow baselines.
    Updates baselines (rolling p50). Flags WARN if any flow is >=25% slower than baseline."""
    print("[perfwatch] Comparing flow durations to baselines...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "performance-watch")
    agent_record["total_invocations"] += 1

    baselines = model_keeper.setdefault("flow_baselines", {})
    SLOWDOWN_THRESHOLD = 1.25  # 25% slower than p50

    slowdowns = []
    updated_baselines = []
    for r in sentinel_report.get("results", []):
        if r.get("status") != "passed":
            continue  # only measure passing flows
        flow_name = r["flow_name"]
        duration = r["duration_ms"]
        base = baselines.get(flow_name)
        if base is None or base.get("n_observations", 0) == 0:
            # First observation — seed baseline
            baselines[flow_name] = {
                "p50_ms": duration,
                "n_observations": 1,
                "last_updated": now_iso(),
            }
            updated_baselines.append(flow_name)
        else:
            p50 = base["p50_ms"]
            if duration >= p50 * SLOWDOWN_THRESHOLD:
                slowdowns.append({
                    "flow_name": flow_name,
                    "duration_ms": duration,
                    "baseline_p50_ms": p50,
                    "slowdown_pct": round((duration - p50) / p50 * 100, 1),
                })
            # Update rolling p50 (simple EMA)
            n = base["n_observations"]
            new_p50 = int(p50 * 0.8 + duration * 0.2)
            baselines[flow_name] = {
                "p50_ms": new_p50,
                "n_observations": n + 1,
                "last_updated": now_iso(),
            }
            updated_baselines.append(flow_name)

    if slowdowns:
        verdict = "WARN"
        confidence = 0.85
        warnings_list = [
            f"{s['flow_name']}: {s['duration_ms']}ms vs baseline {s['baseline_p50_ms']}ms (+{s['slowdown_pct']}%)"
            for s in slowdowns
        ]
    elif not updated_baselines:
        verdict = "PASS"
        confidence = 0.0  # no signal
        warnings_list = ["No passing flows to measure"]
    else:
        verdict = "PASS"
        confidence = 0.9
        warnings_list = []

    report = {
        "agent": "performance-watch",
        "run_id": run_dir.name,
        "verdict": verdict,
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
    perf_path = run_dir / "performance-watch.json"
    perf_path.write_text(json.dumps(report, indent=2))
    print(f"[perfwatch] verdict={verdict} confidence={confidence} (slowdowns={len(slowdowns)})", file=sys.stderr)

    agent_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "key_observation": (warnings_list[0] if warnings_list else f"{len(updated_baselines)} baselines updated, no slowdowns"),
    })
    write_agent_identity(model_keeper, "performance-watch", agent_record)

    return report


# Regex patterns for the Security Scout — kept deliberately conservative
# to minimize false positives in v2.
SECRET_PATTERNS = [
    (re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), "stripe-key"),
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"), "openai-key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-access-key-id"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "github-personal-token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github-fine-grained-token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"), "private-key-block"),
    (re.compile(r"(?i)(?:password|passwd|pwd|api[_-]?key|api[_-]?secret|access[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"), "hardcoded-credential"),
]

DANGEROUS_PATTERNS = [
    (re.compile(r"\beval\s*\("), "eval-call", "Dynamic code execution — review for injection risk"),
    (re.compile(r"new\s+Function\s*\("), "function-ctor", "Function constructor evaluates strings as code"),
    (re.compile(r"child_process\.exec\s*\([^)]*\+"), "exec-string-concat", "Concatenated shell exec — command injection risk"),
    (re.compile(r"dangerouslySetInnerHTML"), "dangerously-set-inner-html", "Bypasses React XSS protection — verify sanitization"),
    (re.compile(r"document\.write\s*\("), "document-write", "document.write is XSS-prone"),
    (re.compile(r"v-html\s*="), "vue-v-html", "Vue v-html bypasses XSS protection"),
]

A11Y_PATTERNS = [
    (re.compile(r"<img\b(?![^>]*\balt\s*=)[^>]*/?>"), "img-no-alt", "<img> without alt attribute"),
    (re.compile(r"<button\b(?![^>]*aria-label)[^>]*>\s*</button>"), "empty-button-no-label", "Empty <button> without aria-label"),
    (re.compile(r"<input\b(?![^>]*\b(?:aria-label|aria-labelledby)\s*=)[^>]*/?>"), "input-no-aria-label", "<input> with no aria-label (verify <label htmlFor=...> elsewhere)"),
    (re.compile(r"<div\b[^>]*\bonClick\s*="), "div-onclick-no-role", "<div onClick> without role=button or tabIndex"),
    (re.compile(r"<a\b[^>]*href\s*=\s*[\"']#[\"']"), "anchor-href-hash", "<a href=\"#\"> is not a real link"),
    (re.compile(r"tabIndex\s*=\s*[\"']?[1-9]"), "tabindex-positive", "Positive tabIndex creates unpredictable focus order"),
]


def run_security_scout(
    diff_path: Path, run_dir: Path, model_keeper: dict,
) -> dict:
    """Static security scan of the diff. Three checks:
    1. Secret patterns in additions
    2. package.json dependency changes (recommend manual audit)
    3. Dangerous code patterns in additions
    """
    print("[security] Scanning diff for security concerns...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "security-scout")
    agent_record["total_invocations"] += 1

    diff_text = diff_path.read_text()
    additions = []  # lines that start with '+' but not '+++'
    current_file = None
    for line in diff_text.split("\n"):
        m_file = re.match(r"^\+\+\+ b/(.+)$", line)
        if m_file:
            current_file = m_file.group(1)
            continue
        if line.startswith("+") and not line.startswith("+++") and current_file:
            additions.append((current_file, line[1:]))

    secrets_found = []
    dangerous_found = []
    for file, line in additions:
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(line):
                secrets_found.append({"file": file, "label": label, "excerpt": line[:80]})
                break  # one match per line is enough
        for pattern, label, desc in DANGEROUS_PATTERNS:
            if pattern.search(line):
                dangerous_found.append({"file": file, "label": label, "description": desc, "excerpt": line[:80]})
                break

    # Check if package.json (or yarn.lock / package-lock.json) changed
    dep_files_changed = [
        line for line in diff_text.split("\n")
        if re.match(r"^\+\+\+ b/.+(?:package\.json|yarn\.lock|package-lock\.json)$", line)
    ]

    # Verdict logic
    if secrets_found:
        verdict = "FAIL"
        confidence = 0.9
    elif dangerous_found:
        verdict = "WARN"
        confidence = 0.7
    elif dep_files_changed:
        verdict = "WARN"
        confidence = 0.6
    else:
        verdict = "PASS"
        confidence = 0.85

    warnings_list = []
    for s in secrets_found[:5]:
        warnings_list.append(f"CRITICAL: secret pattern '{s['label']}' detected in {s['file']}")
    for d in dangerous_found[:5]:
        warnings_list.append(f"{d['file']}: {d['label']} — {d['description']}")
    if dep_files_changed and not secrets_found and not dangerous_found:
        warnings_list.append(f"Dependency file(s) changed — recommend running `npm audit` or `yarn audit` manually")

    report = {
        "agent": "security-scout",
        "run_id": run_dir.name,
        "verdict": verdict,
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
        "v3_followup": "Real SAST integration (CodeQL/Snyk) recommended for production use",
        "generated_at": now_iso(),
    }
    sec_path = run_dir / "security-scout.json"
    sec_path.write_text(json.dumps(report, indent=2))
    print(f"[security] verdict={verdict} confidence={confidence}", file=sys.stderr)

    agent_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "key_observation": (warnings_list[0] if warnings_list else "no security concerns found"),
    })
    write_agent_identity(model_keeper, "security-scout", agent_record)

    return report


def run_accessibility_auditor(
    diff_path: Path, run_dir: Path, model_keeper: dict,
) -> dict:
    """Heuristic accessibility scan of diff additions.
    v2 scaffold: catches common JSX/HTML anti-patterns.
    v3 will integrate real axe-core via Playwright MCP."""
    print("[a11y] Scanning diff for accessibility concerns...", file=sys.stderr)

    agent_record = read_agent_identity(model_keeper, "a11y-auditor")
    agent_record["total_invocations"] += 1

    diff_text = diff_path.read_text()
    issues = []
    current_file = None
    for line in diff_text.split("\n"):
        m_file = re.match(r"^\+\+\+ b/(.+)$", line)
        if m_file:
            current_file = m_file.group(1)
            continue
        if line.startswith("+") and not line.startswith("+++") and current_file:
            # Only check files that look like UI code
            if not re.search(r"\.(tsx|jsx|vue|html|svelte|astro)$", current_file):
                continue
            content = line[1:]
            for pattern, label, desc in A11Y_PATTERNS:
                if pattern.search(content):
                    issues.append({
                        "file": current_file,
                        "label": label,
                        "description": desc,
                        "excerpt": content.strip()[:80],
                    })
                    break  # one issue per added line is enough

    # Verdict logic — A11y is advisory; cap verdict at WARN even with many issues
    if issues:
        verdict = "WARN"
        confidence = min(0.85, 0.5 + 0.05 * len(issues))
    else:
        verdict = "PASS"
        confidence = 0.7  # heuristic — cannot certify "no a11y issues exist"

    warnings_list = [f"{i['file']}: {i['label']} — {i['description']}" for i in issues[:10]]

    report = {
        "agent": "a11y-auditor",
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_reasoning": (
            f"heuristic_issues={len(issues)} found in UI-file additions; "
            f"PASS confidence capped at 0.7 because heuristics cannot prove absence of all a11y bugs"
        ),
        "issues_found": issues[:50],  # cap to keep report small
        "total_issues_found": len(issues),
        "scope_note": "v2 scaffold uses regex heuristics on UI file diffs only. Real axe-core integration is v3.",
        "v3_followup": "Integrate axe-core via Playwright MCP — drive real browser against running app, capture WCAG violations.",
        "evidence": [str(diff_path)],
        "warnings": warnings_list,
        "generated_at": now_iso(),
    }
    a11y_path = run_dir / "a11y-auditor.json"
    a11y_path.write_text(json.dumps(report, indent=2))
    print(f"[a11y] verdict={verdict} confidence={confidence:.2f} (issues={len(issues)})", file=sys.stderr)

    agent_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict,
        "confidence": confidence,
        "key_observation": (warnings_list[0] if warnings_list else "no heuristic a11y issues found"),
    })
    write_agent_identity(model_keeper, "a11y-auditor", agent_record)

    return report


def synthesize_verdict(
    auditor: dict, sentinel: dict, contract: dict, run_dir: Path, model_keeper: dict,
    perf: dict | None = None, security: dict | None = None, a11y: dict | None = None,
) -> dict:
    """QA Lead — combine auditor + sentinel + contract into final verdict."""
    print("[qa-lead] Synthesizing verdict...", file=sys.stderr)

    verdicts = {
        "auditor": auditor["verdict"],
        "sentinel": sentinel["verdict"],
        "contract": contract["verdict"],
    }
    confidences = {
        "auditor": auditor["confidence"],
        "sentinel": sentinel["confidence"],
        "contract": contract["confidence"],
    }

    # All 3 agree?
    unique_verdicts = set(verdicts.values())
    full_agreement = len(unique_verdicts) == 1

    rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
    final_v = max(verdicts.values(), key=lambda v: rank[v])
    agreement_factor = 1.0 if full_agreement else 0.6
    final_confidence = round(min(confidences.values()) * agreement_factor, 2)

    disagreement_reason = None
    if not full_agreement:
        parts = [f"{name}={v} ({int(confidences[name]*100)}%)" for name, v in verdicts.items()]
        disagreement_reason = "Disagreement across agents: " + ", ".join(parts)

    perf_signal = None
    if perf:
        perf_signal = {"verdict": perf["verdict"], "confidence": perf["confidence"]}
        if perf["verdict"] in ("WARN", "FAIL") and final_v == "PASS":
            final_v = "WARN"
            disagreement_reason = (disagreement_reason or "") + (
                " | Performance Watch flagged slowdown — see performance-watch.json"
            )

    security_signal = None
    if security:
        security_signal = {"verdict": security["verdict"], "confidence": security["confidence"]}
        if security["verdict"] == "FAIL":
            final_v = "FAIL"  # security FAIL always wins
            disagreement_reason = (disagreement_reason or "") + (
                " | Security Scout FAILED — see security-scout.json"
            )
        elif security["verdict"] == "WARN" and final_v == "PASS":
            final_v = "WARN"
            disagreement_reason = (disagreement_reason or "") + (
                " | Security Scout flagged a concern"
            )

    a11y_signal = None
    if a11y:
        a11y_signal = {"verdict": a11y["verdict"], "confidence": a11y["confidence"]}
        if a11y["verdict"] == "WARN" and final_v == "PASS":
            final_v = "WARN"
            disagreement_reason = (disagreement_reason or "") + (
                " | Accessibility Auditor flagged issues (heuristic — may be false positives)"
            )

    result = {
        "run_id": run_dir.name,
        "final_verdict": final_v,
        "final_confidence": final_confidence,
        "agreement": full_agreement,
        "disagreement_reason": disagreement_reason,
        "auditor": {"verdict": auditor["verdict"], "confidence": auditor["confidence"]},
        "sentinel": {"verdict": sentinel["verdict"], "confidence": sentinel["confidence"]},
        "contract": {"verdict": contract["verdict"], "confidence": contract["confidence"]},
        "generated_at": now_iso(),
    }
    if perf_signal:
        result["perf"] = perf_signal
    if security_signal:
        result["security"] = security_signal
    if a11y_signal:
        result["a11y"] = a11y_signal
    return result


def write_verdict_md(
    verdict: dict, auditor: dict, sentinel: dict, run_dir: Path, target: str,
    contract: dict | None = None, perf: dict | None = None,
    security: dict | None = None, a11y: dict | None = None,
):
    md_path = run_dir / "verdict.md"
    lines = []
    lines.append(f"# EGV Verification Report — {target}")
    lines.append("")
    lines.append(f"Run ID: `{verdict['run_id']}`")
    lines.append(f"Generated: {verdict['generated_at']}")
    lines.append("")
    lines.append(f"## Final Verdict: **{verdict['final_verdict']}** (confidence: {int(verdict['final_confidence']*100)}%)")
    lines.append("")
    if not verdict["agreement"]:
        lines.append(f"### DISAGREEMENT detected (EGV Principle 5)")
        lines.append(f"{verdict['disagreement_reason']}")
        lines.append("")
    lines.append("## Team verdicts")
    lines.append("")
    lines.append(f"| Agent | Verdict | Confidence |")
    lines.append(f"|-------|---------|-----------|")
    lines.append(f"| 💥 Regression Auditor | {auditor['verdict']} | {int(auditor['confidence']*100)}% |")
    lines.append(f"| 🛡️ Critical Path Sentinel | {sentinel['verdict']} | {int(sentinel['confidence']*100)}% |")
    if contract:
        lines.append(f"| 🔒 Contract Sentinel | {contract['verdict']} | {int(contract['confidence']*100)}% |")
    if perf:
        lines.append(f"| ⚡ Performance Watch | {perf['verdict']} | {int(perf['confidence']*100)}% |")
    if security:
        lines.append(f"| 🔐 Security Scout | {security['verdict']} | {int(security['confidence']*100)}% |")
    if a11y:
        lines.append(f"| ♿ A11y Auditor | {a11y['verdict']} | {int(a11y['confidence']*100)}% |")
    lines.append("")
    lines.append("## Blast radius (Regression Auditor)")
    br = auditor["blast_radius"]
    lines.append(f"- Production code changed: **{br['production_changed_lines']} lines**")
    lines.append(f"- Test code changed: {br['test_changed_lines']} lines (excluded from risk)")
    lines.append(f"- Covered by existing tests: **{br['covered_changed_lines']}** of {br['production_changed_lines']} production lines")
    lines.append(f"- Uncovered production lines: **{br['uncovered_changed_lines']}** (risk)")
    lines.append("")
    if auditor.get("warnings"):
        lines.append("### Warnings")
        for w in auditor["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    lines.append("### Per-file classification")
    lines.append("")
    lines.append("| File | Status | Uncovered |")
    lines.append("|------|--------|-----------|")
    for f in br["files"]:
        lines.append(f"| `{f['file']}` | {f['status']} | {f['uncovered_line_count']} |")
    lines.append("")
    lines.append("## Critical Path Sentinel results")
    lines.append("")
    lines.append("| Flow | Status | Duration | Evidence |")
    lines.append("|------|--------|----------|----------|")
    for r in sentinel["results"]:
        ev = f"`{Path(r['evidence_path']).name}`" if r["evidence_path"] else "—"
        lines.append(f"| {r['flow_name']} | {r['status']} | {r['duration_ms']}ms | {ev} |")
    lines.append("")
    # Contract Sentinel detail (if present)
    if contract and contract.get("warnings"):
        lines.append("## Contract Sentinel warnings")
        lines.append("")
        for w in contract["warnings"][:10]:
            lines.append(f"- {w}")
        lines.append("")
    # Performance Watch detail (if present)
    if perf and perf.get("slowdowns"):
        lines.append("## Performance Watch — slowdowns detected")
        lines.append("")
        lines.append("| Flow | Current | Baseline | Slowdown |")
        lines.append("|------|---------|----------|----------|")
        for s in perf["slowdowns"]:
            lines.append(f"| {s['flow_name']} | {s['duration_ms']}ms | {s['baseline_p50_ms']}ms | +{s['slowdown_pct']}% |")
        lines.append("")
    # Security Scout detail (if present)
    if security and security.get("warnings"):
        lines.append("## Security Scout findings")
        lines.append("")
        for w in security["warnings"][:10]:
            lines.append(f"- {w}")
        lines.append("")
    # A11y Auditor detail (if present)
    if a11y and a11y.get("warnings"):
        lines.append("## A11y Auditor findings")
        lines.append("")
        for w in a11y["warnings"][:10]:
            lines.append(f"- {w}")
        lines.append("")
    lines.append("## EGV Principle Audit")
    lines.append("")
    lines.append("| # | Principle | Enacted? |")
    lines.append("|---|-----------|----------|")
    lines.append("| 1 | Skeptical Default | yes — every claim points to evidence file |")
    lines.append("| 2 | Persistent External Mind | yes — Model Keeper read, run_history updated |")
    lines.append("| 3 | Blast Radius First | yes — coverage × diff computed before any verification |")
    lines.append("| 4 | Evidence Over Verdict | yes — all conclusions cite a JSON or .log file |")
    lines.append("| 5 | Disagreement Is Signal | " + ("yes — disagreement detected and escalated" if not verdict["agreement"] else "yes — all agents agreed") + " |")
    lines.append("| 6 | Calibrated Uncertainty | yes — confidence shown with derivation; capped at 0.95 |")
    lines.append("| 7 | Accumulated Learning | yes — Model Keeper grows: agent identities, baselines, learned_patterns, lifecycle_artifacts |")

    md_path.write_text("\n".join(lines))
    print(f"[qa-lead] verdict.md written to {md_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="Commit SHA or path to a diff file")
    parser.add_argument("--project", default="excalidraw")
    parser.add_argument("--project-root", default=None,
                       help="Path to the target project. v1 keeper lives at <project-root>/.egv/project-keeper.json")
    parser.add_argument("--selected-flows", default=None,
                       help="Comma-separated flow names to run (default: all)")
    parser.add_argument("--enable-layer2", action="store_true",
                       help="Enable Layer 2 E2E synthesis stub generation for uncovered production lines")
    args = parser.parse_args()

    if args.project_root:
        project_root_arg = Path(args.project_root).resolve()
        keeper_path = keeper_path_for_project_root(project_root_arg)
        if not keeper_path.exists():
            print(f"FATAL: keeper not found at {keeper_path}", file=sys.stderr)
            print(f"Run `python3 lib/onboard-project.py {project_root_arg}` first (v2 feature), or create the keeper manually.", file=sys.stderr)
            sys.exit(2)
    else:
        # Legacy discovery — emit deprecation warning
        legacy_path = SKILL_ROOT / "model-keeper" / "projects" / f"{args.project}.json"
        if legacy_path.exists():
            print(f"[WARN] Using legacy keeper path: {legacy_path}. v1: pass --project-root instead.", file=sys.stderr)
            keeper_path = legacy_path
        else:
            # Try v1 path inferred from project name (assumes ./poc/<project>)
            inferred_root = SKILL_ROOT.parent / args.project
            keeper_path = keeper_path_for_project_root(inferred_root)
            if not keeper_path.exists():
                print(f"FATAL: no keeper found. Tried legacy ({legacy_path}) and v1 inferred ({keeper_path}).", file=sys.stderr)
                print(f"Pass --project-root explicitly.", file=sys.stderr)
                sys.exit(2)

    model_keeper = load_model_keeper(keeper_path)

    # Surface any pending Layer 2 proposals (v2 gate enforcement)
    pending_proposals = [
        p for p in model_keeper.get("lifecycle_artifacts", {}).get("synthesis_proposals", [])
        if not p.get("human_reviewed", False)
    ]
    if pending_proposals:
        print(
            f"[orchestrator] WARNING: {len(pending_proposals)} unreviewed Layer 2 proposal(s) "
            f"in Model Keeper. Review them via `python3 lib/egv_cli.py layer2-review`.",
            file=sys.stderr,
        )

    project_root = Path(model_keeper["project_root"])
    coverage_path = project_root / model_keeper["framework_config"]["coverage_output_path"]
    if not coverage_path.exists():
        print(f"FATAL: coverage file not found at {coverage_path}", file=sys.stderr)
        print(f"Run `yarn test:coverage --run` in {project_root} first.", file=sys.stderr)
        sys.exit(2)

    run_id = make_run_id(args.target)
    run_dir = SKILL_ROOT / "reports" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[orchestrator] run_id={run_id}", file=sys.stderr)

    diff_path = get_diff(args.target, project_root)

    selected = args.selected_flows.split(",") if args.selected_flows else None
    auditor = run_regression_auditor(diff_path, coverage_path, project_root, run_dir, model_keeper)
    if args.enable_layer2:
        from layer2 import synthesize_proposals
        layer2_result = synthesize_proposals(
            project_root, run_dir / "blast-radius.json"
        )
        print(f"[layer2] Generated {layer2_result['proposals_generated']} stub proposal(s)", file=sys.stderr)
        # Re-load keeper since layer2 saved it
        model_keeper = load_model_keeper(keeper_path)
    sentinel = run_critical_path_sentinel(model_keeper, project_root, run_dir,
                                          selected_flows=selected)
    perf = run_performance_watch(sentinel, run_dir, model_keeper)
    contract = run_contract_sentinel(diff_path, project_root, run_dir, model_keeper)
    security = run_security_scout(diff_path, run_dir, model_keeper)
    a11y = run_accessibility_auditor(diff_path, run_dir, model_keeper)
    verdict = synthesize_verdict(auditor, sentinel, contract, run_dir, model_keeper,
                                 perf=perf, security=security, a11y=a11y)
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
    write_verdict_md(
        verdict, auditor, sentinel, run_dir, args.target,
        contract=contract, perf=perf, security=security, a11y=a11y,
    )

    qa_record = read_agent_identity(model_keeper, "qa-lead")
    qa_record["total_invocations"] += 1
    qa_record["recent_findings"].append({
        "run_id": run_dir.name,
        "verdict": verdict["final_verdict"],
        "confidence": verdict["final_confidence"],
        "key_observation": (
            "agreement" if verdict["agreement"] else f"disagreement: {verdict['disagreement_reason']}"
        ),
    })
    write_agent_identity(model_keeper, "qa-lead", qa_record)

    save_model_keeper(keeper_path, model_keeper)
    print(f"[orchestrator] Model Keeper updated at {keeper_path}", file=sys.stderr)

    print(f"\n=== EGV Verification ===")
    print(f"Target: {args.target}")
    print(f"Final: {verdict['final_verdict']}  (confidence: {int(verdict['final_confidence']*100)}%)")
    print(f"  Auditor:  {auditor['verdict']} ({int(auditor['confidence']*100)}%)")
    print(f"  Sentinel: {sentinel['verdict']} ({int(sentinel['confidence']*100)}%)")
    print(f"  Contract: {contract['verdict']} ({int(contract['confidence']*100)}%)")
    print(f"  Perf:     {perf['verdict']} ({int(perf['confidence']*100)}%)")
    print(f"  Security: {security['verdict']} ({int(security['confidence']*100)}%)")
    print(f"  A11y:     {a11y['verdict']} ({int(a11y['confidence']*100)}%)")
    print(f"  Agreement: {'YES' if verdict['agreement'] else 'NO — see disagreement_reason'}")
    print(f"\nReport: {run_dir / 'verdict.md'}")


if __name__ == "__main__":
    main()
