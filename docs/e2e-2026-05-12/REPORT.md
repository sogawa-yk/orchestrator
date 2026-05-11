# resource-intelligence (ri_v10) A2A 統合 E2E レポート

**実施日**: 2026-05-12
**対応内容**: orchestrator から `resource-intelligence` namespace の ri_v10 を呼べるよう、
A2A v0.3 ↔ v1.0 ブリッジサービス `bridge-ri` を新規追加

## 背景

`/home/opc/Github/ri_v10` は **A2A v0.3** (`method: "message/send"`, `parts[].kind:"text"`,
`role:"user"/"agent"`) で稼働している。orchestrator の a2a-sdk 1.0 クライアントは
**A2A v1.0** (`method: "SendMessage"`) を送るため、両者は wire 互換性なし。

ri_v10 のソースおよび k8s manifests は触らない制約のもと、protocol 変換を担う
ブリッジサービスを新規に作成して挟む方針を採用。

## アーキテクチャ

```
[Chainlit UI]
    ↓ WebSocket
[orchestrator pod] (orchestrator NS)
    ↓ a2a-sdk v1.0 JSON-RPC SendMessage + Bearer (RI_BRIDGE_A2A_TOKEN)
[bridge-ri pod] (orchestrator NS, replicas=1)
    │   ├ server side: a2a-sdk v1.0 (DefaultRequestHandler + create_jsonrpc_routes)
    │   └ client side: httpx で手書き JSON-RPC v0.3
    ↓ POST /a2a {jsonrpc:"2.0", method:"message/send", params:{message:{parts:[{kind:"text",text:...}]}}}
[resource-intelligence pod] (resource-intelligence NS, **無変更**)
    ↓ google-adk + OCI
[OCI Resource Search / Audit / Tagging API]
```

## 実装

### 新規ファイル (orchestrator repo)

| パス | 役割 |
|---|---|
| `bridges/ri/pyproject.toml` | `a2a-sdk[http-server]>=1.0.2`, `protobuf>=6.33,<7` (SDK 互換), `httpx`, `starlette`, `uvicorn` |
| `bridges/ri/Dockerfile` | Python 3.11-slim + uv multi-stage, CMD `uvicorn bridge_ri.server:app` |
| `bridges/ri/src/bridge_ri/config.py` | env (`RI_UPSTREAM_URL`, `RI_BRIDGE_A2A_TOKEN`, `RI_UPSTREAM_TIMEOUT`) |
| `bridges/ri/src/bridge_ri/agent_card.py` | v1.0 形式 AgentCard (3 skill: resource-search / dependency-map / resource-creator) |
| `bridges/ri/src/bridge_ri/executor.py` | `RiBridgeExecutor` (`AgentExecutor` 継承)、httpx で v0.3 `message/send` を呼ぶ |
| `bridges/ri/src/bridge_ri/server.py` | Starlette app: `/` (SendMessage), `/.well-known/agent-card.json`, `/healthz` (無認証) + `BearerAuthMiddleware` |
| `bridges/ri/tests/test_executor.py` | respx で 7 ケース (正常 / v0.3 payload 形式検証 / JSON-RPC error / 5xx / timeout / 空入力 / 空応答) |
| `deploy/bridge-ri/{deployment,service,networkpolicy}.yaml` | k8s manifests |

### 変更 (orchestrator repo)

- `deploy/configmap-orchestrator-agents.yaml` — `ri-agent` エントリ追加 (base_url: `http://bridge-ri.orchestrator.svc:8080`)
- `deploy/networkpolicy.yaml` — orchestrator → bridge-ri (同 NS) への egress 追加
- `deploy/kustomization.yaml` — bridge-ri/{deployment,service,networkpolicy}.yaml を resources に追加

### Secret 更新 (kubectl で直接)

