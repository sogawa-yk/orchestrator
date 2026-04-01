# 調査結果: ORCH 月次棚卸し統括

## Decision: ORCH を Python 3.12 ベースの単一オーケストレータとして構築する

**Rationale**:
- 技術仕様書で実装言語が Python、UI が Chainlit、A2A が HTTP ベースと定義されている
- Phase 1 PoC では、複数エージェント全体を統括する ORCH の実装速度と保守性が重要である
- Python 3.12 は型ヒント、非同期処理、テスト資産の整備で PoC に十分な成熟度がある

**Alternatives considered**:
- Python 3.11: 十分有力だが、ここでは新規 PoC として 3.12 を採用する
- Node.js: UI や API の選択肢は広いが、既存技術仕様と整合しない

## Decision: ORCH の公開インターフェースは Chainlit UI と A2A Task API を併用する

**Rationale**:
- 利用者価値はチャット UI による自然言語対話で発生する
- 同時に、下位エージェント連携には A2A Task API の契約明確化が必要である
- UI と Task API を分離することで、利用者体験と内部連携の責務を整理できる

**Alternatives considered**:
- UI のみで構成する: 下位エージェントとの契約が曖昧になる
- API のみで構成する: Phase 1 PoC の利用者体験を示しにくい

## Decision: ORCH の状態管理は Oracle Database 上の JSON 主体モデルを採用する

**Rationale**:
- 技術仕様書で ORCH スキーマ、JSON 主体、Converged Database の活用方針が示されている
- 会話文脈、サブタスク応答、承認 payload は非正規で可変性が高い
- セッション、タスク、承認、キャッシュを分けつつ JSON で柔軟に保持できる

**Alternatives considered**:
- 完全なリレーショナル正規化: PoC 初期の変化に対して変更コストが高い
- ファイルベース保存: セッション管理、監査、期限管理に不向き

## Decision: ORCH のフェーズ 1 は同期中心、長時間処理は polling で扱う

**Rationale**:
- 技術仕様書に「基本は同期呼び出し、長時間処理は task 作成後に polling」とある
- Phase 1 は end-to-end 成立が最優先であり、複雑な非同期制御を先送りできる
- タスク状態遷移を明示しやすく、失敗時の部分応答にもつなげやすい

**Alternatives considered**:
- 全面イベント駆動: 将来的には有効だが PoC では過剰
- 完全同期のみ: 長時間処理に対して利用者への状態説明が難しい

## Decision: 検証戦略はローカル単体、ローカル結合、検証環境ブラウザ E2E の三層構成とする

**Rationale**:
- 憲章でローカル単体テスト、ローカル結合テスト、Kubernetes 配備後の
  Playwright E2E 20 シナリオ以上が必須と定義されている
- ORCH は UI、状態管理、下位エージェント連携、承認モデルが絡むため、
  単一レイヤの検証では回帰を防ぎきれない
- 受け入れ時の証跡として、各層の実行結果を追跡できる必要がある

**Alternatives considered**:
- 単体テスト中心: UI とエージェント間連携の不具合を取りこぼす
- E2E 中心: 失敗箇所の切り分けに時間がかかる

## Decision: Phase 1 のスコープは月次棚卸し、深掘り、通知承認に限定する

**Rationale**:
- 機能仕様書と実装フェーズ計画書が、Phase 1 を RI・CQ・NF との連携に限定している
- IAM、ENV、TA、IP を含めると契約、承認、UI が拡散し、PoC の焦点がぼやける
- 月次棚卸しの一気通貫を 먼저成立させる方が、後続拡張の基準線になる

**Alternatives considered**:
- IAM や ENV まで同時導入: Phase 1 の価値よりも統合負荷が増える
- 要約表示のみに縮小: 通知承認までの PoC 価値を示せない
