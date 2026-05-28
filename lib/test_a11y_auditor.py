import importlib.util
from pathlib import Path
import pytest


def _load_run_egv():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("run_egv", str(here / "run-egv.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_img_no_alt_detected():
    m = _load_run_egv()
    text = '<img src="logo.png" />'
    matched = False
    for pattern, label, _ in m.A11Y_PATTERNS:
        if pattern.search(text):
            matched = True
            assert label == "img-no-alt"
            break
    assert matched


def test_img_with_alt_not_flagged():
    m = _load_run_egv()
    text = '<img src="logo.png" alt="Company logo" />'
    for pattern, label, _ in m.A11Y_PATTERNS:
        if pattern.search(text) and label == "img-no-alt":
            pytest.fail(f"<img> with alt was incorrectly flagged as img-no-alt")


def test_anchor_href_hash_detected():
    m = _load_run_egv()
    text = '<a href="#" onClick={...}>Click</a>'
    matched = False
    for pattern, label, _ in m.A11Y_PATTERNS:
        if pattern.search(text):
            matched = label == "anchor-href-hash"
            if matched:
                break
    assert matched


def test_positive_tabindex_detected():
    m = _load_run_egv()
    text = '<div tabIndex="2">...</div>'
    matched = False
    for pattern, label, _ in m.A11Y_PATTERNS:
        if pattern.search(text):
            matched = label == "tabindex-positive"
            if matched:
                break
    assert matched
