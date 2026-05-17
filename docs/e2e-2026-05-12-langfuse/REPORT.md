# Langfuse AI エージェント完全トレーシング E2E レポート

**実施日**: 2026-05-12
**対応内容**: orchestrator に Langfuse OTLP/HTTP エクスポータと openai-agents SDK OTel 計装を導入し、
LLM 生成・ツール呼び出し・A2A 委譲・承認 UI の判断根拠まで含めて Langfuse の Session/Trace ビューで
再現できる状態にした。

## 背景

これまで `langfuse.openai.AsyncOpenAI` ラッパで LLM 呼び出し単体のみが Langfuse に流れていたが、
Agent.run / ツール呼び出し / A2A 委譲 / 承認決定 / context_id 再利用 などの判断系は OTel 経由で
Tempo にしか出ていなかった。今回それらすべてを Langfuse でも見えるようにした。

## アーキテクチャ

```
[Chainlit UI]
    │ tracer.start_span("chat.session", {langfuse.session.id, langfuse.user.id, langfuse.tags})
    └─ tracer.start_as_current_span("chat.message", {input.value, output.value, langfuse.session.id})
        │
        └─ Runner.run(...)  ← openai-agents SDK
            │ (OpenAIAgentsInstrumentor が以下を自動で OTel span 化)
            ├─ Agent Workflow / orchestrator.agent / openai.response (GENERATION)
            └─ TOOL call_remote_agent
                └─ tracer.start_as_current_span("tool.call_remote_agent", {
                       agent.id, skill.id, routing.reason, agent_card.fetch,
                       approval.policy, approval.cache_hit,
                       context.id.source, context.id.reused, context.id,
                       a2a.state, a2a.outcome, a2a.task.id,
                       input.value, output.value
                   })
                   └─ a2a.send_message / JsonRpcTransport / POST (httpx instrumentation)

OTel TracerProvider に span_processor を 2 本登録:
  1. BatchSpanProcessor → OTLP/gRPC → otel-gateway → Tempo (既存)
  2. BatchSpanProcessor → OTLP/HTTP → https://langfuse.../api/public/otel/v1/traces (今回追加, Basic auth)
```

## 実装

### コード変更 (orchestrator repo)

| ファイル | 役割 |
|---|---|
| `src/orchestrator/observability/otel_setup.py` | Langfuse OTLP/HTTP エクスポータを追加。Basic auth ヘッダで public/secret を送る。`OpenAIAgentsInstrumentor(replace_existing_processors=True)` で openai-agents SDK の trace event を OTel 化 (built-in OpenAI tracing は除去)。 |
| `src/orchestrator/observability/langfuse_setup.py` | `langfuse.openai` ラッパ廃止 → 純 `openai.AsyncOpenAI`。`Langfuse(...)` に `tracing_enabled=False` を渡し SDK 内蔵 TracerProvider と competeしない。 |
| `src/orchestrator/observability/__init__.py` | export 更新 (`build_openai_client`) |
| `src/orchestrator/agent/runtime.py` | `build_openai_client` 利用に切替 |
| `src/orchestrator/app.py` | `chat.session` / `chat.message` span に `langfuse.session.id` / `langfuse.user.id="anonymous"` / `langfuse.tags=(chainlit, orchestrator, <env>)` / `input.value` / `output.value` を付与 |
| `src/orchestrator/agent/tools.py` | `tool.call_remote_agent` / `tool.request_user_approval` に判断属性 (routing.reason, approval.policy, context.id.reused, a2a.outcome, ...) を付与。span をメソッド全体に拡張し早期 return もトレースする |
| `pyproject.toml` | `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-openai-agents`, `langfuse>=4.0.0` を依存に追加 |

### 設定変更

- `deploy/deployment.yaml`: image を `0.4.1-langfuse-trace` に更新
- ConfigMap / Secret / NetworkPolicy: **無変更** (既存の `langfuse-keys` Secret とクラスタ内 Langfuse Service をそのまま使用)

### 単体テスト

```
$ uv run pytest tests/unit -q
........................................                                 [100%]
40 passed in 2.43s
```

新規追加: `_build_langfuse_otlp_exporter` の有効/無効分岐, `tool.call_remote_agent` の各
シナリオ (success / unknown_agent / 承認否認 / context_id 再利用) で span 属性が
期待値で設定されること, `tool.request_user_approval` の input/output/decision 属性。

