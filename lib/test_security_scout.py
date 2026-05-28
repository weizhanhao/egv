"""Unit tests for Security Scout regex patterns and detection logic.

Test strings are built at runtime via string concatenation so the static source
never contains a literal that matches a real secret pattern (which would trip
GitHub's secret scanner during push). The constructed runtime strings still
match the SECRET_PATTERNS regexes — that's the whole point of these tests.
"""

import importlib.util
from pathlib import Path


def _load_run_egv():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("run_egv", str(here / "run-egv.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secret_regex_matches_openai_key():
    m = _load_run_egv()
    # Built at runtime — never a literal secret in this source.
    fake_key = "sk-" + "proj-" + ("z" * 24)
    text = f'const k = "{fake_key}";'
    matched = False
    for pattern, label in m.SECRET_PATTERNS:
        if pattern.search(text):
            matched = True
            assert label == "openai-key"
            break
    assert matched


def test_secret_regex_matches_stripe_key():
    m = _load_run_egv()
    # Built at runtime — never a literal secret in this source.
    fake_key = "sk_" + "live_" + ("z" * 24)
    text = f'const s = "{fake_key}";'
    matched = False
    for pattern, label in m.SECRET_PATTERNS:
        if pattern.search(text):
            matched = True
            assert label == "stripe-key"
            break
    assert matched


def test_dangerous_pattern_eval():
    m = _load_run_egv()
    text = "const x = eval(userInput);"
    matched = False
    for pattern, label, desc in m.DANGEROUS_PATTERNS:
        if pattern.search(text):
            matched = True
            assert label == "eval-call"
            break
    assert matched
