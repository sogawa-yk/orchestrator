"""Langfuse Datasets に Orchestrator の golden サンプル 15 件を upsert する。

カテゴリと件数:
- 通常診断 (承認不要)            4
- 承認必要 → Approve            2
- 承認必要 → Reject              2
- 承認却下後の代替提案            2 (Reject の発展)
- input-required 中断・再開       2
- 失敗系 (401 / Timeout / 不明)   2
- マルチターン文脈継続           2
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from ..observability.langfuse_setup import get_langfuse_client

DATASET_NAME = "orchestrator-golden-v1"


@dataclass
class GoldenItem:
    id: str
    category: str
    inputs: list[dict[str, str]]  # [{"role":"user","content":"..."}, ...]
    expected_output: dict[str, Any]
    metadata: dict[str, Any]


GOLDEN_ITEMS: list[GoldenItem] = [
    # 通常診断 4
    GoldenItem(
        id="diag-pods",
        category="normal-diagnosis",
        inputs=[{"role": "user", "content": "ec-shop の Pod 一覧を見せて"}],
        expected_output={"contains": ["ec-web", "Running"], "should_call": ["call_remote_agent"]},
        metadata={"agent": "telemetry-analyst", "skill": "diagnose-ec-shop", "approval": "none"},
    ),
    GoldenItem(
        id="diag-checkout-slow",
        category="normal-diagnosis",
        inputs=[{"role": "user", "content": "ec-shop の checkout が遅い気がする。原因を調べて。"}],
        expected_output={"contains": ["checkout", "原因"], "should_call": ["call_remote_agent"]},
        metadata={"agent": "telemetry-analyst", "skill": "diagnose-ec-shop", "approval": "none"},
    ),
    GoldenItem(
        id="diag-5xx",
        category="normal-diagnosis",
        inputs=[{"role": "user", "content": "ec-shop で 5xx 増えてないか確認して"}],
        expected_output={"contains": ["5xx"], "should_call": ["call_remote_agent"]},
        metadata={"agent": "telemetry-analyst", "skill": "diagnose-ec-shop", "approval": "none"},
    ),
    GoldenItem(
        id="diag-cart-restart",
        category="normal-diagnosis",
        inputs=[{"role": "user", "content": "cart の Pod が再起動を繰り返している。原因は?"}],
        expected_output={"contains": ["cart"], "should_call": ["call_remote_agent"]},
        metadata={"agent": "telemetry-analyst", "skill": "diagnose-ec-shop", "approval": "none"},
    ),
    # 承認必要 → Approve 2
    GoldenItem(
        id="appr-approve-priv-pods",
        category="approval-approved",
        inputs=[
            {"role": "user", "content": "高権限プロファイルで ec-shop の Pod を診断して"},
            {"role": "system", "content": "[テスト前提] 承認 UI で Approve を選ぶ"},
        ],
        expected_output={
            "should_call": ["request_user_approval", "call_remote_agent"],
            "approval_must_precede_call": True,
        },
        metadata={"agent": "telemetry-analyst-privileged", "skill": "diagnose-ec-shop", "approval": "approved"},
    ),
    GoldenItem(
        id="appr-approve-priv-5xx",
        category="approval-approved",
        inputs=[
            {"role": "user", "content": "高権限で 5xx を調査して"},
            {"role": "system", "content": "[テスト前提] 承認 UI で Approve を選ぶ"},
        ],
        expected_output={
            "should_call": ["request_user_approval", "call_remote_agent"],
            "approval_must_precede_call": True,
        },
        metadata={"agent": "telemetry-analyst-privileged", "skill": "diagnose-ec-shop", "approval": "approved"},
    ),
    # 承認必要 → Reject 2
    GoldenItem(
        id="appr-reject-priv-pods",
        category="approval-rejected",
        inputs=[
            {"role": "user", "content": "高権限プロファイルで ec-shop の Pod を診断して"},
            {"role": "system", "content": "[テスト前提] 承認 UI で Reject を選ぶ"},
        ],
        expected_output={
            "should_call": ["request_user_approval"],
            "should_not_call": ["call_remote_agent"],
            "final_must_mention": ["却下", "代替"],
        },
        metadata={"agent": "telemetry-analyst-privileged", "skill": "diagnose-ec-shop", "approval": "rejected"},
    ),
    GoldenItem(
        id="appr-reject-priv-cart",
        category="approval-rejected",
        inputs=[
            {"role": "user", "content": "高権限で cart の状態を診断して"},
            {"role": "system", "content": "[テスト前提] 承認 UI で Reject を選ぶ"},
        ],
        expected_output={
            "should_call": ["request_user_approval"],
            "should_not_call": ["call_remote_agent"],
            "final_must_mention": ["却下"],
        },
        metadata={"agent": "telemetry-analyst-privileged", "skill": "diagnose-ec-shop", "approval": "rejected"},
    ),
    # 却下後の代替提案 (Reject 後にユーザーが代替を求める) 2
    GoldenItem(
        id="appr-reject-then-alt-pods",
        category="rejected-alternative",
        inputs=[
            {"role": "user", "content": "高権限プロファイルで ec-shop の Pod を診断して"},
            {"role": "system", "content": "[テスト前提] 承認 UI で Reject"},
            {"role": "user", "content": "じゃあ承認不要のプロファイルで同じ調査して"},
        ],
        expected_output={
            "should_call": ["request_user_approval", "call_remote_agent"],
            "second_call_agent": "telemetry-analyst",
        },
        metadata={"category_note": "却下後に承認不要エージェントへ切り替え"},
    ),
    GoldenItem(
        id="appr-reject-then-alt-5xx",
        category="rejected-alternative",
        inputs=[
            {"role": "user", "content": "高権限で 5xx を確認して"},
            {"role": "system", "content": "[テスト前提] 承認 UI で Reject"},
            {"role": "user", "content": "通常プロファイルで再度お願い"},
        ],
        expected_output={
            "should_call": ["request_user_approval", "call_remote_agent"],
            "second_call_agent": "telemetry-analyst",
        },
        metadata={},
    ),
    # input-required (現状 ta-agent では再現しないためモック想定) 2
    GoldenItem(
        id="input-req-namespace",
        category="input-required",
        inputs=[
            {"role": "user", "content": "アプリの調子が悪い"},
            {"role": "system", "content": "[テスト前提] リモートが namespace 質問で input-required を返す"},
            {"role": "user", "content": "ec-shop"},
        ],
        expected_output={"should_resume_with": "ec-shop"},
        metadata={"agent": "telemetry-analyst", "skill": "diagnose-ec-shop"},
    ),
    GoldenItem(
        id="input-req-period",
        category="input-required",
        inputs=[
            {"role": "user", "content": "ec-shop の最近の異常を調べて"},
            {"role": "system", "content": "[テスト前提] リモートが期間を input-required で求める"},
            {"role": "user", "content": "直近 1 時間"},
        ],
        expected_output={"should_resume_with": "1 時間"},
        metadata={"agent": "telemetry-analyst", "skill": "diagnose-ec-shop"},
    ),
    # 失敗系 2
    GoldenItem(
        id="fail-unknown-agent",
        category="failure",
        inputs=[{"role": "user", "content": "存在しないエージェント notthere を呼んで"}],
        expected_output={
            "should_not_call": ["call_remote_agent"],
            "final_must_mention": ["利用できない", "存在しない", "見つからない"],
        },
        metadata={"failure": "unknown-agent"},
    ),
    GoldenItem(
        id="fail-unauthorized",
        category="failure",
        inputs=[
            {"role": "user", "content": "ec-shop を診断して"},
            {"role": "system", "content": "[テスト前提] リモートが 401 を返す"},
        ],
        expected_output={"final_must_mention": ["unauthorized", "認証"]},
        metadata={"failure": "401"},
    ),
    # マルチターン 2
    GoldenItem(
        id="multiturn-followup",
        category="multiturn",
        inputs=[
            {"role": "user", "content": "ec-shop の Pod 一覧を見せて"},
            {"role": "user", "content": "今度は再起動回数だけ表で見せて"},
        ],
        expected_output={"should_reuse_context_id": True},
        metadata={"agent": "telemetry-analyst"},
    ),
    GoldenItem(
        id="multiturn-narrow",
        category="multiturn",
        inputs=[
            {"role": "user", "content": "checkout が遅い"},
            {"role": "user", "content": "じゃあ過去 1 時間に絞って"},
        ],
        expected_output={"should_reuse_context_id": True},
        metadata={"agent": "telemetry-analyst"},
    ),
]


def upsert_to_langfuse() -> dict[str, Any]:
    """Langfuse Datasets に golden 15 件を作成 (既存があれば追加のみ)。"""
    client = get_langfuse_client(get_settings())
    if client is None:
        return {"ok": False, "reason": "Langfuse client unavailable (key 未設定)"}

    # Dataset が無ければ作る
    try:
        client.create_dataset(name=DATASET_NAME, description="Orchestrator P3 golden 15")
    except Exception:  # noqa: BLE001 — 既存 Dataset の場合 409 を投げる SDK 実装あり
        pass

    created = 0
    skipped = 0
    for item in GOLDEN_ITEMS:
        try:
            client.create_dataset_item(
                dataset_name=DATASET_NAME,
                input={"messages": item.inputs, "category": item.category, "id": item.id},
                expected_output=item.expected_output,
                metadata={**item.metadata, "category": item.category, "id": item.id},
            )
            created += 1
        except Exception as e:  # noqa: BLE001
            # 既存 item の場合は skip としてカウント
            skipped += 1
            continue

    return {
        "ok": True,
        "dataset": DATASET_NAME,
        "total": len(GOLDEN_ITEMS),
        "created": created,
        "skipped": skipped,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(upsert_to_langfuse(), ensure_ascii=False, indent=2))