## デプロイ

| 項目 | 値 |
|---|---|
| 旧 image | `kix.ocir.io/nr3c2r62ocsa/orchestrator:0.3.0-a2a-sdk` |
| 新 image | `kix.ocir.io/nr3c2r62ocsa/orchestrator:0.4.1-langfuse-trace` |
| ロールアウト | `deployment.apps/orchestrator successfully rolled out` |
| Pod | `orchestrator-6d9b795c89-...` (新), `orchestrator-6bcb4cbffb-...` (旧, 終了済) |
| Pod 起動ログ | `Langfuse OTLP span exporter を有効化` の INFO 行を確認 |

## E2E シナリオと Langfuse 検証

UI: `https://orchestrator.devday26.sogawa-yk.com` (新 image 稼働中)
Langfuse: `https://langfuse.devday26.sogawa-yk.com` (Public API 経由で検証 — UI ログインなし)

### シナリオ A: 承認不要 + context_id 再利用 (telemetry-analyst)

**入力**:
- Turn 1: 「ec-shop ネームスペースのポッドの状態を一覧して」
- Turn 2: 「再起動回数が1以上のものだけ表示して」 (follow-up)

**Langfuse 検証**:

```
session_id: 06cbb301-1f14-4549-a961-f5b515c096fc
└─ chat.session (root, tags=['chainlit', 'orchestrator', 'staging'])
   ├─ chat.message (input: 'ec-shop ネームスペースのポッドの状態を一覧して')
   │  └─ tool.call_remote_agent
   │      agent.id=telemetry-analyst, skill.id=diagnose-ec-shop
   │      routing.reason=from_registry_listed, approval.policy=not_required
   │      context.id.source=none, context.id.reused=false
   │      a2a.outcome=success
   └─ chat.message (input: '再起動回数が1以上のものだけ表示して')
      └─ tool.call_remote_agent
         context.id.source=caller, context.id=d42f28bd-...
         a2a.state=failed (リモート側 BadRequest), a2a.outcome=remote_failed
```

LLM が turn 2 で `context_id` を明示的に渡している (`context.id.source=caller`) ことが
属性から読み取れた。リモート側で BadRequest が起き `a2a.state=failed` がフラットに見え、
かつ orchestrator は前ターンの情報から代替応答を生成して綺麗に縮退している。

### シナリオ B-2: 承認要 → ユーザー承認 → リモート実行 (iam-agent)

**入力**: 「IAM Agent で create_user を呼んで。username=trace-approved-1, email=trace-approved-1@example.com, display_name=Trace Approved。承認理由は『Langfuse trace 承認パス検証』」

**UI**: AskAction で「承認」を選択 → orchestrator が `call_remote_agent` を実行 → IAM Agent が
SCIM 409 (`User with the same userName already exists`) を返す (実 IAM で同名 user が既存)

**Langfuse 検証** (trace_id: `b56e298b10e9cb61d624e3cbe143c200`):

```
tool.request_user_approval:
  agent.id=iam-agent, skill.id=create_user
  approval.reason="Langfuse trace 承認パス検証"
  approval.payload_size=107
  approval.decision=approved
  output.value="approved"

tool.call_remote_agent: (承認後)
  agent.id=iam-agent, skill.id=create_user
  routing.reason=from_registry_listed
  approval.policy=required_pre_approved  ← 承認済を policy エンジンが確認
  approval.cache_hit=true                 ← session_state からキャッシュ取得
  context.id.source=none, context.id.reused=false
  a2a.state=completed, a2a.outcome=success
  output.value="...status=409 detail=...User with the same userName already exists..."
```

承認 → 委譲 → リモート応答までの判断連鎖が一本の trace に揃った。`approval.cache_hit=true`
+ `approval.policy=required_pre_approved` の組み合わせは「承認済みキャッシュを使って呼んだ」
というセマンティクスを明示的に表す。

### シナリオ B-1: 承認要 → ユーザー却下 (iam-agent)

**入力**: 「IAM Agent で create_user を呼んで。username=test-trace1, email=test-trace1@example.com, display_name=Test Trace1。承認理由は『trace 検証用テストユーザー作成』」

