# ORCH（Orchestrator Agent）技術仕様書

- 文書版数: Draft v0.1
- 対象フェーズ: Phase 1 PoC
- 実装言語: Python
- UI: Chainlit
- A2A: HTTP ベース Task API
- 認証: Instance Principal（基本） / API Key（テナント権限操作時のみ）
- LLM 接続: LiteLLM on OKE
- データストア: Oracle Database（ORCH スキーマ、JSON 主体）
- 文書言語: 日本語

---

## 1. 目的

ORCH はマルチエージェントシステムの**単一会話入口**として動作し、ユーザー要求を解釈し、適切な下位エージェントへ委譲し、結果を統合し、必要に応じてユーザー承認を受け付ける。

Phase 1 PoC では、主シナリオを **月次のテナント整理** とし、以下の流れを実現する。

1. ユーザーが月次整理を依頼する
2. ORCH が RI に事実収集を依頼する
3. ORCH が CQ に評価を依頼する
4. ORCH が結果を要約し、上位 3〜5 件を提示する
5. 必要に応じて NF に通知ドラフト生成を依頼する
6. ユーザー承認後に NF へ送信を依頼する

---

## 2. スコープ

### 2.1 Phase 1 で実装する範囲

- チャット UI による会話入口
- テナント整理タスクの受付
- LLM によるプランニング
- RI / CQ / NF への task ベース呼び出し
- サブタスク結果の統合
- 上位 3〜5 件の要約生成
- ユーザー深掘り指示への応答
- 通知ドラフトの提示
- メール送信の承認受付
- セッション内コンテキスト保持
- 1 日程度の分析サマリキャッシュ
- ローカル単体テスト、ローカル結合テスト、Kubernetes 配備後の Playwright E2E 検証

### 2.2 Phase 1 では実装しない範囲

- IAM / ENV / TA / IP を含む複雑な自動ルーティング
- 長期メモリ / ユーザープロファイル保存
- 通知先メールアドレスの自動解決
- 完全自律の定期実行
- UI 上の高度な管理ダッシュボード
- マルチテナント SaaS 的な強い分離制御

---

## 3. ORCH の責務

### 3.1 主責務

- ユーザーとの対話を開始・継続する
- 会話文脈から意図と対象を抽出する
- LLM を用いて実行計画を立てる
- 下位エージェントにタスクを委譲する
- 下位エージェントの結果を統合する
- ユーザーにわかりやすい形で提示する
- 承認が必要なアクションを明示する
- ユーザー承認を受けて副作用操作を実行させる
- 実行履歴・タスク履歴を保持する

### 3.2 非責務

- OCI の詳細検索ロジックそのもの
- コスト評価アルゴリズムそのもの
- メール本文の最終送信処理そのもの
- IAM の変更系実行
- アプリケーション構成の深い解釈

---

## 4. 関連エージェント

### 4.1 RI
- テナント横断のリソース棚卸し
- リージョン / コンパートメント横断の配置把握
- タグや作成者候補の根拠収集
- リソース間の関係把握

### 4.2 CQ
- コスト観点の評価
- service limit / quota の評価
- 棚卸し候補度の評価
- 推奨アクション生成

### 4.3 NF
- 通知ドラフト生成
- メール送信

### 4.4 将来拡張
- IAM
- ENV
- TA
- IP

---

## 5. ユースケース

### 5.1 月次のテナント整理（主ユースケース）

#### 入力例
- 「今月のテナント整理をしたい」
- 「コスト増と limit 逼迫を見て、必要なら担当者に連絡したい」
- 「棚卸し候補を上位から見せて」

#### ORCH の期待動作
- 対象期間を補完または確認する
- RI へ棚卸しと根拠収集を依頼する
- CQ へ評価を依頼する
- 上位 3〜5 件を要約表示する
- 深掘り候補を提示する
- ユーザーが希望したら NF に通知案を作らせる
- ユーザー承認後に送信する

