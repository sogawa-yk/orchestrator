from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.agent import runtime


@pytest.fixture(autouse=True)
def _restore_env():
    saved = os.environ.get("ORCH_PROMPT_PATH")
    yield
    if saved is None:
        os.environ.pop("ORCH_PROMPT_PATH", None)
    else:
        os.environ["ORCH_PROMPT_PATH"] = saved


def test_load_system_prompt_falls_back_to_bundled_when_env_unset():
    os.environ.pop("ORCH_PROMPT_PATH", None)
    prompt = runtime._load_system_prompt()
    assert "オーケストレータ" in prompt
    assert "横断依頼" in prompt


def test_load_system_prompt_prefers_external_file(tmp_path: Path):
    external = tmp_path / "system.ja.md"
    external.write_text("# OVERRIDE\nhello\n", encoding="utf-8")
    os.environ["ORCH_PROMPT_PATH"] = str(external)
    prompt = runtime._load_system_prompt()
    assert prompt.startswith("# OVERRIDE")


def test_load_system_prompt_falls_back_when_external_missing(tmp_path: Path, caplog):
    missing = tmp_path / "nope.md"
    os.environ["ORCH_PROMPT_PATH"] = str(missing)
    with caplog.at_level("WARNING"):
        prompt = runtime._load_system_prompt()
    assert "オーケストレータ" in prompt
    assert any("ORCH_PROMPT_PATH" in r.message for r in caplog.records)


def test_load_system_prompt_contains_multi_domain_decomposition_section():
    os.environ.pop("ORCH_PROMPT_PATH", None)
    prompt = runtime._load_system_prompt()
    assert "複数領域依頼" in prompt
    assert "領域分解の手順" in prompt
    assert "ユーザー影響" in prompt
    assert "リソース影響" in prompt
