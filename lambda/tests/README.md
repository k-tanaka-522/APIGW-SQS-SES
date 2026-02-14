# Lambda テスト

Lambda 関数と共通 Layer のユニットテスト。

## ディレクトリ構成

```
lambda/tests/
├── conftest.py              # 共通fixture（環境変数・テストイベント定義）
├── unit_alertmailer/        # alert-mailer 関数のテスト
│   ├── test_handler.py          # SQSアンラップ・ルーティング
│   ├── test_cloudwatch_alarm.py # CloudWatch Alarm 抽出
│   ├── test_cloudwatch_logs.py  # CloudWatch Logs 抽出
│   ├── test_ecs_task.py         # ECS Task State Change 抽出
│   ├── test_renderer.py        # HTML/テキスト生成
│   └── test_sender.py          # SES送信
└── unit_layer/              # 共通Layer（lambda_common）のテスト
    ├── test_decorator.py        # @lambda_bootstrap デコレータ
    └── test_logger.py           # ログ出力
```

## 実行方法

プロジェクトルート（`pyproject.toml` がある場所）で実行する。

```bash
# 全テスト実行
pytest lambda/tests/ -v

# 特定ディレクトリのみ
pytest lambda/tests/unit_alertmailer/ -v
pytest lambda/tests/unit_layer/ -v

# 特定ファイルのみ
pytest lambda/tests/unit_alertmailer/test_handler.py -v

# 特定テストクラス・メソッドのみ
pytest lambda/tests/unit_alertmailer/test_handler.py::TestClassify -v
pytest lambda/tests/unit_alertmailer/test_handler.py::TestClassify::test_cloudwatch_logs -v

# カバレッジ付き
pytest lambda/tests/ --cov=lambda --cov-report=term-missing
```

## 仕組み

- **pytest**: Python のテストフレームワーク。`test_` で始まるファイル・関数を自動検出する
- **conftest.py**: テスト共通の設定や fixture を定義するファイル。同階層以下のテストから自動的に参照される
- **fixture**: テストに必要なデータやオブジェクトを提供する仕組み。関数の引数に書くだけで自動注入される

```python
# conftest.py で定義した fixture
@pytest.fixture
def mock_context():
    ...

# テストで使う（引数名で自動注入）
def test_something(mock_context):
    handler(event, mock_context)
```

- **mock / patch**: 外部サービス（SES, CloudWatch 等）の呼び出しを偽のオブジェクトに差し替える。実際の AWS リソースは不要

## 前提条件

```bash
pip install pytest pytest-cov moto boto3 aws-xray-sdk
```