### 5.2 深掘り表示

#### 例
- 「この件を詳しく見せて」
- 「コスト起点で並べ替えて」
- 「このコンパートメントだけ見せて」
- 「この作成者候補に送るメールを作って」

#### 期待動作
- 既存の作業コンテキストを用いて再分析せずに応答できるものは再利用する
- 追加取得が必要な場合のみ下位エージェントを再度呼ぶ

---

## 6. 実行モデル

### 6.1 基本モデル
- リクエスト駆動
- 将来的には定期ジョブに拡張可能

### 6.2 呼び出しモデル
- A2A over HTTP
- 基本は同期呼び出し
- 長時間処理は task 作成後に polling

### 6.3 プランニング方式
- **LLM 主導**
- ただし実装上は以下をコード側で制限する
  - 呼び出し可能なエージェント種別
  - 呼び出し可能なスキル
  - 承認なしで実行可能な操作
  - 承認が必要な副作用操作

---

## 7. 状態遷移

ORCH の親タスクは以下の状態を持つ。

- `received`
- `planning`
- `dispatching`
- `waiting_subtasks`
- `waiting_user_approval`
- `aggregating`
- `completed`
- `failed`
- `cancelled`

### 7.1 遷移例

```text
received
  -> planning
  -> dispatching
  -> waiting_subtasks
  -> aggregating
  -> waiting_user_approval  (通知案がある場合)
  -> dispatching            (NF 送信)
  -> completed
```

### 7.2 異常系

```text
planning -> failed
waiting_subtasks -> failed
waiting_user_approval -> cancelled
dispatching -> failed
```

---

## 8. UI 要件（Chainlit）

### 8.1 必須 UI 機能
- チャット入力
- 対象テナント / 期間指定
- 要約結果表示
- 深掘り指示
- 通知先メールアドレス入力
- 通知ドラフト確認
- 承認 / キャンセル
- 実行履歴表示

### 8.2 UI 表示方針
- 初回は上位 3〜5 件の要約表示
- 「全件」「詳細」「コスト起点」「limit 起点」「棚卸し起点」で再表示できる
- 承認が必要な操作は明示的な確認 UI を出す

---

## 8.3 検証方針

- 開発完了前にローカル単体テストを実行し、主要ロジックの回帰を検知する
- ローカル結合テストで ORCH と下位エージェントの連携、状態遷移、DB 更新を確認する
- 検証対象ビルドは Kubernetes クラスタへ配備し、実運用に近い構成で確認する
- E2E テストは Playwright を用い、ブラウザからの操作で 20 シナリオ以上を実行する
- シナリオには初回対話、深掘り表示、承認、失敗時リトライ、通知フローを含める

---

## 9. 内部作業コンテキスト

ORCH はセッションごとに 1 つの作業コンテキストを保持する。

### 9.1 保持項目
- session_id
- user_id
- tenant_id
- analysis_period
- current_objective
- current_filters
- current_summary
- ranked_issues
- selected_issue_id
- draft_notifications
- pending_approval
- subtask_results
- created_at
- updated_at
- expires_at

### 9.2 保持方針
- セッション内で利用
- 長期個人メモリとしては扱わない
- 分析サマリは 1 日程度キャッシュ
- 承認待ちは明示完了またはキャンセルまで保持

---

## 10. データストア設計方針

### 10.1 主たる保存形式
ORCH は **JSON 主体** とする。

理由:
- 会話コンテキストが非正規で変動しやすい
- 実行計画やサブタスク結果がエージェントごとに異なる
- 承認待ち payload をそのまま保存しやすい
- Converged Database の JSON 機能を訴求しやすい

### 10.2 スキーマ構成（ORCH スキーマ）

#### A. `orch_sessions`
会話セッションの主情報

主要列:
- `session_id` (PK)
- `user_id`
- `tenant_id`
- `status`
- `created_at`
- `updated_at`
- `expires_at`

