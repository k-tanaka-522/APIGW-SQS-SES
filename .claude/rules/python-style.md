# Python コーディング規約

## スタイル
- Python 3.12 の機能を活用（`type | None` 構文、ZoneInfo 等）
- インデント: スペース4つ
- 最大行長: 119文字
- 文字列: ダブルクォート優先（f-string も同様）

## 型ヒント
- 関数の引数と戻り値に型ヒントを付ける
- `dict | None` の union 構文を使用（`Optional` は使わない）
- 例: `def extract(event: dict, context) -> dict | None:`

## import 順序
1. 標準ライブラリ（`os`, `json`, `datetime` 等）
2. サードパーティ（`boto3`, `aws_xray_sdk` 等）
3. ローカル（`lambda_common.*`, `handlers.*` 等）
- 各グループ間は空行で区切る

## 命名規則
- 関数・変数: snake_case
- クラス: PascalCase
- 定数: UPPER_SNAKE_CASE
- プライベート関数: `_` プレフィックス（例: `_split`, `_build_xcp_html`）

## boto3 クライアント初期化
- モジュールレベルで初期化（Lambda のコールドスタート最適化）
- リージョンは環境変数から取得

```python
import boto3
import os

ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
```

## JSON 出力
- `ensure_ascii=False` を指定（日本語をエスケープしない）

## タイムゾーン
- 内部処理は UTC、表示は JST（`ZoneInfo("Asia/Tokyo")`）
- `format_jst()` ユーティリティを使用
