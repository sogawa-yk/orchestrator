# P3 改善ループ 実施結果サマリ

実施日: 2026-04-27
Dataset: `orchestrator-golden-v1` (active 10 件)
モデル: `openai.gpt-4.1` (OCI Enterprise AI、Langfuse v3 LLM Connector 経由)
Judge 評価モデル: 同上

## スコア推移

| Iteration | tool-selection (0-5) | approval-correctness (0-1) | final-helpfulness (0-5) | 主な変更 |
|---|---|---|---|---|
| 1 | 4.90 | **0.30** | 4.50 | 初期実装 |
| 2 | 4.40 | **1.00** | 4.10 | judge プロンプトに `metadata.approval` 情報を渡し採点ロジックを明文化 |
| 3 | 4.40 | 1.00 | 4.00 | system prompt に context_id 再利用ルールと失敗 kind の明示ルールを追加 |

## iter1 で判明したこと

iter1 は **eval を最初ローカルから実行**したが、ローカルから `ta-agent.telemetry-analyst.svc:8080` への
DNS 解決が効かず AgentCard 取得が失敗 → Agent が `call_remote_agent` を諦め、ほぼ全 trace で
describe_remote_agent のみ呼ばれる結果になった。これは eval の実行環境の問題であり、Agent の
コード起因ではない。

**対処**: eval は **orchestrator Pod 内で実行**することにし、同一クラスタ DNS で ta-agent に到達
できる状態で計測する運用に確定。以降の iter2/3 はすべて Pod 内実行。

## iter1→2 の改善 (approval-correctness 0.30 → 1.00)

iter1 の low-score の原因は **agent 側ではなく judge 側のプロンプト不備**。Judge は trace から
needs_approval を推測する以外なく、本来正しい挙動 (承認不要スキルで request_user_approval を
呼ばない) を「もしかしたら承認必須かもしれない」と誤判定して減点していた。

**対処** (`src/orchestrator/eval/judges.py`):
- `metadata.approval` フィールド (`none` / `approved` / `rejected` / 未指定) を Judge に渡す
- システムプロンプトで「metadata.approval が確定情報、tool_calls からの推測より優先」と明記
- 採点ルール (a)(b)(c) を `approval` 値ごとに具体化

これで approval-correctness は **10/10 が 1.0** に到達し、目標を達成。

## iter2→3 の改善 (system prompt の細部チューニング)

iter2 のレポートで残った真の low-score:

- `multiturn-narrow` tool-selection 4.0: 「2 ターン目で前回の context_id が再利用されていない」
- `fail-unauthorized` final-helpfulness 1.0: 「最終応答に "unauthorized" 等のキーワードがない」

**対処** (`src/orchestrator/agent/prompts/system.ja.md`):
- マルチターン会話で context_id の再利用を強く要求するセクションを追加
- 失敗時、`call_remote_agent` の戻り値の `kind` フィールド (unauthorized / unavailable / timeout /
  failed) を最終応答に **そのまま明示** することを要求

iter3 でスコアは大きくは動かなかった (LLM 評価のばらつき範囲)。テストケース側に
「ta-agent では実機再現しない」前提 (`input-required` を返す、401 を返す) があり、
これらは Agent の挙動と Judge の期待が必然的にずれる。

## 終了条件 (P3 計画) との対比

P3 計画の終了条件:
- 全 Evaluator 平均が閾値超え (tool-selection ≥ 4.0、approval-correctness ほぼ 1.0、
  final-helpfulness ≥ 4.0)
- approval-correctness が **15 件中 14 件以上 1.0** (active 10 件版では 9-10 件)

iter3 達成状況:
- tool-selection 4.40 ≥ 4.0 ✅
- approval-correctness 1.00 (10/10) ≥ 0.93 (≒ 14/15) ✅
- final-helpfulness 4.00 ≥ 4.0 ✅ (ボーダーぎりぎりだが達成)

**3 周到達、スコア閾値クリア、これ以上の劇的な改善はテストデータ側の手直しが先**、
として一旦終了とする。

## 次に効きそうな改善 (将来作業)

1. **テストデータの手直し** (実機再現性):
   - `input-req-namespace` / `input-req-period`: ta-agent が input-required を返さないので、
     代わりに「最初の発話で意図を曖昧にし、agent が user に逆質問する」ケースに置換
   - `fail-unauthorized`: 401 を実機で発生させるには認証エラーを起こす設定が必要。代わりに
     「未知の skill を呼ぶ」ケース等で失敗系を確認
2. **Judge のばらつき低減**: Judge を gpt-4.1 ではなく `openai.gpt-4.1-mini` 等の安価な
   モデルにし、`temperature=0` を確実に使い、複数回採点して平均を取る (cost vs ばらつきの
   トレードオフ)
3. **承認必須エージェントが追加されたら** `DEFERRED_PRIVILEGED_ITEMS` を有効化し追加で 6 件評価

## 運用メモ

- 本ループは **Pod 内で実行** が前提 (`kubectl exec ... python -m orchestrator.eval.runner`)
- レポートは Pod の `/app/eval/runs/{date}-iter{N}/report.md` に出力されるので、
  `kubectl cp` で取り出して git に保存する
- 「Evaluator failed: 'dict' object has no attribute 'name'」という Langfuse SDK 内の
  ERROR が ~1-3 件出るが、トータル評価には影響しない (10 件のうち集計に到達しているのは
  全件)。SDK バージョン更新で解消されるか別途調査
