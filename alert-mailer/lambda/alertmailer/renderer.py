"""
renderer.py - 通知メールのレンダリング

抽出済みフィールド辞書 + field_map.json + priority_map.json を組み合わせて
統一フォーマットの HTML メールとプレーンテキストメールを生成する。

デザイン仕様:
  - 2カラムテーブル（項目名 / 通知内容）
  - ヘッダ色: #0f1c50（紺）、行ヘッダ色: #6785c1（青灰）
  - 重要度セルには Critical/Warning/Info/Unknown の CSSクラスを適用
"""
import html as html_mod


def render(fields: dict, field_map: dict, priority_map: dict) -> tuple[str, str, str]:
    """
    フィールド辞書から件名・テキスト本文・HTML本文を生成する。

    Args:
        fields: 各ハンドラが抽出したフィールド辞書
        field_map: field_map.json（表示項目の定義）
        priority_map: priority_map.json（重要度→ラベル/CSSクラスの対応）

    Returns:
        (subject, body_text, body_html) のタプル
    """
    # 重要度キー（ALARM, OK 等）から表示ラベルとCSSクラスを解決
    priority_key = fields.get("priority", "")
    priority_info = priority_map.get(priority_key, {"label": "不明", "css_class": "Unknown"})
    fields["priority_label"] = priority_info["label"]
    fields["priority_css_class"] = priority_info["css_class"]

    # field_map.json の fields 定義順に行データを組み立て
    rows = []
    for f in field_map["fields"]:
        value = fields.get(f["key"], "-")
        rows.append({"label": f["label"], "value": value, "key": f["key"]})

    body_html = _build_html(rows, fields)
    body_text = _build_plain_text(rows, fields)
    subject = _build_subject(fields)

    return subject, body_text, body_html


def _build_subject(fields: dict) -> str:
    """メール件名を生成。形式: [重要度] プラグイン名 : 監視項目ID"""
    plugin = fields.get("plugin_name", "")
    monitor_id = fields.get("monitor_id", "")
    priority_label = fields.get("priority_label", "")
    return f"[{priority_label}] {plugin} : {monitor_id}"


def _build_plain_text(rows: list[dict], fields: dict) -> str:
    """プレーンテキスト版の本文を生成（「項目名: 値」形式の一覧）。"""
    lines = []
    for r in rows:
        lines.append(f"{r['label']}: {r['value']}")
    return "\n".join(lines)


def _build_html(rows: list[dict], fields: dict) -> str:
    """
    統一フォーマットの HTML テーブルを生成する。

    運用チームが見慣れた形式で監視メールを受け取れるようにする。
    """
    css_class = fields.get("priority_css_class", "Unknown")
    env_name = html_mod.escape(fields.get("env_name", ""))
    plugin_name = html_mod.escape(fields.get("plugin_name", ""))

    # 各行の HTML を生成（重要度セルには CSSクラスを付与）
    tbody_rows = ""
    for r in rows:
        value_escaped = html_mod.escape(str(r["value"]))
        if r["key"] == "priority_label":
            # 重要度セルのみ背景色付きで強調表示
            td = f'<td class="{html_mod.escape(css_class)}"><pre>{value_escaped}</pre></td>'
        else:
            td = f'<td><pre>{value_escaped}</pre></td>'
        tbody_rows += f"<tr><th>{html_mod.escape(r['label'])}</th>{td}</tr>\n"

    return f"""\
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<title>{env_name} {plugin_name} 通知</title>
<style>
table{{border-spacing:0;border:none}}
table thead tr th{{padding:.3em;border-bottom:1px solid #0f1c50;background-color:#0f1c50;color:#fff}}
table tbody tr th{{padding:.3em;border-bottom:1px solid #0f1c50;background-color:#6785c1;text-align:right;color:#fff}}
table tbody tr td{{padding:.3em;border-bottom:1px solid #0f1c50}}
td.Critical{{background-color:#bc4328;font-weight:700;font-size:large;color:#fff}}
td.Warning{{background-color:#e6b600;font-weight:700;font-size:large}}
td.Info{{background-color:#0080b1;font-weight:700;font-size:large;color:#fff}}
td.Unknown{{background-color:#bc4328;font-weight:700;font-size:large;color:#fff}}
</style>
</head>
<body>
<main><article>
<p>{env_name}にて以下のイベントが発生しました。<br>通知内容を確認の上、対応を行ってください。</p>
<table cellspacing="0">
<thead><tr><th>項目名</th><th>通知内容</th></tr></thead>
<tbody>
{tbody_rows}
</tbody>
</table>
</article></main>
</body>
</html>
"""
