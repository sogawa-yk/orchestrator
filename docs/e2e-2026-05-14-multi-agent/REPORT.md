# orchestrator マルチエージェント委譲改善 E2E レポート (2026-05-14)

## 1. 課題と目的

ユーザー (sogawa-yk) から「orchestrator が**複数エージェントを積極的に利用して問題解決をしようとしていない**」との報告。
3 つの A2A エージェント (`telemetry-analyst` / `iam-agent` / `ri-agent`) が登録されているにもかかわらず、横断依頼で一部しか呼ばれない事象を Playwright + Langfuse で再現・修正・確認した。

## 2. 原因 (Phase A 調査結果)

`src/orchestrator/agent/prompts/system.ja.md` の元プロンプトには 2 つの問題があった:

1. **役割定義の Kubernetes バイアス**: 冒頭が「あなたは Kubernetes クラスタ内に存在する専門エージェントを呼び分けるオーケストレータ」となっており、LLM が「クラスタ」「環境」など曖昧語を **Kubernetes 単一領域**に解釈しがち。
2. **能動的な分解指示の欠如**: ツールの存在と使い方は説明していたが、「複数領域にまたがる依頼は該当する全エージェントへ委譲せよ」という明示ルールがなかった。

副次的に temperature=0.2 (runtime.py:68) も保守的だが、本タスクは**プロンプトのみ修正**で対応。

## 3. 修正内容

### 3.1 配信方式

- 新規 ConfigMap `orchestrator-prompts` (`deploy/configmap-orchestrator-prompts.yaml`) に `system.ja.md` を格納し volumeMount で `/etc/orchestrator/prompts/system.ja.md` に配信
- `runtime.py::_load_system_prompt` を環境変数 `ORCH_PROMPT_PATH` 優先 (ファイル存在チェック + バンドル版フォールバック) に変更
- `deploy/deployment.yaml` に env `ORCH_PROMPT_PATH=/etc/orchestrator/prompts/system.ja.md` と volumeMount を追加
- `deploy/kustomization.yaml` に新 ConfigMap を登録
- 単体テスト `tests/unit/test_runtime_prompt.py` で外部優先 / 不在時フォールバック / バンドル版経路を検証 (43 件 PASS)

### 3.2 プロンプトの主な変更

| 項目 | Before | After |
|---|---|---|
| 役割定義 | 「Kubernetes クラスタ内に存在する専門エージェント」(K8s バイアス) | 「複数の専門領域 (Kubernetes 観測、IAM、OCI リソース) を担当するエージェント群を束ねるオーケストレータ」+「『クラスタ』『環境』も 3 領域すべてを指す」と明記 |
| 横断依頼ルール | なし | 冒頭に「【絶対遵守】横断依頼の取り扱い」セクション。横断キーワード列挙 + 全エージェント順次呼出を必須化 + 省略禁止 |
| 単一領域ドメインマッピング | なし | 単一領域キーワード→対応エージェント表 (telemetry/iam/ri) |
| 応答前セルフチェック | なし | ターン終了直前に「全エージェント呼出済か」mental check を必須化 |
| 失敗時挙動 | 既存ルール維持 | 「1 つが失敗しても残りを継続」を明示追加 |
| 応答スタイル | 既存 | 複数結果統合時はエージェント別見出し + 全体サマリを必須化 |

## 4. 検証結果 (Playwright + Langfuse)

### 4.1 Phase A: ベースライン (修正前)

| # | 入力 | Langfuse `agent.id` (call_remote_agent) | 期待 | 結果 |
|---|---|---|---|---|
| A1 | 「(1) ec-shop Pod、(2) コンパートメント、(3) IAM グループ」(明示3点列挙) | `telemetry-analyst`, `iam-agent`, `ri-agent` | 3 | ✅ 3/3 |
| A2 | 「うちのクラスタの現状を一通り把握したい」(曖昧横断) | `telemetry-analyst`, `ri-agent` | 3 | ❌ **2/3 (iam-agent 抜け落ち)** ← 報告再現 |

