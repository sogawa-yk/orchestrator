# Orchestrator: ユーザー対応待ちタスク

このドキュメントは **ユーザー (sogawa-yk) 側で実施が必要な作業** を 1 箇所にまとめたもの。
各項目の優先度・前提・手順・完了確認方法を記載する。

最終更新日: 2026-04-27

---

## 1. 【ブロッカー / 高】 Langfuse v3 の LLM Connector 設定 (OCI Enterprise AI 接続)

### 目的
P3 の **LLM-as-a-Judge 改善ループ (3 周)** を動かすために、Langfuse から OCI Enterprise AI に
チャット補完リクエストを送れる状態にする必要がある。

### なぜユーザー作業か
Langfuse v3 の LLM Connector は Langfuse Web UI から 1 度だけ設定するもので、
シークレット (API キー) を Langfuse に保存する操作のため、Orchestrator のコードからは管理しない。

### 前提
- Langfuse Web: `https://langfuse.sogawa-yk.com` にログインできること
- 利用するプロジェクトが選択されていること

### 手順
1. Langfuse Web にログイン
2. 左メニュー **Settings → LLM Connections** (もしくは Project Settings → LLM Connections) を開く
3. **Add new connection** を押し、以下を入力:
   - **Provider**: `openai` (OpenAI 互換のため)
   - **API key**: ta-agent が使っているのと同じ OCI Enterprise AI キー
     ```
     kubectl get secret oci-genai-key -n orchestrator -o jsonpath='{.data.api_key}' | base64 -d
     ```
   - **Base URL**: `https://inference.generativeai.ap-osaka-1.oci.oraclecloud.com/openai/v1`
   - **Custom models** (利用するモデル名を登録):
     - `openai.gpt-4.1`
     - 他に評価で試したいモデルがあれば追加 (例: `openai.gpt-4.1-mini`)
4. **Test connection** で 200 OK を確認
5. 保存

### 完了確認
完了したら以下を実行:

```bash
uv run python -m orchestrator.eval.runner --check-only
```

`langfuse_client: "ok"` と `dataset_exists: false` (まだ作っていないので) が返れば OK。
完了の旨を Claude (本セッション) に伝えれば、以下を順次実行する:

1. `uv run python -m orchestrator.eval.runner --upsert-dataset` (golden 10 件投入)
2. `uv run python -m orchestrator.eval.runner --iter 1` (1 周目)
3. low-score trace を分析し `prompts/system.ja.md` / `tool_descriptions.yaml` / `few_shots.yaml` を改善
4. `--iter 2`, `--iter 3` まで回す

---

## 2. 【中】 OCI Enterprise AI のプロジェクト OCID 整合性の判断

### 現状の不整合
- **ConfigMap `orchestrator-config`** の `OCI_GENAI_PROJECT`:
  `ocid1.generativeaiproject.oc1.ap-osaka-1.amaaaaaassl65iqa73g3pulzlb7rhkw4d2pns4i7es4srj3n6tpjdyxphpaq`
  (orchestrator 用として最初に指定されたプロジェクト OCID)
- **実際に使用している API キー** (`oci-genai-key.api_key` を `telemetry-analyst` から複製):
  ta-agent のプロジェクト
  `ocid1.generativeaiproject.oc1.ap-osaka-1.amaaaaaassl65iqak67q6dr5zu6jqoimgf54sylota5devqglzkkoenxznxa`
  に紐づいている

OCI の OpenAI 互換エンドポイントは Bearer Token のみで認証するため動作はするが、
ログ・課金・利用統計が ta-agent と同じプロジェクトに混ざる。

### 選択肢
| 選択 | 内容 | コスト |
|---|---|---|
| **A** (推奨) | orchestrator 用プロジェクトで API キーを発行し、`enterprise-ai-api-key` を更新 | 数分 |
| **B** | ConfigMap の `OCI_GENAI_PROJECT` を ta-agent と同じ OCID に揃える | 1 行修正 |
| **C** | このまま放置 (本番化前に再検討) | ゼロ |

### 手順 (A を選んだ場合)
1. OCI Console → Generative AI → orchestrator のプロジェクトに行き、API キーを発行
2. Secret 更新:
   ```bash
   kubectl create secret generic enterprise-ai-api-key \
     --from-literal=api-key="<新しいキー>" \
     -n default --dry-run=client -o yaml | kubectl apply -f -
   # orchestrator NS にも複製
   kubectl get secret enterprise-ai-api-key -n default -o json \
     | jq 'del(.metadata.namespace, .metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.managedFields, .metadata.ownerReferences) | .metadata.namespace = "orchestrator"' \
     | kubectl apply -f -
   ```
3. Deployment の `OPENAI_API_KEY` 参照先を `oci-genai-key.api_key` から
   `enterprise-ai-api-key.api-key` に戻す (`deploy/deployment.yaml`)
4. rollout
   ```bash
   kubectl rollout restart -n orchestrator deploy/orchestrator
   ```
