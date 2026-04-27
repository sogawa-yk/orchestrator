"""LLM-as-a-Judge による Orchestrator 改善ループ用モジュール。

- `dataset.py`: Langfuse Datasets に golden サンプルを upsert
- `judges.py`: 3 種の Evaluator プロンプト定義
- `runner.py`: Dataset を実行し trace + score を生成し報告書 (Markdown) を吐く

Langfuse v3 の LLM Connector (OCI Enterprise AI 接続) はユーザーが UI で手動設定する。
こちら (orchestrator) は Connector が設定済みである前提で、Dataset / Score API を叩く。
"""