#### B. `orch_context_docs`
作業コンテキスト JSON

主要列:
- `context_id` (PK)
- `session_id` (FK)
- `context_type`
- `context_json` (JSON)
- `version_no`
- `created_at`
- `updated_at`

#### C. `orch_tasks`
親タスク情報

主要列:
- `task_id` (PK)
- `session_id`
- `task_type`
- `status`
- `current_phase`
- `started_at`
- `ended_at`
- `error_code`
- `error_message`

#### D. `orch_subtasks`
下位エージェント呼び出し履歴

主要列:
- `subtask_id` (PK)
- `parent_task_id`
- `target_agent`
- `skill_id`
- `request_json` (JSON)
- `response_json` (JSON)
- `status`
- `started_at`
- `ended_at`

#### E. `orch_approvals`
承認待ち管理

主要列:
- `approval_id` (PK)
- `session_id`
- `task_id`
- `approval_type`
- `approval_payload` (JSON)
- `status`
- `approved_by`
- `approved_at`
- `cancelled_at`

#### F. `orch_response_cache`
応答サマリキャッシュ

主要列:
- `cache_key` (PK)
- `tenant_id`
- `period_key`
- `summary_json` (JSON)
- `created_at`
- `expires_at`

### 10.3 索引方針
- `session_id`
- `task_id`
- `tenant_id + period_key`
- `status`
- `expires_at`

必要に応じて JSON path index を追加する。

---

## 11. A2A インターフェース

### 11.1 Agent Card
各エージェントは Agent Card を公開し、ORCH は起動時または定期的に取得・キャッシュする。

想定項目:
- agent_id
- name
- description
- base_url
- skills
- capabilities
- auth_mode
- health_status

### 11.2 Task API
- `POST /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/cancel`
- `GET /agent-card`
- `GET /health`

### 11.3 ORCH から下位エージェントへの要求例

#### RI への要求
```json
{
  "task_type": "tenant_inventory_analysis",
  "inputs": {
    "tenant_id": "ocid1.tenancy...",
    "period": {
      "from": "2026-03-01",
      "to": "2026-03-31"
    },
    "focus": ["inventory", "owner_candidates", "resource_relations"]
  }
}
```

#### CQ への要求
```json
{
  "task_type": "tenant_issue_evaluation",
  "inputs": {
    "tenant_id": "ocid1.tenancy...",
    "inventory_snapshot_ref": "ri-task-123",
    "focus": ["cost", "limits", "cleanup"]
  }
}
```

#### NF への要求
```json
{
  "task_type": "draft_notification",
  "inputs": {
    "tenant_id": "ocid1.tenancy...",
    "issue_id": "issue-001",
    "recipient_email": "user@example.com",
    "tone": "confirmation_with_suggestion"
  }
}
```

---

## 12. ORCH の応答方針

### 12.1 初回応答
- 全体サマリ
- 上位 3〜5 件
- 推奨する次アクション
- 深掘り候補の提示

### 12.2 表示ビュー
- issue view
- resource view
- owner view
- compartment view
- cost view
- limit view

### 12.3 デフォルト
- 問題単位で表示
- 配下に対象リソースを表示
- さらに作成者候補・推奨アクション・通知要否を添える

---

## 13. 承認モデル

### 13.1 Phase 1 の承認対象
- メール送信

### 13.2 承認不要
- RI / CQ の読み取り処理
- NF のドラフト生成
- サマリ表示
- 深掘り表示

### 13.3 承認時の UI
- 宛先メールアドレス
- 件名
- 本文
- 対象 issue
- 送信理由
- 送信 / キャンセル

---

## 14. 認証・認可

### 14.1 OCI 呼び出し
- 基本: Instance Principal
- 例外: テナント権限を要する操作は API Key

### 14.2 下位エージェント呼び出し
- A2A 呼び出し時にサービス間認証を付与
- PoC では簡素な共有認証情報または mTLS / bearer token を選択可能
- 本番想定では mTLS または署名付きトークンを推奨

