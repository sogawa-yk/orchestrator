from __future__ import annotations

import json
import textwrap

import pytest
import yaml
from pydantic import ValidationError

from orchestrator.registry import load_registry
from orchestrator.registry.loader import load_registry_from_text
from orchestrator.registry.policy import requires_approval


SAMPLE_YAML = textwrap.dedent(
    """\
    version: 1
    defaults:
      timeout_seconds: 60
      retry: { max_attempts: 2, backoff_seconds: 1.5 }
      card_cache_ttl_seconds: 600
    agents:
      - id: telemetry-analyst
        display_name: Telemetry Analyst
        base_url: http://ta-agent.telemetry-analyst.svc:8080/a2a
        auth: { kind: bearer, token_env: TA_AGENT_A2A_TOKEN }
        enabled: true
        tags: [observability]
        approval:
          default: not_required
          skills:
            diagnose-ec-shop: { requires_approval: false }
            secret-skill: { requires_approval: true }
      - id: incident-responder
        display_name: Incident Responder
        base_url: http://ir.example.svc:8080/a2a
        enabled: false
        approval:
          default: required
    """
)


def test_load_registry_from_text() -> None:
    reg = load_registry_from_text(SAMPLE_YAML)
    assert len(reg.agents) == 2
    assert reg.get("telemetry-analyst") is not None
    assert reg.get("missing") is None
    assert [a.id for a in reg.enabled_agents()] == ["telemetry-analyst"]


def test_load_registry_file_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path / "missing.yaml")


@pytest.fixture()
def reg():
    return load_registry_from_text(SAMPLE_YAML)


def test_explicit_skill_false(reg) -> None:
    a = reg.get("telemetry-analyst")
    assert requires_approval(a, "diagnose-ec-shop") is False


def test_explicit_skill_true(reg) -> None:
    a = reg.get("telemetry-analyst")
    assert requires_approval(a, "secret-skill") is True


def test_default_required_when_no_skill_entry(reg) -> None:
    a = reg.get("incident-responder")
    assert requires_approval(a, "any-skill") is True


def test_card_metadata_used_when_no_explicit(reg) -> None:
    a = reg.get("telemetry-analyst")
    card = {
        "skills": [
            {"id": "card-only-skill", "metadata": {"x-orchestrator": {"requires_approval": True}}}
        ]
    }
    assert requires_approval(a, "card-only-skill", agent_card=card) is True


def test_card_overrides_explicit_false_safe_side(reg) -> None:
    """安全側ルール: 明示 False でも card が True 申告ならば True を採用"""
    a = reg.get("telemetry-analyst")
    card = {
        "skills": [
            {
                "id": "diagnose-ec-shop",
                "metadata": {"x-orchestrator": {"requires_approval": True}},
            }
        ]
    }
    assert requires_approval(a, "diagnose-ec-shop", agent_card=card) is True


def test_card_does_not_override_explicit_true(reg) -> None:
    """明示 True で card が False でも True を維持"""
    a = reg.get("telemetry-analyst")
    card = {
        "skills": [
            {
                "id": "secret-skill",
                "metadata": {"x-orchestrator": {"requires_approval": False}},
            }
        ]
    }
    assert requires_approval(a, "secret-skill", agent_card=card) is True


def test_unknown_skill_falls_back_to_default(reg) -> None:
    a = reg.get("telemetry-analyst")
    assert requires_approval(a, "unlisted") is False


SAMPLE_JSON = json.dumps(yaml.safe_load(SAMPLE_YAML))


def test_load_registry_from_json_env(tmp_path) -> None:
    """A2A_AGENTS_JSON 経路: ファイル不在でも JSON で同等の Registry を組める。"""
    reg_json = load_registry(tmp_path / "missing.yaml", json_text=SAMPLE_JSON)
    reg_yaml = load_registry_from_text(SAMPLE_YAML)
    assert {a.id for a in reg_json.agents} == {a.id for a in reg_yaml.agents}
    assert [a.id for a in reg_json.enabled_agents()] == [
        a.id for a in reg_yaml.enabled_agents()
    ]


def test_json_takes_precedence_over_yaml(tmp_path) -> None:
    yaml_path = tmp_path / "agents.yaml"
    yaml_path.write_text(SAMPLE_YAML, encoding="utf-8")
    only_one = json.dumps(
        {
            "version": 1,
            "agents": [
                {
                    "id": "json-only",
                    "display_name": "JSON Only",
                    "base_url": "http://json-only/a2a",
                }
            ],
        }
    )
    reg = load_registry(yaml_path, json_text=only_one)
    assert [a.id for a in reg.agents] == ["json-only"]


def test_empty_json_falls_back_to_yaml(tmp_path) -> None:
    yaml_path = tmp_path / "agents.yaml"
    yaml_path.write_text(SAMPLE_YAML, encoding="utf-8")
    reg = load_registry(yaml_path, json_text="   ")
    assert {a.id for a in reg.agents} == {"telemetry-analyst", "incident-responder"}


def test_invalid_json_raises(tmp_path) -> None:
    with pytest.raises(ValidationError):
        load_registry(tmp_path / "missing.yaml", json_text="{not json")
