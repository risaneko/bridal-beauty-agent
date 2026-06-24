"""サロン提出用「美容指示書」の Markdown 生成（純粋関数・副作用なし）.

`generate_spec_sheet`（tools.py）から呼ばれる本体。GCS保存やネットワークI/Oは持たず、
構造化データ（dict）→ 日本語Markdown文字列への変換だけを担う（単体テストしやすくするため）。

必須3要素（plan.md:169）:
  ① 部位別の色/質感（表）
  ② NG欄（最優先制約として目立たせる）
  ③ 参考画像（恒久公開URLを埋め込む。一時URLは呼び出し側で弾く）
"""
from __future__ import annotations

# 部位別オーダー表に出す既定の並び順（入力に無い部位は出さない／追加部位は末尾に回す）。
_PART_ORDER = [
    "ベース",
    "アイブロウ",
    "アイシャドウ",
    "アイライン",
    "マスカラ・まつげ",
    "チーク",
    "リップ",
    "ハイライト・シェーディング",
]

# 参考画像URLが「一時URL（時間で失効する）」かを判定するためのシグネチャ。
# YouCam VTO結果は S3 署名付き（amazonaws / X-Amz-Expires）で約2hで失効するため、
# そのまま指示書に埋めるとリンク切れする → 呼び出し側で gcs_save_result 永続化を促す。
_TEMP_URL_MARKERS = ("amazonaws.com", "x-amz-", "makeupar.com")


def is_temporary_url(url: str) -> bool:
    """URLが一時URL（時間失効する）と疑わしいかを返す。"""
    u = (url or "").lower()
    return any(marker in u for marker in _TEMP_URL_MARKERS)


def _esc(text: object) -> str:
    """Markdownテーブルのセル用に最小限エスケープ（パイプと改行を無害化）。"""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _sorted_parts(parts: list[dict]) -> list[dict]:
    """既定の部位順に並べる。未知の部位は元の順序を保って末尾へ。"""
    def key(item: dict):
        name = str(item.get("part", "")).strip()
        return (_PART_ORDER.index(name) if name in _PART_ORDER else len(_PART_ORDER))
    return sorted(parts, key=key)


