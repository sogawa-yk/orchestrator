# telemetry-analyst-new `Agent.run` 修正プラン

## 問題のサマリ

orchestrator の UI から `telemetry-analyst` を呼ぶと、A2A 配線は成功するが ta-new 内部で以下の 400 エラーになり Task が `failed` で返る:

```
openai.BadRequestError: Error code: 400 - {'error':
  {'code': 'invalid_value',
   'message': "Invalid 'input': expected a valid Responses API input payload.",
   'param': 'input',
   'type': 'invalid_request_error',
   'valid': True}}
```

呼び出しスタックの末端は `agents/models/openai_responses.py:662` の `await client.responses.create(**create_kwargs)`。
すなわち openai-agents SDK 0.14.8 が組み立てた `responses.create()` の **`input`** 引数が、OCI Generative AI の Responses API に拒否されている。

## 観測されている事実

| 項目 | 値 |
|---|---|
| ta-new image | telemetry-analyst-new リポジトリ main ブランチ由来 (9 日前デプロイ) |
| `openai-agents` | 0.14.8 |
| `openai` | 2.33.0 |
| `OCI_GENAI_MODEL` | `openai.gpt-oss-120b` |
| `OPENAI_BASE_URL` | `https://inference.generativeai.ap-osaka-1.oci.oraclecloud.com/openai/v1` |
| **失敗パス** | `ta.a2a.executor.TelemetryAnalystExecutor.execute` → `agent.run` (非ストリーミング) |
| 失敗時の構成 | `Runner.run(input=user_msg(str), session=OpenAIConversationsSession(...))` |
| **chainlit パス** | `agent.run_stream` (ストリーミング) — 同じ Agent インスタンスを使う |
| 既存の OCI 互換レイヤ | `_oci_compat.OCISanitizingTransport` が `mcp_call.output` 欠落と `function_call_output.output==""` のみ補完 |
| 既存テストでカバーされているか | 単体テストはモック前提で、実 OCI への入力フォーマット検証は無し |

`chainlit_app.py` も同様に `agent.run_stream(conversation_id=conv_id)` を呼ぶが、こちらは Playwright で再現していないので「動いている」と決め付けず、本プラン Phase 1 で確認する。

## 原因仮説 (確からしさの高い順)

### 仮説 H1: `Runner.run`（非ストリーミング）と `OpenAIConversationsSession` の組合せが 不正な `input` を生成
- 開発時 / 評価時には `Runner.run_streamed`（ストリーミング）経路しか叩いておらず、A2A 経由で初めて `Runner.run`（非ストリーミング）が使われた
- SDK 0.14.8 は session 経由で履歴を取得し `input` 配列に詰める。空履歴のときに `input=[]` を送り、OCI が「空の input 配列は invalid」と拒否する可能性
- もしくは session メタデータ (`previous_response_id` / `conversation` パラメータ) と `input` の併用が OCI 仕様と噛み合わない

### 仮説 H2: 直前に `conversations.create()` で生成した `conv_id` が「`responses.create(conversation=...)` に渡せる形式」と異なる
- OCI Responses API は `previous_response_id` 形式は受けるが `conversation` パラメータの仕様が OpenAI 公式と違う可能性
- `executor.py` は新規 conv を毎回作り即使うため、SDK がその conv を `responses.create(conversation=conv_id, input=[...])` に組み込んで送り、OCI 側が「input は payload として不正」と拒絶しているかもしれない

### 仮説 H3: instructions の文字数 / コンテンツが原因
- `build_instructions` でシステムプロンプト + skills + memory を結合しており、相当な長さになる
- ただし「invalid input」というエラーコードは長さ超過 (`context_length_exceeded`) とは異なるため、可能性は低い

### 除外できる仮説
- a2a-sdk の wire protocol 不整合: orchestrator → ta-new の A2A 層は **正常 200 で疎通**、Task が生成されている。問題はその後の OCI API 呼び出し
- Bearer 認証や NetworkPolicy: 401/403 ではなく **OCI 側 400** なので config は正しい
- `_oci_compat` の sanitizer: `mcp_call` も `function_call_output` も A2A 初回ターンには無いので関係しない

