# 実装計画: ORCH 月次棚卸し統括

**ブランチ**: `001-orch-monthly-inventory` | **作成日**: 2026-04-01 | **仕様**: [/home/opc/Github/orchestrator/specs/001-orch-monthly-inventory/spec.md](/home/opc/Github/orchestrator/specs/001-orch-monthly-inventory/spec.md)  
**入力**: `/specs/001-orch-monthly-inventory/spec.md` の機能仕様、および `docs/01_ORCH_技術仕様書.md`、`docs/実装フェーズ計画書.md`

**注記**: このテンプレートは `/speckit.plan` コマンドで埋める。実行フローは
`.specify/templates/plan-template.md` を参照する。

## 要約

ORCH の Phase 1 PoC として、月次のテナント整理依頼を会話で受け付け、
RI・CQ・NF と連携しながら、上位 3〜5 件の要対応項目を要約表示し、深掘り表示と
通知承認までを end-to-end で成立させる。実装は縦切り優先で進め、PoC 段階では
ORCH を入口としつつ、A2A over HTTP による下位エージェント呼び出し、JSON 主体の
状態保持、承認付き通知フロー、ローカル単体 / 結合テスト、検証環境配備後の
ブラウザ E2E 20 シナリオ以上を品質ゲートに含める。

## 技術コンテキスト

**言語 / バージョン**: Python 3.12  
**主要依存関係**: FastAPI、Chainlit、LiteLLM、Pydantic、oracledb  
**ストレージ**: Oracle Database（ORCH スキーマ、JSON 主体）  
**ローカル単体テスト**: `python -m pytest tests/unit`  
**ローカル結合テスト**: `python -m pytest tests/integration`  
**Kubernetes 検証環境**: OKE 上の staging namespace を想定  
**E2E テスト**: Playwright によるブラウザ E2E 20 シナリオ以上  
**対象プラットフォーム**: Linux 上で稼働する Kubernetes ワークロード  
**プロジェクト種別**: マルチエージェント web-service + chat-ui orchestrator  
**性能目標**: 初回要約は 10 秒以内、深掘り再表示は 5 秒以内を PoC 目標とする  
**制約**: 日本語文書必須、承認前の副作用操作禁止、A2A 呼び出しは allowlist 管理、
JSON スキーマ検証必須、E2E 20 シナリオ以上必須  
**想定スケール / 範囲**: Phase 1 PoC、1 tenancy 単位、月次分析 1 期間、同時 10 セッション程度

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Chainlit, LiteLLM, Pydantic, oracledb  
**Storage**: Oracle Database (JSON-centric)  
**Project Type**: web-service orchestrator

## 憲章チェック

*ゲート: Phase 0 調査前に必須。Phase 1 設計後に再確認する。*

- [x] すべての計画文書が日本語で記述されている
- [x] 要件、ユーザーストーリー、実装、検証が追跡可能に対応付けられている
- [x] ローカル単体テストとローカル結合テストの実行方法が定義されている
- [x] Kubernetes 検証環境への配備手順が定義されている
- [x] Playwright によるブラウザ E2E を 20 シナリオ以上実施する計画がある
- [x] ログ、メトリクス、トレースなど観測性の追加・更新方針が定義されている

**初回ゲート判定**: 違反なし。Phase 0 と Phase 1 の成果物で追跡可能性、検証計画、
観測性、Kubernetes 配備後 E2E の方針を具体化する。  
**設計後の再評価**: `research.md`、`data-model.md`、`contracts/`、`quickstart.md`
作成後に再確認し、全ゲートを維持している。

## プロジェクト構成

### ドキュメント（この機能）

```text
specs/001-orch-monthly-inventory/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── a2a-task-api.yaml
│   └── chat-approval-contract.md
└── tasks.md
```

### ソースコード（リポジトリルート）

```text
apps/
├── orch/
│   ├── api/
│   ├── ui/
│   ├── domain/
│   └── services/
├── ri/
├── cq/
└── nf/

packages/
└── common/

infra/
└── k8s/

tests/
├── unit/
├── integration/
└── contract/

playwright/
└── specs/
```

**構成方針**: ORCH、RI、CQ、NF をアプリ単位で分離しつつ、共通契約や補助ロジックは
`packages/common/` に集約する。ORCH の会話入口とオーケストレーションは `apps/orch/`
に置き、`infra/k8s/` で検証環境への配備を管理し、`tests/` と `playwright/` で
ローカル検証と配備後検証を分離する。

## 複雑性トラッキング

現時点で憲章違反を正当化する必要はない。複雑性の追加が必要になった場合のみ、
`tasks.md` 生成前に理由と代替案を明記する。