### 14.3 UI アクセス
- PoC は簡易認証可
- 本番想定では SSO 連携を前提にする

---

## 15. LLM 利用方針

### 15.1 LiteLLM 経由
- すべての LLM 呼び出しは LiteLLM を経由する
- モデル切り替え、監査、再試行、レート制御を共通化する

### 15.2 ORCH における LLM 用途
- 意図解釈
- 計画生成
- 結果要約
- 表示形式変換
- 承認前メッセージ生成

### 15.3 ガードレール
- 実行可能なエージェントとスキルは allowlist
- 副作用操作は承認フラグ必須
- 出力 JSON はスキーマ検証する

---

## 16. 監視・ログ・監査

### 16.1 ログ
- ユーザー要求
- セッション ID
- タスク状態遷移
- サブタスク呼び出し
- 承認操作
- エラー情報

### 16.2 メトリクス
- タスク成功率
- タスク所要時間
- サブタスク失敗率
- 承認待ち件数
- キャッシュヒット率
- LLM 呼び出し回数 / レイテンシ

### 16.3 監査
- いつ誰が送信承認したか
- どの issue に対して誰へ送ったか
- どの下位エージェント結果に基づいて送ったか

---

## 17. エラーハンドリング

### 17.1 基本方針
- 一部失敗でも可能な限り部分結果を返す
- 主経路失敗時は「分析不完全」を明示する
- 承認前に失敗した通知送信は再送可能にする

### 17.2 パターン別
- **RI 失敗**: 棚卸し分析不完全として終了または再試行
- **CQ 失敗**: 事実一覧のみ返し、評価未完了を明示
- **NF ドラフト失敗**: メールドラフト作成失敗を明示
- **NF 送信失敗**: 送信失敗と下書きを提示
- **LLM 失敗**: ルールベースの簡易応答にフォールバック
- **DB 失敗**: セッション継続不可として新規実行を案内

---

## 18. API 例（ORCH 単体）

### 18.1 UI バックエンド API 例
- `POST /ui/chat`
- `POST /ui/analyze`
- `POST /ui/approval/{approval_id}/approve`
- `POST /ui/approval/{approval_id}/cancel`
- `GET /ui/session/{session_id}`
- `GET /ui/history`

### 18.2 内部レスポンス例
```json
{
  "session_id": "sess-001",
  "task_id": "task-001",
  "status": "waiting_user_approval",
  "summary": {
    "top_issues": [
      {
        "issue_id": "issue-001",
        "issue_type": "cost_spike",
        "priority": "high",
        "summary": "Compute リソースのコストが前月比で大きく増加"
      }
    ]
  },
  "next_actions": [
    "issue-001 を詳しく見る",
    "対象作成者候補へのメール文面を作る"
  ]
}
```

---

## 19. Converged Database 訴求ポイント（ORCH）

ORCH は Oracle Database の Converged Database 特性を、以下の観点で活用する。

- **JSON**: セッション、計画、サブタスク結果、承認 payload の保持
- **Relational**: 状態管理、索引、検索性、監査
- 将来的には **Vector** を用いて類似ケース検索や過去対応の再利用も可能

PoC では ORCH は **JSON 主体 + 関係表補助** の代表例として位置づける。

---

## 20. 実装上の補足

### 20.1 推奨モジュール構成
- `ui/`
- `api/`
- `planner/`
- `orchestrator/`
- `a2a_client/`
- `llm/`
- `db/`
- `auth/`
- `approval/`
- `observability/`

### 20.2 推奨ライブラリ例
- FastAPI
- Pydantic
- SQLAlchemy
- oracledb
- httpx
- Chainlit

---

## 21. 今後の拡張

- IAM / ENV / TA / IP の動的ルーティング
- 定期実行ジョブ
- scope-aware routing
- 通知先の自動解決
- 類似インシデント / 過去対応の検索
- より高度な承認ワークフロー
