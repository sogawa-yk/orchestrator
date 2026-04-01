# データモデル: ORCH 月次棚卸し統括

## 1. 棚卸し依頼

### 概要
月次棚卸しの開始要求と、その会話中に確定した前提情報を表す。

### 主な属性

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| request_id | string | 必須 | 棚卸し依頼を一意に識別する ID |
| session_id | string | 必須 | 会話セッション ID |
| tenant_id | string | 任意 | 対象テナント識別子 |
| analysis_period_from | date | 必須 | 分析開始日 |
| analysis_period_to | date | 必須 | 分析終了日 |
| scope | string | 必須 | 対象スコープ |
| focus_areas | string[] | 必須 | cost、limit、inventory などの重点観点 |
| current_objective | string | 必須 | 現在の利用者目的 |
| status | enum | 必須 | draft、confirmed、dispatched、completed、failed |

### バリデーション

- `analysis_period_from` は `analysis_period_to` 以下でなければならない
- `focus_areas` は 1 件以上必要
- `scope` は Phase 1 では tenancy を既定値とする

### 状態遷移

`draft` -> `confirmed` -> `dispatched` -> `completed`  
`draft` -> `failed`  
`confirmed` -> `failed`

## 2. 要対応項目

### 概要
棚卸し結果と評価結果を統合し、優先順位付きで利用者へ提示する対応候補を表す。

### 主な属性

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| issue_id | string | 必須 | 対応項目 ID |
| session_id | string | 必須 | 会話セッション ID |
| issue_type | enum | 必須 | cost_spike、limit_near_exhaustion、cleanup_candidate など |
| priority_rank | integer | 必須 | 表示順序 |
| severity | enum | 必須 | high、medium、low |
| summary | string | 必須 | 利用者向け要約 |
| rationale | string | 必須 | 優先理由 |
| recommended_actions | string[] | 必須 | 次アクション候補 |
| resource_refs | string[] | 必須 | 関連リソース参照 |
| owner_candidates | string[] | 任意 | 連絡先候補 |
| source_task_refs | string[] | 必須 | 元になったサブタスク参照 |

### バリデーション

- `priority_rank` はセッション内で重複してはならない
- `summary` と `rationale` は利用者へ提示可能な日本語文であること
- `recommended_actions` は最低 1 件必要

## 3. 表示ビュー

### 概要
利用者が会話中に選択する表示切り口を表す。

### 主な属性

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| view_id | string | 必須 | 表示ビュー ID |
| session_id | string | 必須 | 会話セッション ID |
| view_type | enum | 必須 | issue、resource、owner、cost、limit、compartment |
| selected_filters | json | 任意 | 現在の絞り込み条件 |
| selected_issue_id | string | 任意 | 選択中の要対応項目 |
| rendered_at | datetime | 必須 | 最終描画時刻 |

### バリデーション

- `view_type` は定義済み列挙値のみ許可
- フィルタはセッションに紐づくデータのみを参照する

## 4. 通知案

### 概要
送信前の通知内容と承認対象情報を保持する。

### 主な属性

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| draft_id | string | 必須 | 通知案 ID |
| session_id | string | 必須 | 会話セッション ID |
| issue_id | string | 必須 | 対象要対応項目 |
| recipient_email | string | 必須 | 宛先メールアドレス |
| subject | string | 必須 | 件名 |
| body | string | 必須 | 本文 |
| tone | string | 任意 | 文体指定 |
| approval_status | enum | 必須 | pending、approved、rejected、deferred、failed |
| send_status | enum | 必須 | draft、queued、sent、failed、cancelled |

### バリデーション

- `recipient_email` は妥当なメール形式でなければならない
- `approval_status=approved` の場合のみ `send_status=queued` 以降へ遷移できる
- `body` は対象 issue の要約と理由を含む

### 状態遷移

`pending/draft` -> `approved/queued` -> `approved/sent`  
`pending/draft` -> `deferred/draft`  
`pending/draft` -> `rejected/cancelled`  
`approved/queued` -> `failed/failed`

## 5. 承認待ちアクション

### 概要
利用者の判断が必要な処理単位を管理する。

### 主な属性

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| approval_id | string | 必須 | 承認管理 ID |
| session_id | string | 必須 | 会話セッション ID |
| task_id | string | 必須 | 親タスク ID |
| approval_type | enum | 必須 | notification_send |
| approval_payload | json | 必須 | 宛先、件名、本文、対象 issue など |
| status | enum | 必須 | input_required、approved、rejected、deferred、cancelled |
| acted_by | string | 任意 | 判断者 |
| acted_at | datetime | 任意 | 判断時刻 |

### バリデーション

- `approval_payload` は UI 表示に必要な最低項目を含まなければならない
- `status=approved` の場合は `acted_at` が必須

## 6. セッション / タスク / サブタスク

### セッション

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| session_id | string | 必須 | 会話セッション ID |
| user_id | string | 任意 | 利用者 ID |
| tenant_id | string | 任意 | 対象テナント |
| status | enum | 必須 | active、waiting_subtasks、waiting_user_approval、completed、failed、cancelled |
| expires_at | datetime | 必須 | セッション期限 |

### 親タスク

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| task_id | string | 必須 | 親タスク ID |
| session_id | string | 必須 | 会話セッション ID |
| task_type | string | 必須 | monthly_inventory_orchestration など |
| status | enum | 必須 | received、planning、dispatching、waiting_subtasks、aggregating、waiting_user_approval、completed、failed、cancelled |
| current_phase | string | 必須 | 現在の処理段階 |

### サブタスク

| 属性 | 型 | 必須 | 説明 |
|------|----|------|------|
| subtask_id | string | 必須 | サブタスク ID |
| parent_task_id | string | 必須 | 親タスク ID |
| target_agent | enum | 必須 | RI、CQ、NF |
| request_payload | json | 必須 | 下位エージェントへの要求 |
| response_payload | json | 任意 | 下位エージェント応答 |
| status | enum | 必須 | queued、working、input_required、completed、failed |

## 7. 関係図

```text
棚卸し依頼 1 --- 1 セッション
セッション 1 --- N 親タスク
親タスク 1 --- N サブタスク
セッション 1 --- N 要対応項目
セッション 1 --- N 表示ビュー
要対応項目 1 --- 0..N 通知案
通知案 1 --- 0..1 承認待ちアクション
```
