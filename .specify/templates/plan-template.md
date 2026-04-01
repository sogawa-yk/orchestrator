# 実装計画: [FEATURE]

**ブランチ**: `[###-feature-name]` | **作成日**: [DATE] | **仕様**: [link]  
**入力**: `/specs/[###-feature-name]/spec.md` の機能仕様

**注記**: このテンプレートは `/speckit.plan` コマンドで埋める。実行フローは
`.specify/templates/plan-template.md` を参照する。

## 要約

[feature spec から主要要求と技術方針を日本語で要約]

## 技術コンテキスト

<!--
  ACTION REQUIRED: このセクションはプロジェクト固有の技術情報で置き換えること。
  すべて日本語で記述し、曖昧な点は NEEDS CLARIFICATION と明示すること。
-->

**言語 / バージョン**: [例: Python 3.12 or NEEDS CLARIFICATION]  
**主要依存関係**: [例: FastAPI, Chainlit, SQLAlchemy or NEEDS CLARIFICATION]  
**ストレージ**: [例: Oracle Database, files, N/A]  
**ローカル単体テスト**: [例: pytest tests/unit]  
**ローカル結合テスト**: [例: pytest tests/integration]  
**Kubernetes 検証環境**: [例: OKE staging namespace]  
**E2E テスト**: [例: Playwright, 20 シナリオ以上]  
**対象プラットフォーム**: [例: Linux server on Kubernetes]  
**プロジェクト種別**: [例: web-service, agent backend, web-ui]  
**性能目標**: [領域固有の数値目標]  
**制約**: [例: p95 200ms 未満、外部 API 制限、承認必須操作]  
**想定スケール / 範囲**: [例: 1 tenancy, 10k resources, 50 UI flows]

## 憲章チェック

*ゲート: Phase 0 調査前に必須。Phase 1 設計後に再確認する。*

- [ ] すべての計画文書が日本語で記述されている
- [ ] 要件、ユーザーストーリー、実装、検証が追跡可能に対応付けられている
- [ ] ローカル単体テストとローカル結合テストの実行方法が定義されている
- [ ] Kubernetes 検証環境への配備手順が定義されている
- [ ] Playwright によるブラウザ E2E を 20 シナリオ以上実施する計画がある
- [ ] ログ、メトリクス、トレースなど観測性の追加・更新方針が定義されている

## プロジェクト構成

### ドキュメント（この機能）

```text
specs/[###-feature]/
├── plan.md              # このファイル (/speckit.plan の出力)
├── research.md          # Phase 0 の出力
├── data-model.md        # Phase 1 の出力
├── quickstart.md        # Phase 1 の出力
├── contracts/           # Phase 1 の出力
└── tasks.md             # Phase 2 の出力 (/speckit.tasks)
```

### ソースコード（リポジトリルート）
<!--
  ACTION REQUIRED: 下記は例示。実際の構成に置き換え、不要な選択肢は削除すること。
  納品する plan.md に Option 表記を残してはならない。
-->

```text
# 単一プロジェクト構成
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
├── unit/
└── e2e/

# Web アプリ構成
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

playwright/
└── specs/
```

**構成方針**: [採用した構成と、上記ツリーで具体化した実パスを日本語で説明]

## 複雑性トラッキング

> **憲章チェック違反を正当化する必要がある場合のみ記入**

| 逸脱項目 | 必要性 | より単純な代替案を採用しない理由 |
|----------|--------|----------------------------------|
| [例: 4 つ目のサービス追加] | [現在の必要性] | [既存 3 サービスでは不足する理由] |
