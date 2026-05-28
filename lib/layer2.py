"""layer2.py — Layer 2: AI E2E synthesis for uncovered production lines.

v1 implementation: structured stub. For each uncovered file in the auditor's
blast-radius report, generates a Playwright test STUB (markdown describing what
to test, with a code template). All stubs are marked `needs_human_review: True`
so a human OR a v2 LLM-driven synthesizer can finish them.

v2 (this file): When `ANTHROPIC_API_KEY` env var is set AND the `anthropic` SDK
is installed, attempts real Claude-generated Playwright tests for up to 3 files
per run. On any failure (missing key, missing SDK, network, API error) it falls
back gracefully to the stub template — the orchestrator never crashes from
Layer 2 issues.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from agent_identity import read_agent_identity, write_agent_identity
from model_keeper import (
    keeper_path_for_project_root,
    load_model_keeper,
    now_iso,
    provenance_stamp,
    save_model_keeper,
)


PLAYWRIGHT_TEMPLATE = """\
// auto-generated stub by EGV Layer 2 (v1) — NEEDS HUMAN COMPLETION
// File under test: {file}
// Uncovered lines: {uncovered_lines}
// Suggested behavior to verify: <FILL IN — describe the user-visible behavior these lines support>

import {{ test, expect }} from '@playwright/test';

test.describe('{file_basename} — uncovered behavior coverage', () => {{
  test('exercises uncovered lines {uncovered_summary}', async ({{ page }}) => {{
    // TODO: navigate to the page that triggers this code path
    await page.goto('/');

    // TODO: perform the user action that exercises lines {uncovered_summary}
    // ...

    // TODO: assert the visible outcome
    // await expect(page.getByRole(...)).toBeVisible();
  }});
}});
"""


def try_llm_complete_stub(
    file_path: str,
    uncovered_lines: list[int],
    project_root: Path,
) -> Optional[str]:
    """Attempt to generate a real Playwright test via Claude API.
    Returns the generated code, or None if API unavailable / error.
    Graceful degradation — never raises.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    # Read the source file to give Claude context (capped at 4KB)
    src_path = Path(project_root) / file_path
    if not src_path.exists():
        return None
    try:
        source = src_path.read_text()[:4000]
    except (OSError, UnicodeDecodeError):
        return None

    uncovered_summary = (
        f"{uncovered_lines[0]}-{uncovered_lines[-1]}" if len(uncovered_lines) > 5
        else ",".join(str(x) for x in uncovered_lines)
    )

    prompt = f"""You are a Playwright test author. Given a TypeScript/JavaScript source file
and a list of UNCOVERED lines (no existing test exercises them), write a single Playwright
test that would exercise those lines.

Source file: {file_path}
Uncovered lines: {uncovered_summary}

Source content (first 4KB):
```
{source}
```

Output ONLY the Playwright test code — no preamble, no explanation, no markdown fences.
Start with `import {{ test, expect }} from '@playwright/test';` and write one test.
Use semantic selectors (getByRole, getByLabel, getByText). If you genuinely cannot infer
the user behavior from the source, output a // SYNTHESIS_FAILED comment as the first line.
"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if "SYNTHESIS_FAILED" in text:
            return None
        return text
    except Exception as e:
        # Print to stderr but never crash the orchestrator
        print(f"[layer2-llm] API call failed: {e}", file=sys.stderr)
        return None


def synthesize_proposals(project_root: Path, blast_radius_path: Path) -> dict[str, Any]:
    """Read blast-radius.json, generate stubs for uncovered production lines.
    Persists proposals to lifecycle_artifacts.synthesis_proposals in the Model Keeper.

    If `ANTHROPIC_API_KEY` is set and `anthropic` SDK is installed, attempts LLM
    completion for up to 3 files; otherwise falls back to template stubs.
    """
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    blast = json.loads(Path(blast_radius_path).read_text())

    proposals = []
    llm_completions = 0
    stub_fallbacks = 0
    MAX_LLM_PER_RUN = 3  # cost cap

    for f in blast.get("files", []):
        if f.get("is_test_file"):
            continue
        if not f.get("uncovered_lines"):
            continue
        if f["coverage_status"] not in ("uncovered", "partially_covered", "not_in_coverage"):
            continue
        file_path = f["file"]
        uncovered = f["uncovered_lines"]
        file_basename = Path(file_path).name

        # Try LLM completion first (bounded), fall back to stub
        llm_code = None
        if llm_completions < MAX_LLM_PER_RUN:
            llm_code = try_llm_complete_stub(file_path, uncovered, project_root)
            if llm_code:
                llm_completions += 1

        if llm_code:
            playwright_stub = llm_code
            synthesis_method = "llm-haiku-4.5"
        else:
            if len(uncovered) > 5:
                uncovered_summary = f"{uncovered[0]}-{uncovered[-1]}"
            else:
                uncovered_summary = ",".join(str(x) for x in uncovered)
            playwright_stub = PLAYWRIGHT_TEMPLATE.format(
                file=file_path,
                file_basename=file_basename,
                uncovered_lines=uncovered,
                uncovered_summary=uncovered_summary,
            )
            synthesis_method = "stub-template"
            stub_fallbacks += 1

        proposal = {
            "proposal_id": f"synth-{file_basename}-{now_iso()}",
            "uncovered_file": file_path,
            "uncovered_lines": uncovered,
            "uncovered_line_count": len(uncovered),
            "playwright_stub": playwright_stub,
            "synthesis_method": synthesis_method,
            "needs_human_review": True,
            "v2_action_when_implemented": (
                "Replace stub with LLM-generated test driven by Playwright MCP. "
                "Run 3x consecutively; if green and stable, propose adding to test suite."
            ),
            **provenance_stamp(agent="layer2-synth", run_id=blast.get("generated_at", "unknown")),
        }
        proposals.append(proposal)

    if proposals:
        la = keeper.setdefault("lifecycle_artifacts", {})
        la.setdefault("synthesis_proposals", []).extend(proposals)
        save_model_keeper(keeper_path, keeper)

    return {
        "proposals_generated": len(proposals),
        "llm_completions": llm_completions,
        "stub_fallbacks": stub_fallbacks,
        "proposals": proposals,
    }