## 修正プラン

### Phase 1: 診断 (1〜2 時間)

**目的**: どの仮説が当たりかを実機 request body で確定させる。

1. **request body をログに出す**
   - `src/ta/agent/_oci_compat.py` の `OCISanitizingTransport.handle_async_request` で、`/responses` POST の body を **DEBUG ログに出力** する一時パッチ (`if logger.isEnabledFor(logging.DEBUG)` ガード付き)
   - 環境変数 `LOG_LEVEL=DEBUG` で再ビルド & デプロイ
   - A2A 経由で 1 回叩き、失敗した body を抽出

2. **chainlit 経路で動作するか確認**
   - Playwright で `https://ta.devday26.sogawa-yk.com/ui` (もし公開されていれば) を叩く、または cluster 内 `ta-agent-ui` に port-forward
   - 同じ質問で chainlit が成功するなら → H1 が確定 (非ストリーミング側のバグ)

3. **No-session の挙動確認**
   - A2A executor を一時的に書き換え、`oci_conv_id = None` を強制 → `agent.run(user_text, conversation_id=None)`
   - これで通れば → H1/H2 のうち session/conversation 関連の不具合が確定

成果物: 失敗する `responses.create` の引数 dump、原因の確定

### Phase 2: 修正 (2〜4 時間、根本原因による)

#### Plan A: H1 (`Runner.run` + session) が原因の場合 — **第一候補**

A2A executor を **stream 経路に揃える** ことで非ストリーミング由来のバグを回避し、chainlit と同じパスで実 OCI を叩く。

```python
# src/ta/a2a/executor.py の execute() の後半を以下のように変更:
text_parts: list[str] = []
tool_calls: list[dict] = []
response_id: str | None = None
async for ev in agent.run_stream(
    user_text, mode="engineer",
    conversation_id=oci_conv_id,
    metadata={"source": "a2a", "a2a_task_id": task_id, "a2a_context_id": ctx_id},
):
    if ev["type"] == "delta":
        text_parts.append(ev.get("text", ""))
    elif ev["type"] == "tool_call":
        tool_calls.append({"name": ev["name"], "arguments": ev.get("arguments", "")})
    elif ev["type"] == "done":
        response_id = ev.get("response_id")
final_text = "".join(text_parts) or (ev.get("text") if isinstance(ev, dict) else "")
```

**メリット**: chainlit と同じコードパスを使うため、稼働実績が活かせる。`Agent.run` 自体には触れない。

**デメリット**: 「同期 Task → Artifact 完了通知」の元設計から「内部で stream を集約して 1 回返す」に変わる。streaming サポートを A2A v1.0 で expose するのは別タスクのまま。

#### Plan B: H2 (conversation パラメータ問題) が原因の場合

A2A executor から OCI Conversations 連携を**一旦オフ**にし、A2A プロトコル層の `context_id` で会話継続を扱う方針に切り替える。

```python
# src/ta/a2a/executor.py
# OCI Conversations にマッピングして継続会話を支援  ← これを削除
result = await agent.run(
    user_text, mode="engineer",
    conversation_id=None,  # ← 強制 None
    metadata={"source": "a2a", "a2a_task_id": task_id, "a2a_context_id": ctx_id},
)
```

**メリット**: シンプル。A2A は単発リクエスト/レスポンス前提でも動く (Task ごとに独立)。

**デメリット**: 「同一 context_id 内で前回の応答を覚えている」継続会話が失われる (orchestrator 側は ReAct ループ内では同じ task しか叩かないので影響は小さい想定)。

#### Plan C: payload を sanitize で強制補正 (H1/H2 共通)