def build_markdown(spec: dict) -> str:
    """構造化データ（dict）→ サロン提出用の日本語Markdown指示書を生成する.

    欠損フィールドには寛容（ある情報だけで組む）。必須は「部位別の色/質感」と「NG欄」。

    想定する spec の形（すべて任意。無ければその節を省略）:
        {
          "bride_name": "リサ",
          "event": "挙式・披露宴",
          "look_name": "ナチュラル上品ルック",
          "look_description": "肌のツヤを生かした、引き算の上品メイク",
          "dress": {"color": "オフホワイト", "neckline": "ハートカット", "color_dress": "グリーン"},
          "personal_color": "ブルベ夏",
          "face_features": "丸顔・アーモンドアイ",
          "parts": [
            {"part": "リップ", "color": "ローズ系", "finish": "セミマット", "note": "青みでブルベに馴染む"},
            ...
          ],
          "ng_makeup": ["囲み目", "青のアイシャドウ"],
          "reference_images": [{"caption": "メイク試着", "url": "https://storage.googleapis.com/..."}],
          "notes_to_salon": "リハ前共有。色は写真優先でお願いします。"
        }
    """
    bride = _esc(spec.get("bride_name", "")).strip()
    event = _esc(spec.get("event", "")).strip()
    look_name = str(spec.get("look_name", "")).strip()
    look_desc = str(spec.get("look_description", "")).strip()
    dress = spec.get("dress") or {}
    personal_color = str(spec.get("personal_color", "")).strip()
    face_features = str(spec.get("face_features", "")).strip()
    parts = [p for p in (spec.get("parts") or []) if isinstance(p, dict)]
    ng = [str(x).strip() for x in (spec.get("ng_makeup") or []) if str(x).strip()]
    refs = [r for r in (spec.get("reference_images") or []) if isinstance(r, dict) and r.get("url")]
    notes = str(spec.get("notes_to_salon", "")).strip()

    lines: list[str] = []

    # --- 見出し ---
    title = "# 美容指示書（サロン提出用）"
    if bride:
        title += f"　— {bride} 様"
    lines.append(title)
    sub = " / ".join(x for x in (event, look_name) if x)
    if sub:
        lines.append(f"> {sub}")
    lines.append("")
    lines.append(
        "> この指示書は、メイクに詳しくない花嫁が「なりたいイメージ」と"
        "「**絶対に避けたいこと**」を担当者の方へ正確に共有するために作成しています。"
    )
    lines.append("")

    # --- 1. 仕上がりイメージ ---
    lines.append("## 1. 仕上がりイメージ")
    if look_name:
        lines.append(f"**{look_name}**")
    if look_desc:
        lines.append("")
        lines.append(look_desc)
    facts = []
    if dress:
        dparts = []
        if dress.get("color"):
            dparts.append(f"色 {_esc(dress['color'])}")
        if dress.get("neckline"):
            dparts.append(f"ネックライン {_esc(dress['neckline'])}")
        if dress.get("color_dress"):
            dparts.append(f"カラードレス {_esc(dress['color_dress'])}")
        if dparts:
            facts.append(f"- **ドレス**：{' / '.join(dparts)}")
    if personal_color:
        facts.append(f"- **パーソナルカラー**：{personal_color}")
    if face_features:
        facts.append(f"- **顔の特徴**：{face_features}")
    if facts:
        lines.append("")
        lines.extend(facts)
    lines.append("")

    # --- 2. 部位別オーダー（必須①）---
    lines.append("## 2. 部位別オーダー")
    if parts:
        lines.append("")
        lines.append("| 部位 | 色 | 質感 | 指定・理由 |")
        lines.append("|---|---|---|---|")
        for p in _sorted_parts(parts):
            lines.append(
                f"| {_esc(p.get('part'))} | {_esc(p.get('color'))} "
                f"| {_esc(p.get('finish'))} | {_esc(p.get('note'))} |"
            )
    else:
        lines.append("")
        lines.append("_（部位別の指定はまだありません）_")
    lines.append("")

    # --- 3. NG欄（必須②・最優先制約）---
    lines.append("## 3. ⛔ 絶対にやりたくないこと（NG）")
    lines.append("")
    lines.append("**この指示書で最も優先される項目です。以下は必ず避けてください。**")
    lines.append("")
    if ng:
        for item in ng:
            lines.append(f"- ⛔ {item}")
    else:
        lines.append("- _（NG指定なし。気になる点があれば当日担当者へお伝えください）_")
    lines.append("")

    # --- 4. 参考イメージ（必須③・恒久URL）---
    lines.append("## 4. 参考イメージ")
    if refs:
        for r in refs:
            url = str(r.get("url")).strip()
            cap = str(r.get("caption", "") or "参考イメージ").strip()
            lines.append("")
            lines.append(f"### {cap}")
            lines.append(f"![{cap}]({url})")
            lines.append("")
            lines.append(f"[画像を開く]({url})")
            if is_temporary_url(url):
                lines.append("")
                lines.append(
                    "> ⚠️ この画像URLは一時URLの可能性があります"
                    "（時間が経つと表示できなくなります）。"
                )
    else:
        lines.append("")
        lines.append("_（参考画像はまだありません）_")
    lines.append("")

    # --- 5. 担当者の方へ ---
    if notes:
        lines.append("## 5. 担当者の方へ")
        lines.append("")
        lines.append(notes)
        lines.append("")

    lines.append("---")
    lines.append("*この指示書は bridal_beauty_agent（YouCam API × Google Virtual Try-on × ADK）が生成しました。*")
    return "\n".join(lines).rstrip() + "\n"
