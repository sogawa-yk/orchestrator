from __future__ import annotations


class A2AError(Exception):
    """A2A 呼び出しの基底例外。"""


class RemoteAgentUnauthorized(A2AError):
    """401。Bearer Token が不正または欠落。"""


class RemoteAgentUnavailable(A2AError):
    """5xx またはサーバ側 A2A 無効。"""


class RemoteAgentTimeout(A2AError):
    """接続/読み込みタイムアウト。"""


class RemoteAgentFailed(A2AError):
    """Task が failed / canceled で返ってきた、または JSON-RPC エラー。"""


class InputRequired(A2AError):
    """Task が input-required で中断。再開のため task_id / context_id / prompt を保持。"""

    def __init__(
        self, prompt: str, *, task_id: str | None, context_id: str | None
    ) -> None:
        super().__init__(prompt)
        self.prompt = prompt
        self.task_id = task_id
        self.context_id = context_id