`_oci_compat.OCISanitizingTransport` を拡張し、`/responses` POST 時に:
- `input` が空配列なら `[{"role":"user","content":[{"type":"input_text","text":"..."}]}]` の shape にフォールバック
- `conversation` パラメータが OCI 不適合形式なら除外して `previous_response_id` を使う

**メリット**: SDK の挙動を一切変えずに OCI 側の差分を吸収できる (`_oci_compat` の責務に沿う)。

**デメリット**: 真の原因によっては補正が複雑化、原因によっては効かない可能性。Phase 1 の dump がないと安易に書けない。

**選定基準**: Phase 1 の調査結果に基づき:
- chainlit (stream) で成功し A2A (非 stream) で失敗 → **Plan A 採用**
- 両方失敗、no-session で成功 → **Plan B 採用**
- 両方失敗、no-session でも失敗 → **Plan C** で payload 構造を調査して個別に補正

### Phase 3: 検証 (30 分〜1 時間)

1. **ローカル単体テスト追加** (`tests/unit/test_a2a_executor.py`)
   - `TelemetryAnalystExecutor.execute` の正常ケース・例外ケースを `Agent.run` モックで検証
   - 既存テスト 199 件と合わせて全 pass を確認

2. **クラスタ反映**
   - 新 image を `nrt.ocir.io/nr3c2r62ocsa/koi-ocir-dev/ta-agent:v1-fix-input` (or 同等) でビルド & push
   - `kubectl set image` か kustomize で deployment 更新、rollout

3. **Playwright で UI E2E 再実行**
   - `docs/e2e-2026-05-10/REPORT.md` と同じ手順で「ec-shop の checkout サービスが遅い…」を投入
   - **state=completed** で diagnosis テキストが UI まで届くことを確認
   - スクショ取得し新レポート (`docs/e2e-yyyy-mm-dd/REPORT.md`) を生成

4. **iam-agent 経由フローの非劣化確認**
   - 同セッションで「OCI Identity Domains のグループ一覧を取得してください」も実行し、引き続き正常動作することを確認

## 触らないもの

- orchestrator のコード (`A2AClient`, agents.yaml 等)
- iam-agent のコード
- ta-new の `Agent.run` 内部 (Runner / SDK 周辺) — Plan A/B はあくまで **executor.py のみ** の変更で済ませる
- `OCISanitizingTransport` の既存 sanitize 処理 (mcp_call / function_call_output の補完はそのまま残す)

## リスクと対応

| リスク | 影響 | 緩和策 |
|---|---|---|
| chainlit UI 経路まで道連れにバグる | UI / CLI 利用者が影響 | Phase 1 で chainlit が現状動くか先に確認、Plan B/C の場合は executor.py のみ変更で chainlit を温存 |
| Plan A の `run_stream` 集約で artifact metadata (`tool_calls`, `response_id`) の欠落 | A2A 経由のトレースが薄くなる | done イベントから `response_id` を、tool_call イベントから tool_calls を集約 (上記コード例) |
| OCI Conversations を切ると ta-new 内で文脈ロスト | 同一 context での連続呼出時に前提情報を都度送る必要 | orchestrator の ReAct は単発呼出が基本のため当面は許容。長期的には A2A の `context_id` から OCI conv にマッピングするなら Plan A を選ぶ |
| `LOG_LEVEL=DEBUG` で平時もログ膨張 | 運用ログコスト増 | Phase 1 の調査用ビルドのみで、PR 取り込み前にデバッグログを削除 |

## 想定スケジュール

| Phase | 工数目安 |
|---|---|
| Phase 1 (診断) | 1–2h |
| Phase 2 (修正) | 2–4h (Plan による) |
| Phase 3 (検証) | 0.5–1h |
| 合計 | 3.5–7h |

## 実装方針 (推奨)

1. **Phase 1 を必ず実施**してから Plan を選ぶ。「多分 H1 だろう」で書き始めない
2. 修正 PR は executor.py の変更を **最小ハンク** に保ち、レビュー容易に
3. 修正後は Playwright での再現可能な E2E を追加し、回帰防止
