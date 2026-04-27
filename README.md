# orchestrator

クラスタ内 A2A 対応エージェントを束ねる **Supervisor 型** AI エージェント。
単一 ReAct ループ + コンテキストエンジニアリングで、専門エージェントへの委譲・承認 UI 提示・観測を担う。

## 全体像

- **UI**: Chainlit (`@cl.on_chat_start` / `@cl.on_message`)
- **エージェントランタイム**: openai-agents SDK (OCI Enterprise AI を OpenAI 互換で利用)
- **A2A**: httpx + JSON-RPC 直叩き (`message/send`, `tasks/get` など A2A v1.0)
- **承認 UI**: Chainlit `AskActionMessage` (Pre-approval) + `AskUserMessage` (input-required)
- **観測**: OpenTelemetry SDK (Tempo/Loki/Mimir → Grafana) + Langfuse (LLM 監査)
- **設定**: ConfigMap (`orchestrator-config`, `orchestrator-agents`) + Secret 投影

詳細は `/home/opc/.claude/plans/a2a-ai-ai-ui-kubernetes-pod-react-a2a-do-staged-cookie.md` の実装計画を参照。

## ディレクトリ

```
src/orchestrator/
  agent/        単一 ReAct エージェント (runtime / tools / prompts / context)
  registry/     AgentRegistry (ConfigMap 投影 + AgentCard キャッシュ + 承認ポリシー)
  a2a_client/   A2A v1.0 JSON-RPC クライアント
  approval/     Chainlit 承認 UI と session state
  observability/ OTel SDK / Langfuse 初期化
  eval/         (P3) Langfuse Datasets と LLM-as-a-Judge

tests/unit/   ローカル単体テスト
deploy/       Kubernetes manifests (kustomize)
```

## ローカル起動

```bash
# 依存解決
uv sync --extra dev

# 単体テスト
uv run pytest tests/unit -q

# ローカルで AgentRegistry を直書きしたい場合は env で path を上書き
export ORCH_AGENTS_PATH=$PWD/deploy/configmap-orchestrator-agents.yaml  # ※ ConfigMap yaml ではなく中身の agents.yaml が必要
# 実際は以下のように agents.yaml を抽出して使う:
yq '.data."agents.yaml"' deploy/configmap-orchestrator-agents.yaml > /tmp/agents.yaml
export ORCH_AGENTS_PATH=/tmp/agents.yaml

# 環境変数 (例: cluster 内 Bearer Token を使う場合)
export OPENAI_API_KEY=$(kubectl get secret enterprise-ai-api-key -n default -o jsonpath='{.data.api-key}' | base64 -d)
export TA_AGENT_A2A_TOKEN=$(kubectl get secret ta-agent-a2a-token -n telemetry-analyst -o jsonpath='{.data.token}' | base64 -d)
export LANGFUSE_PUBLIC_KEY=$(kubectl get secret langfuse-credentials -n orchestrator -o jsonpath='{.data.LANGFUSE_PUBLIC_KEY}' | base64 -d)
export LANGFUSE_SECRET_KEY=$(kubectl get secret langfuse-credentials -n orchestrator -o jsonpath='{.data.LANGFUSE_SECRET_KEY}' | base64 -d)
# port-forward しないと cluster 内 Service には到達しないので、ローカルでは Langfuse / OTel は noop になることに留意

# Chainlit 起動
uv run chainlit run src/orchestrator/app.py -h --port 8000
# http://localhost:8000 をブラウザで開く
```

## Kubernetes 配備 (staging)

事前準備: 必要な Secret を `orchestrator` ネームスペースにコピー。

```bash
# 1) OCI Enterprise AI API key を default → orchestrator にコピー
kubectl get secret enterprise-ai-api-key -n default -o yaml \
  | sed 's/namespace: default/namespace: orchestrator/' \
  | grep -v 'resourceVersion\|uid\|creationTimestamp' \
  | kubectl apply -f -

# 2) ta-agent A2A Token を telemetry-analyst → orchestrator にコピー (名前を変える)
kubectl get secret ta-agent-a2a-token -n telemetry-analyst -o json \
  | jq '.metadata.namespace = "orchestrator" | .metadata.name = "ta-agent-a2a-token-orchestrator"
        | del(.metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp)' \
  | kubectl apply -f -

# 3) langfuse-credentials は既に orchestrator NS にある前提 (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)

# 適用
kubectl apply -k deploy/

# 確認
kubectl -n orchestrator rollout status deploy/orchestrator
kubectl -n orchestrator port-forward svc/orchestrator 8000:8000
# ブラウザで http://localhost:8000
```

## 観測の見方

- **Tempo / Grafana** で trace 検索: `service.name=orchestrator`
  - 階層: `chat.message` → `agent.run` → `tool.*` → `a2a.send_message` → `http.client`
- **Langfuse** (`https://langfuse.sogawa-yk.com`) で session ビュー
- **Loki** で `{service_name="orchestrator"}`
- (P2 以降) **Prometheus**:
  - `orchestrator_agent_calls_total`
  - `orchestrator_approval_total`
  - `orchestrator_tool_latency_ms`

## 進行フェーズ

- ✅ P1: ta-agent を Chainlit から呼べる MVP
- ⏳ P2: 承認フロー + observability の本格化
- ⏳ P3: Langfuse LLM-as-a-Judge による改善ループ (3 周)

## 関連リソース

- 実装計画: `/home/opc/.claude/plans/a2a-ai-ai-ui-kubernetes-pod-react-a2a-do-staged-cookie.md`
- A2A サーバ呼出 runbook: `docs/runbooks/a2a_client.md`
