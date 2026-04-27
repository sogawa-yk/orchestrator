"""Eval ループ runner。

Langfuse v3 の LLM Connector (OCI Enterprise AI 接続) はユーザーが UI で手動設定する。
本 runner は **Connector 設定が完了した前提**で動く。設定前は `--check-only` モードで
依存の状態だけ確認する。

使い方:
  uv run python -m orchestrator.eval.runner --check-only
  uv run python -m orchestrator.eval.runner --upsert-dataset
  uv run python -m orchestrator.eval.runner --iter 1   # Connector 設定後

ループ 1 周の流れ:
  1. Dataset を取得 (Langfuse `orchestrator-golden-v1`)
  2. 各 item を Orchestrator (Agent) に流して trace を生成
  3. 全 trace に対し 3 種の Judge を実行 (Connector 経由 or 自前 OCI 呼出)
  4. スコアを Langfuse Score API へ post
  5. eval/runs/{date}-iter{N}/report.md を出力
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..observability.langfuse_setup import get_langfuse_client
from .dataset import DATASET_NAME, GOLDEN_ITEMS, upsert_to_langfuse
from .judges import ALL_JUDGES

logger = logging.getLogger(__name__)


def check_only() -> dict[str, Any]:
    """Langfuse 接続と Dataset 存在を確認する (Connector 設定前でも実行可)。"""
    s = get_settings()
    out: dict[str, Any] = {
        "langfuse_host": s.langfuse_host,
        "langfuse_keys_set": bool(s.langfuse_public_key and s.langfuse_secret_key),
        "openai_base_url": s.openai_base_url,
        "openai_key_set": bool(s.openai_api_key),
        "model": s.orch_model,
        "registry_path": str(s.orch_agents_path),
        "dataset_name": DATASET_NAME,
        "golden_items": len(GOLDEN_ITEMS),
    }
    client = get_langfuse_client(s)
    if client is None:
        out["langfuse_client"] = "unavailable"
        return out
    out["langfuse_client"] = "ok"
    try:
        ds = client.get_dataset(DATASET_NAME)
        out["dataset_exists"] = True
        out["dataset_items_count"] = len(ds.items) if hasattr(ds, "items") else None
    except Exception as e:  # noqa: BLE001
        out["dataset_exists"] = False
        out["dataset_lookup_error"] = str(e)
    return out


def run_iteration(iter_num: int) -> dict[str, Any]:
    """改善ループ 1 周。Langfuse v3 LLM Connector が設定済みである前提。

    現状は雛形のみ。実際の (1) Agent 実行 (2) Judge 実行 (3) Score post は
    Langfuse Connector 経由で動く環境が用意されてから本実装する。
    """
    s = get_settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return {"ok": False, "reason": "Langfuse keys 未設定"}

    today = _dt.date.today().isoformat()
    out_dir = Path("eval/runs") / f"{today}-iter{iter_num}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"

    placeholder = (
        f"# Eval Iteration {iter_num} ({today})\n\n"
        "## 状態\n\n"
        "Langfuse v3 の LLM Connector (OCI Enterprise AI 接続) 設定がユーザー側でまだ完了していない、\n"
        "もしくは runner の本実装が未着手のため、本ファイルは雛形です。\n\n"
        "Connector 設定が完了したら以下を実装してください:\n\n"
        "1. Dataset (`orchestrator-golden-v1`) を取得\n"
        "2. 各 item の `input.messages` を Orchestrator Agent に渡し trace を生成\n"
        "3. 3 種の Judge (`tool-selection` / `approval-correctness` / `final-helpfulness`) を Connector 経由で実行\n"
        "4. スコアを Langfuse Score API へ post\n"
        "5. 本ファイルに採点表 + 改善差分要約を追記\n\n"
        f"## Judges\n\n"
        + "\n".join(f"- {j.name} (range {j.score_range})" for j in ALL_JUDGES)
        + "\n"
    )
    report_path.write_text(placeholder, encoding="utf-8")
    return {
        "ok": True,
        "iter": iter_num,
        "report": str(report_path),
        "note": "Connector 設定が必要です。準備が整い次第お知らせください。",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Orchestrator eval runner")
    p.add_argument("--check-only", action="store_true", help="依存の状態確認のみ")
    p.add_argument("--upsert-dataset", action="store_true", help="Dataset を upsert")
    p.add_argument("--iter", type=int, default=0, help="改善ループの周回番号")
    args = p.parse_args(argv)

    if args.check_only:
        print(json.dumps(check_only(), ensure_ascii=False, indent=2))
        return 0
    if args.upsert_dataset:
        print(json.dumps(upsert_to_langfuse(), ensure_ascii=False, indent=2))
        return 0
    if args.iter > 0:
        print(json.dumps(run_iteration(args.iter), ensure_ascii=False, indent=2))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