`orchestrator-a2a-tokens` Secret に `RI_BRIDGE_A2A_TOKEN` キーを追加 (既存
`TA_AGENT_A2A_TOKEN` / `IAM_AGENT_A2A_TOKEN` は維持)。orchestrator と bridge-ri が
同 Secret を `envFrom: secretRef` で取り込み、共有値で認証する。

### orchestrator コード変更

**ゼロ**。既存の `A2AClient` / `card_cache` が `agents.yaml` 追記分を読むだけで動作。

### ri_v10 の触ったもの

**ゼロ** (制約通り)。

## 解決した不具合

1. **a2a-sdk 1.0.2 と protobuf 7.x の互換性問題**: `'google._upb._message.FieldDescriptor' object has no attribute 'label'` エラー。
   - 原因: a2a-sdk が `FieldDescriptor.label` を参照するが protobuf 7.x で削除済
   - 対策: `bridges/ri/pyproject.toml` で `protobuf>=6.33,<7` に pin
   - 動作確認: 初回イメージ (`v0.1.0`) で 500 → pin 後イメージ (`v0.1.1`) で 200 OK

## 検証

### Unit テスト (`bridges/ri/tests/test_executor.py`)

```
7 passed in 0.19s
```

- 正常レスポンス → text part 結合
- v0.3 送信 payload の形式 (`method:"message/send"`, `parts[].kind:"text"`)
- JSON-RPC error / HTTP 5xx / timeout / 空入力 / 空応答 のいずれも適切なエラー message

### In-cluster smoke (orchestrator pod から bridge → ri_v10)

`A2AClient` で `SendMessage` 投入 → `state=completed` で実 OCI のコンパートメント一覧
(`iaas_team`, `Management-Agents`, `oci-tutorial` 等) が取得できることを確認。

### Playwright UI E2E

| ケース | 結果 | スクショ |
|---|---|---|
| **初期画面**: 3 エージェント (telemetry-analyst / iam-agent / **ri-agent**) が welcome に列挙 | ✅ | `01-initial-3-agents.png` |
| **ri-agent**: 「resource-intelligence で OCI テナンシのコンパートメント一覧を取得」 | ✅ 整形テーブルで tenancy-root + 9 件超のコンパートメントを表示 | `02-ri-compartments.png` |
| **iam-agent + ta-agent 非劣化**: 「iam-agent でグループ一覧、telemetry-analyst で ec-shop の Pod 状況」 | ✅ 1 ターンで両エージェントを並列呼出、3 グループ + 2 Pod 情報を整形応答 | `03-iam-and-ta-non-regression.png` |

## デプロイ

| 項目 | 値 |
|---|---|
| 初回 image | `kix.ocir.io/nr3c2r62ocsa/bridge-ri:0.1.0` (protobuf 互換性問題で 500) |
| 修正 image | `kix.ocir.io/nr3c2r62ocsa/bridge-ri:0.1.1` (protobuf<7 pin) |
| Pod | `bridge-ri-7698bfbb56-*`, 1/1 Running |
| NetworkPolicy | bridge-ri 専用 NP で「ingress: orchestrator pod のみ / egress: kube-system DNS + resource-intelligence:443」 |
| 既存 NP 更新 | `orchestrator-egress` に「同 NS 内 bridge-ri:8080」egress 追加 |

## 結論

| 項目 | 結果 |
|---|---|
| ri_v10 の **無変更** 制約 | ✅ ソース・k8s ともに touch していない |
| orchestrator から resource-intelligence を A2A 呼び出し | ✅ UI 経由で実 OCI データ取得まで完走 |
| 既存 iam-agent / ta-agent の非劣化 | ✅ 同セッションで並列呼出も成功 |
| Unit テスト | ✅ 7/7 pass |
| クラスタ反映 | ✅ image push + rollout 完了 |

## Out of Scope (本タスク非対応)

- data part / file part の翻訳 (今回 text のみ抽出)
- bridge-ri の HPA / 高可用化 (replicas=1)
- ri_v10 側に Bearer 認証導入
- ri_v10 が v1.0 に移行した際のブリッジ撤去
