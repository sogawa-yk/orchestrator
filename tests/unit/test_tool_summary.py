"""`_summarize_tool_call` / `_summarize_tool_output` の単体テスト。

UI に出る 1 行サマリが各ツール × 各結果パターンで期待通りであることを確認する。
"""
from __future__ import annotations

from orchestrator.app import (
    _summarize_tool_call,
    _summarize_tool_output,
)


def test_summarize_tool_call_list_remote_agents() -> None:
    assert _summarize_tool_call("list_remote_agents", {}) == "利用可能なエージェント一覧を取得"


def test_summarize_tool_call_describe_remote_agent() -> None:
    assert (
        _summarize_tool_call("describe_remote_agent", {"agent_id": "ta-agent"})
        == "エージェント `ta-agent` の詳細を取得"
    )


def test_summarize_tool_call_describe_remote_agent_missing_id() -> None:
    assert (
        _summarize_tool_call("describe_remote_agent", {})
        == "エージェント `?` の詳細を取得"
    )


def test_summarize_tool_call_request_user_approval() -> None:
    out = _summarize_tool_call(
        "request_user_approval",
        {"reason": "本番クラスタへの操作"},
    )
    assert out == "ユーザー承認をリクエスト: 本番クラスタへの操作"


def test_summarize_tool_call_request_user_approval_truncates_long_reason() -> None:
    long = "a" * 200
    out = _summarize_tool_call("request_user_approval", {"reason": long})
    # 60 文字超は短縮 (57 + "…")
    assert out.startswith("ユーザー承認をリクエスト: ")
    assert out.endswith("…")
    assert len(out) < 80


def test_summarize_tool_call_request_user_approval_no_reason() -> None:
    assert (
        _summarize_tool_call("request_user_approval", {}) == "ユーザー承認をリクエスト"
    )


def test_summarize_tool_call_call_remote_agent() -> None:
    out = _summarize_tool_call(
        "call_remote_agent",
        {"agent_id": "ta-agent", "skill_id": "list_kustomizations"},
    )
    assert out == "`ta-agent` に `list_kustomizations` を依頼"


def test_summarize_tool_call_unknown_tool() -> None:
    assert _summarize_tool_call("mystery_tool", {}) == "ツール `mystery_tool` を実行"
    assert _summarize_tool_call("", {}) == "ツール実行"


def test_summarize_tool_output_list_remote_agents() -> None:
    assert _summarize_tool_output("list_remote_agents", [{}, {}, {}]) == "3 件取得"
    assert _summarize_tool_output("list_remote_agents", []) == "0 件取得"
    # 想定外の型でもクラッシュしない
    assert _summarize_tool_output("list_remote_agents", "fallback") == "取得"


def test_summarize_tool_output_describe_remote_agent_success() -> None:
    out = _summarize_tool_output(
        "describe_remote_agent",
        {"skills": [{"id": "a"}, {"id": "b"}]},
    )
    assert out == "2 skill"


def test_summarize_tool_output_describe_remote_agent_error() -> None:
    assert (
        _summarize_tool_output("describe_remote_agent", {"error": "AgentCard 取得失敗"})
        == "失敗"
    )


def test_summarize_tool_output_request_user_approval_decisions() -> None:
    assert (
        _summarize_tool_output("request_user_approval", {"decision": "approved"})
        == "承認"
    )
    assert (
        _summarize_tool_output("request_user_approval", {"decision": "rejected"})
        == "拒否"
    )
    assert (
        _summarize_tool_output("request_user_approval", {"decision": "timeout"})
        == "タイムアウト"
    )


def test_summarize_tool_output_call_remote_agent_states() -> None:
    assert (
        _summarize_tool_output("call_remote_agent", {"state": "completed"}) == "完了"
    )
    assert (
        _summarize_tool_output("call_remote_agent", {"state": "input-required"})
        == "追加入力待ち"
    )
    assert (
        _summarize_tool_output("call_remote_agent", {"state": "failed"}) == "失敗"
    )


def test_summarize_tool_output_call_remote_agent_error_denied() -> None:
    out = _summarize_tool_output(
        "call_remote_agent",
        {"error": "承認が必要", "needs_approval": True, "denied": True},
    )
    assert out == "失敗 (denied)"


def test_summarize_tool_output_call_remote_agent_error_unauthorized() -> None:
    out = _summarize_tool_output(
        "call_remote_agent",
        {"error": "unauthorized: 401", "kind": "unauthorized"},
    )
    assert out == "失敗 (unauthorized)"


def test_summarize_tool_output_unknown_tool_falls_back() -> None:
    assert _summarize_tool_output("mystery", {"anything": 1}) == "完了"
