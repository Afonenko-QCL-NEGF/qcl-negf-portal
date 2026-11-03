"""Declare the selected Results constructor-fixture closure for retained tests.

These are source-only synthetic constructors, not installed Results APIs. Keep
them in their owning repository so its selected Contracts grammar stays aligned.
The integration checkout supplies the sibling repositories; missing sources must
fail the dependent tests rather than silently selecting a historical worktree.
"""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def results_fixture_sources(monkeypatch):
    components = Path(__file__).resolve().parents[2]
    source = components / "qcl-negf-results" / "tests"
    names = ("result_v2_fixture", "final_diagnostics_fixture", "consumer_context_fixture")
    for name in names:
        if not (source / f"{name}.py").is_file():
            pytest.fail(f"Selected Results constructor source is required: {source / name}")
    monkeypatch.syspath_prepend(str(source))
    for name in names:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve() == source / f"{name}.py", (
            f"Foreign Results constructor imported: {module.__file__}"
        )
