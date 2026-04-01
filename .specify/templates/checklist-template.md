# [CHECKLIST TYPE] チェックリスト: [FEATURE NAME]

**目的**: [このチェックリストが対象とする内容]
**作成日**: [DATE]
**対象機能**: [spec.md または関連文書へのリンク]

**注記**: このチェックリストは `/speckit.checklist` コマンドで生成する。記述は日本語を原則とし、
必要に応じてローカル単体テスト、ローカル結合テスト、Kubernetes 配備、Playwright E2E の
確認項目を含めること。

<!-- 
  ============================================================================
  IMPORTANT: The checklist items below are SAMPLE ITEMS for illustration only.
  
  The /speckit.checklist command MUST replace these with actual items based on:
  - User's specific checklist request
  - Feature requirements from spec.md
  - Technical context from plan.md
  - Implementation details from tasks.md
  
  DO NOT keep these sample items in the generated checklist file.
  ============================================================================
-->

## [Category 1]

- [ ] CHK001 明確な完了条件を持つ確認項目
- [ ] CHK002 ローカル単体テストに関する確認項目
- [ ] CHK003 ローカル結合テストに関する確認項目

## [Category 2]

- [ ] CHK004 Kubernetes 検証環境への配備確認
- [ ] CHK005 Playwright E2E 20 シナリオ以上の確認
- [ ] CHK006 文書の日本語整備に関する確認

## Notes

- 完了した項目は `[x]` で記録する
- コメントや所見は項目の直下に追記する
- 関連文書や実行結果への参照を残す
- 項目番号は追跡しやすいよう連番にする