5. Pod 内で疎通確認
   ```bash
   kubectl exec -n orchestrator deploy/orchestrator -- python -c "
   import os, httpx
   r = httpx.post(os.environ['OPENAI_BASE_URL']+'/chat/completions',
     headers={'Authorization': f'Bearer {os.environ[\"OPENAI_API_KEY\"]}'},
     json={'model':'openai.gpt-4.1','messages':[{'role':'user','content':'hi'}],'max_tokens':5},
     timeout=30)
   print(r.status_code, r.text[:120])
   "
   ```

### 完了確認
- 上記が `200` を返す
- ブラウザから `https://orchestrator.sogawa-yk.com` で会話が継続できる

---

## 3. 【低 / 未来】 副作用あり (承認必須) リモートエージェントを追加するとき

### 概要
将来 `incident-responder` 等の「rollout-restart」「scale」など副作用ありの A2A エージェントが
クラスタにデプロイされたら、orchestrator は承認 UI 込みで対応できる。コード側は実装済。

### ユーザー側でやること
1. **Secret コピー** (orchestrator NS への Bearer Token 複製)
   ```bash
   kubectl get secret <new-agent-token> -n <new-agent-ns> -o json \
     | jq 'del(.metadata.namespace, .metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.managedFields) | .metadata.namespace = "orchestrator" | .metadata.name = "<new-agent>-a2a-token"' \
     | kubectl apply -f -
   ```
2. **`deploy/configmap-orchestrator-agents.yaml`** に新規 agents エントリ追加
   ```yaml
   - id: incident-responder
     display_name: "Incident Responder (rollout 制御)"
     base_url: "http://ir.<ns>.svc:8080/a2a"
     auth: { kind: bearer, token_env: IR_AGENT_A2A_TOKEN }
     enabled: true
     tags: [incident-response, privileged]
     approval:
       default: required
       skills:
         rollout-restart: { requires_approval: true, require_reason_input: true }
   ```
3. **`deploy/deployment.yaml`** の env に追加
   ```yaml
   - name: IR_AGENT_A2A_TOKEN
     valueFrom:
       secretKeyRef: { name: <new-agent>-a2a-token, key: token }
   ```
4. **`deploy/networkpolicy.yaml`** の egress に新規 namespace 許可を追加
5. **`src/orchestrator/eval/dataset.py`** の `DEFERRED_PRIVILEGED_ITEMS` を
   `GOLDEN_ITEMS` に統合 (もしくは置換)
6. `kubectl apply -k deploy/` して rollout

完了後、Claude に「`<agent_id>` を追加した」と伝えれば、承認フローの実機検証
(Approve/Reject の UI 動作、メトリクス `orchestrator_approval_total` 確認) まで実施する。

---

## 4. 【参考】 spec-kit 関連の未コミット変更について

### 状況
リポジトリには pre-existing で以下の変更が未コミットで残っている:

```
M  .specify/memory/constitution.md
M  .specify/templates/*.md (5 ファイル)
D  AGENTS.md
D  docs/01_ORCH_*.md (3 ファイル)
D  specs/001-orch-monthly-inventory/*.md (8 ファイル)
```

これらは P1 開始前から working tree にあり、Claude は触っていない。
ユーザー指示で「spec-kit は完全に無視する」方針のため、Claude は P1〜P3 のコミットには
これらを含めなかった。

### 選択肢
| 選択 | 結果 |
|---|---|
| **A** (推奨) | `git add -A && git commit -m "chore: drop spec-kit artifacts"` で 1 コミットにまとめて消す |
| **B** | `git restore .specify/ AGENTS.md docs/01_*.md specs/` で元に戻す (将来 spec-kit を使う可能性を残す) |
| **C** | 放置 (working tree が dirty なまま) |

判断はユーザーに委ねる。

---

## 5. 【参考】 ブラウザでの動作確認チェックリスト

`https://orchestrator.sogawa-yk.com` で以下を試して問題なければ、現状の MVP は完成として
扱える。

- [ ] 起動メッセージに `Telemetry Analyst (ec-shop 障害診断)` が表示される
- [ ] 「ec-shop の Pod 一覧を見せて」 → 表形式で結果が返る
- [ ] 同一スレッドで「再起動回数だけ表で見せて」 → 前提を引き継いだ結果が返る (context_id 継続)
- [ ] 「存在しない notthere エージェントを呼んで」 → 利用不能の旨を返し `call_remote_agent` を呼ばない
- [ ] Grafana で `orchestrator_agent_calls_total` が増えている
- [ ] Tempo で `service.name=orchestrator` の trace が `chat.session` ルートで見える
- [ ] Loki で `{service_name="orchestrator"}` に JSON ログが出る
- [ ] Langfuse Sessions で chainlit thread_id が session として出る (LLM 呼出 trace 込み)

未通過項目があれば Claude に伝えれば原因調査・修正する。

---

## 連絡時の伝え方の例

- 「Langfuse Connector 設定済み」 → P3 改善ループ開始
- 「OCI プロジェクト整合性を A で対応した」 → 動作確認 + コミット
- 「`<agent_id>` を追加した」 → 承認フロー実機検証
- 「ブラウザ確認で X が NG」 → 該当箇所のログ・trace を見て修正