**UI**: Chainlit の AskAction で「承認 / 却下」が表示 → **却下を選択** (`docs/e2e-2026-05-12-langfuse/01-approval-ui.png`)

**Langfuse 検証** (trace_id: `4cbaec5c9391d4d800e4f17525c1b715`):

```
chat.message (sessionId=6b2d475a-..., userId=anonymous)
├─ GENERATION openai.response (system=openai, operation=chat)
├─ TOOL request_user_approval (openai-agents SDK 自動 TOOL span)
├─ SPAN tool.request_user_approval (我々の span):
│     agent.id=iam-agent, skill.id=create_user
│     approval.reason="trace 検証用テストユーザー作成"
│     approval.payload_size=94, approval.decision=rejected
│     input.value="trace 検証用テストユーザー作成"
│     output.value="rejected"
└─ GENERATION openai.response (rejected を受けて LLM が代替案を生成)

[tool.call_remote_agent は呼ばれていない] ← LLM が rejected を見て中止 (期待通り)
```

「なぜ呼ばなかったか」が trace 上で確認できる: 承認を求めた → 却下された → 呼び出しを中断、
という ReAct ループの判断連鎖がそのまま span 階層で再現できる状態。

### シナリオ C: 観測の網羅性 (全 trace)

API `GET /api/public/traces?orderBy=timestamp.desc` の結果:

| 種別 | 件数の例 | 内容 |
|---|---|---|
| `chat.session` | 5 | root trace, tags=['chainlit','orchestrator','staging'], userId=anonymous |
| `chat.message` | 7 | input/output 取得, session 紐付け OK |
| TOOL spans | (sessionに包含) | `describe_remote_agent`, `request_user_approval`, `call_remote_agent` |
| GENERATION | (sessionに包含) | `openai.response` (gen_ai.system=openai, gen_ai.operation.name=chat) |
| SPAN | (sessionに包含) | `Agent Workflow`, `orchestrator.agent`, `a2a.send_message`, `JsonRpcTransport.*` |
| `POST` (root) | 多数 | httpx instrumentation で出る単独 trace (a2a httpx 呼び出しは A2A SDK 自体が context propagation していないため root 扱いになる) |

すべての session に `userId=anonymous`、`environment=staging`、tags 3 種が付与されていることを確認。

## 既知の制限と今後の改善

| 項目 | 状況 | 影響 | 推奨対応 |
|---|---|---|---|
| GENERATION の `model` / token usage | `model=null`, `usage=0` | コスト可視化と model 別フィルタが効かない | `opentelemetry-instrumentation-openai-agents` の `_extract_response_attributes` が `gen_ai.request.model` / `gen_ai.usage.*` を埋めない。回避策として `OpenAIChatCompletionsModel` をラップして手動で span attribute を埋める拡張を別タスクで検討 |
| 単発 `POST` trace が孤立 | 影響軽微 | Langfuse で a2a の httpx 呼び出しが root として現れる | A2A SDK 内部で OTel context propagation を起動するか、httpx 計装側で suppression するかの設計判断が必要 |
| ChainLit の `session_id` と Chainlit `thread_id` の関係 | 別 ID で管理 | Chainlit UI 上での再接続時に session_id が再採番される (ログ上 3 連発) | Chainlit thread_id を `langfuse.session.id` 第二候補に使う検討は将来 |
| `chat.message` トレースの `tags` フィールド (root 以外) | 空配列 | 検索時は session.tags でフィルタすれば OK | Langfuse の仕様 — session に tag を付ければ全 trace に propagation される |

## 参照

- 実装計画: `/home/opc/.claude/plans/orchestrator-langfuse-keys-orchestrator-breezy-kitten.md`
- Langfuse 受信エンドポイント: `${LANGFUSE_HOST}/api/public/otel/v1/traces` (Basic auth `public_key:secret_key`)
- 主要ファイル diff:
  - `src/orchestrator/observability/otel_setup.py`
  - `src/orchestrator/observability/langfuse_setup.py`
  - `src/orchestrator/agent/tools.py`
  - `src/orchestrator/app.py`
- 検証スクリーンショット: `01-approval-ui.png` (承認 UI), `02-chat-flow.png` (会話フロー全体)
