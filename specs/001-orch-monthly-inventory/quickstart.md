# クイックスタート: ORCH 月次棚卸し統括

## 1. 目的

Phase 1 PoC の主要導線である「月次棚卸し依頼 → 要約表示 → 深掘り → 通知承認」を
ローカル環境と検証環境で確認するための手順を示す。

## 2. 前提

- Python 3.12 が利用できること
- Oracle Database、LiteLLM、RI、CQ、NF の接続先が検証用に用意されていること
- Kubernetes クラスタと対象 namespace へデプロイ可能であること
- ブラウザ E2E 実行環境が利用できること

## 3. ローカル検証

### 3.1 単体テスト

```bash
python -m pytest tests/unit
```

### 3.2 結合テスト

```bash
python -m pytest tests/integration
```

### 3.3 ローカル起動後の確認観点

1. 月次棚卸しの自然言語依頼を入力できる
2. 対象期間や重点観点の不足が補われる
3. 上位 3〜5 件の要対応項目が要約表示される
4. 深掘り指示で表示ビューを切り替えられる
5. 通知案作成、承認、保留のいずれも選択できる

## 4. Kubernetes 検証環境への配備

```bash
kubectl apply -f infra/k8s/
kubectl rollout status deployment/orch -n staging
```

### 配備後確認

1. ORCH のチャット UI が利用可能である
2. RI、CQ、NF との連携先が疎通できる
3. 監視ログでセッション ID、タスク状態、承認操作が追跡できる

## 5. ブラウザ E2E

```bash
npx playwright test
```

### 最低限含めるシナリオ群

- 月次棚卸し開始
- 対象期間補完
- 上位 3〜5 件の要約表示
- issue / resource / owner / compartment 切り替え
- 通知案作成
- 承認
- 保留
- 下位エージェント失敗時の部分応答

合計 20 シナリオ以上を維持し、結果を記録すること。

## 6. 完了条件

- ローカル単体テスト成功
- ローカル結合テスト成功
- Kubernetes 検証環境への配備成功
- ブラウザ E2E 20 シナリオ以上成功