- 修正前トレース: `91729a25b65de84804b4ca67cdc423e1` (A2 - iam 抜け)
- スクリーンショット: `before/03-vague-only-2-agents.png`

### 4.2 Phase C: 修正後

| # | 入力 | Langfuse `agent.id` (call_remote_agent) | 期待 | 結果 |
|---|---|---|---|---|
| C1 | 「うちのクラスタの現状を一通り把握したい」 | `iam-agent`, `ri-agent`, `telemetry-analyst` | 3 | ✅ **3/3** |
| C2 (回帰) | 「ec-shop ネームスペースの Pod だけ見せて」 | `telemetry-analyst` | 1 | ✅ 1/1 (過剰呼出なし) |
| C3 (承認) | 「IAM に test-user-2026-05-14 を作成して」 | `iam-agent.create_user` で `request_user_approval` → 却下で `call_remote_agent` 実行されず | 承認 UI 表示 + 却下で停止 | ✅ |

- 修正後トレース:
  - C1: `6190d50787dfc56d306fdfd0c967ca50`
  - C2: `aabf67054d776d9c11965a23e62bbaed`
  - C3: `dd345ebf2fdd33e783cf4120242ccd89` (`approval.decision: rejected`)
- スクリーンショット:
  - `after/01-vague-3-agents-success.png` (3 領域分の見出しが応答に並ぶ)
  - `after/02-single-domain-only-telemetry.png` (telemetry-analyst のみ呼出)
  - `after/03-approval-ui.png` (Chainlit `AskAction` の承認 UI)

### 4.3 プロンプト調整の試行錯誤 (参考)

役割定義に Kubernetes バイアスが残ったまま「横断ルール」だけ強化した中間版では、`「クラスタの現状把握」→ ri+telemetry のみ` `「環境の健康状態チェック」→ telemetry のみ` と依然として IAM が抜け続けた。冒頭の役割定義を「3 領域を束ねるオーケストレータ」と書き直した瞬間に C1 で 3/3 を達成したことから、**LLM の解釈は最上位ロール文に強く引きずられる**ことが確認できた (改善ループ参考データとして記録)。

## 5. 変更ファイル一覧

| 種類 | パス |
|---|---|
| 修正 | `src/orchestrator/agent/prompts/system.ja.md` |
| 修正 | `src/orchestrator/agent/runtime.py` (`_load_system_prompt` を ORCH_PROMPT_PATH 優先に) |
| 修正 | `deploy/deployment.yaml` (env + volumeMount + volume 追加) |
| 修正 | `deploy/kustomization.yaml` (新 ConfigMap 登録) |
| 新規 | `deploy/configmap-orchestrator-prompts.yaml` |
| 新規 | `tests/unit/test_runtime_prompt.py` |
| 新規 | `docs/e2e-2026-05-14-multi-agent/REPORT.md` (本ファイル) + before/after スクリーンショット |

## 6. ロールバック手順

```bash
git revert <commit>             # コード/ConfigMap を戻す
kubectl apply -k deploy/         # ConfigMap を旧内容で上書き
kubectl -n orchestrator rollout restart deploy/orchestrator
```

緊急時は `deployment.yaml` の env から `ORCH_PROMPT_PATH` を削除するだけで、即座にイメージにバンドルされた旧プロンプトへ戻る。

## 7. 残課題・推奨フォローアップ

- 横断依頼で **telemetry-analyst の `diagnose-ec-shop` skill** が「Cannot add more than 20 items at a time」エラーで `remote_failed` を返す事象が継続。本修正の範囲外だが、ta-agent 側の入力制限を別チケットで調査が必要。
- ri-agent の同時実行で「OCI Generative AI への接続失敗」エラーが時折発生。同じく ri 側の負荷耐性確認推奨。
- 本修正は temperature を変えていない (0.2 のまま)。さらなる安定化が必要であれば temperature=0.3 + プロンプト維持で追加検証可能。
