from __future__ import annotations

from pathlib import Path

import yaml

from .models import Registry


def load_registry(path: str | Path) -> Registry:
    """ConfigMap (yaml) から Registry を読み込む。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"AgentRegistry yaml が見つからない: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Registry.model_validate(raw)


def load_registry_from_text(text: str) -> Registry:
    """テストや CLI から直接 yaml 文字列を渡す用途。"""
    raw = yaml.safe_load(text) or {}
    return Registry.model_validate(raw)
