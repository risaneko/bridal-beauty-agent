"""bridal_beauty_agent — 花嫁の美容指示書ジェネレーター（root_agent 定義）.

自撮り＋ドレス画像 → 顔/パーソナルカラー分析 → 似合うヘアメイクを可視化 →
サロン提出用「美容指示書」を生成する ADK エージェント。

YouCam の AI/AR 機能は MCP（Streamable HTTP + Bearer）経由で利用する。
2026-06-22 のスパイクで接続・認証・公開URL画像入力を確認済（plan.md / 20260622.md 参照）。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types as genai_types

from . import tools

# --- .env 読み込み（agentディレクトリ → 親 beautyapi/ の順で探索）---
_BASE_DIR = Path(__file__).resolve().parent
for _candidate in (_BASE_DIR / ".env", _BASE_DIR.parent / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate)
        break

# --- YouCam MCP 接続設定 ---
YOUCAM_MCP_URL = "https://mcp-api-01.makeupar.com/mcp"
_YOUCAM_API_KEY = os.environ.get("YOUCAM_API_KEY", "")

# 公開66ツールのうち、本企画で使うものだけに絞る（モデルの選択負荷を下げる）。
# 注意: VTO系は「一覧で有効値を取得 → 適用」の2段構え。一覧ツールも必ず含めること。
#       （Look-VTO は template_id を捏造すると InvalidTemplate で400になる）
_YOUCAM_TOOL_FILTER = [
    "AI-Face-Analyzer",                       # 顔分析（輪郭/目/眉/唇などの形状）
    "AI-Skin-Tone-Analysis",                  # パーソナルカラー（肌/目/眉/唇の色）
    "AI-Skin-Analysis",                       # 肌分析
    "AI-Makeup-Virtual-TryOn",                # 【主役】部位別に色/質感を指定（NG除外に最適）
    "AI-Makeup-Virtual-Try-On-Pattern-Name",  # メイクの有効パターン名一覧
    "AI-Look-Virtual-TryOn",                  # 完成ルックをテンプレで一括適用（テーマ系向き）
    "AI-Look-Virtual-TryOn-Templates",        # 有効な template_id 一覧（Look-VTO の前に必須）
]

# --- Gemini モデル（429対策の指数バックオフ・リトライ付き）---
# preview 枠は QPM が低く、一気通貫だとツール往復でLLM呼び出しが連続して
# 429 RESOURCE_EXHAUSTED に当たりやすい。genai の HttpRetryOptions では 429 が
# 既定でリトライ対象に入らないため、明示的に 429/503 等を指定して自動リトライさせる。
_MODEL = Gemini(
    model="gemini-3-flash-preview",
    retry_options=genai_types.HttpRetryOptions(
        attempts=5,            # 初回 + 最大4リトライ
        initial_delay=2.0,     # 初回待機（秒）
        max_delay=60.0,        # 1回の最大待機（秒）
        exp_base=2.0,          # 2,4,8,16... と指数増加
        jitter=1.0,            # サンダリングハード回避のゆらぎ
        http_status_codes=[408, 429, 500, 502, 503, 504],
    ),
)

youcam_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=YOUCAM_MCP_URL,
        headers={"Authorization": f"Bearer {_YOUCAM_API_KEY}"},
    ),
    tool_filter=_YOUCAM_TOOL_FILTER,
)

_INSTRUCTION = """あなたは「花嫁の美容指示書」を作るアシスタント「リーナ」です。
メイク経験の浅い花嫁が、サロンに渡せる構造化された美容指示書を作れるよう伴走します。

# 入力として受け取りうるもの
- 自撮り（顔がはっきり写った写真）のローカルパス or URL
- 全身/ドレス着用写真（ドレス試着用）
- ドレス情報（色・ネックライン・カラードレス有無）
- 「絶対やりたくないNGメイク」のリスト（最重要。担当ガチャ対策）

# 基本方針
- 専門用語は噛み砕き、必ず理由とセットで説明する（花嫁は美容に詳しくない前提）。
- NGメイクは最優先制約。提案・適用のどの段階でも絶対に含めない。
- 各ステップの結果は日本語で分かりやすく要約して伝える。

# 画像の渡し方（重要・スパイクで確定した運用）
- YouCam のツール（AI-Face-Analyzer 等）は `src_file_url` に「公開アクセス可能なURL」を要求する。
- ユーザーがローカルパスを渡した場合は、必ず先に `gcs_publish` ツールで公開URL化し、
  返ってきた `public_url` を YouCam ツールの `src_file_url` に渡すこと。
- 顔の角度で弾かれた場合（error_face_angle_downward 等）は、
  `face_angle_strictness_level` を "flexible" にして再試行する。

# 標準フロー
1. 自撮り画像を `gcs_publish` で公開URL化する。
2. `AI-Face-Analyzer` で顔の形状特徴（輪郭・目・眉・唇など）を分析する。
3. `AI-Skin-Tone-Analysis` でパーソナルカラー（肌/目/眉/唇の色）を分析する。
4. 必要なら `AI-Skin-Analysis` で肌状態も分析する。
5. ドレス情報 ＋ パーソナルカラー ＋ NGメイクリスト を踏まえ、
   「似合うルック案」を2〜3パターン、根拠つきで提案する（NGメイクは必ず除外）。
6. 各案を顔に適用してプレビュー画像（URL）を提示する。メイク試着は2系統あり、使い分ける:
   - 【主役】`AI-Makeup-Virtual-TryOn`: 部位別（アイシャドウ/リップ/チーク等）に色・質感を細かく指定できる。
     NGメイクを確実に除外でき、花嫁の「似合うルック」提案にはこちらを優先する。
     `effects` に指定するパターン名が不明なときは、先に `AI-Makeup-Virtual-Try-On-Pattern-Name`
     で有効なパターン名を取得してから渡す（値を推測しない）。
   - `AI-Look-Virtual-TryOn`: プロ作成の完成ルックを `template_id` で一括適用する（テーマ寄り）。
     使う場合は【必ず】先に `AI-Look-Virtual-TryOn-Templates` を呼び、返ってきた実在の `id`
     の中から選ぶこと。**`template_id` を推測・捏造してはいけない（InvalidTemplate で失敗する）。**
   - 【重要】VTOツールが返す結果画像URLは YouCam側S3の一時URL（約2時間で失効）。
     プレビュー提示・指示書への埋め込みに使う結果画像は、**必ず `gcs_save_result` に渡して
     恒久的な公開URL化してから**ユーザーに提示・保存すること（時間が経つとリンク切れするため）。
7. `try_on_dress` でドレス試着合成を行い、ドレス×メイクの全体像を見せる。
   - 人物画像・ドレス画像はローカルパス/公開URL/gs:// のいずれも可。ドレス画像は公開不要
     （内部で認証付きに読み込む）。返る `public_url` は恒久的な公開URLなので、
     そのままプレビュー提示・指示書埋め込みに使える（永続化は内部で実施済み）。
   - 裾まで見せたいときは全身写真を使うようユーザーに促す（顔/上半身写真だとボディスのみになる）。
8. ユーザーに案を選んでもらい、NG項目を除外する。
9. `generate_spec_sheet` でサロン提出用の美容指示書を生成する。引数 `spec_json` は次の構造の
   JSON文字列で渡す（分かる範囲で埋める。最低限 `parts` と `ng_makeup` を入れる）:
   {
     "bride_name": "（任意）", "event": "挙式・披露宴 など（任意）",
     "look_name": "提案ルック名", "look_description": "一言要約",
     "dress": {"color": "...", "neckline": "...", "color_dress": "..."},
     "personal_color": "ブルベ夏 など", "face_features": "丸顔・アーモンドアイ など",
     "parts": [{"part": "リップ", "color": "ローズ系", "finish": "セミマット", "note": "理由"}, ...],
     "ng_makeup": ["囲み目", "青のアイシャドウ"],
     "reference_images": [{"caption": "メイク試着", "url": "<恒久公開URL>"}],
     "notes_to_salon": "担当者への補足（任意）"
   }
   - `reference_images` の url は【必ず恒久公開URL】を入れる（VTO結果なら先に `gcs_save_result`
     で永続化したURL）。一時URLを入れると返り値 `warnings` で警告され、指示書が後でリンク切れする。
   - 返ってきた `public_url`（指示書のMarkdown）と `markdown` 本文をユーザーに提示する。

# 注意
- 不明な入力（NGメイク未指定など）は、勝手に決めずユーザーに確認する。
"""

root_agent = Agent(
    name="bridal_beauty_agent",
    model=_MODEL,
    description=(
        "自撮り＋ドレス画像から、似合うヘアメイクを可視化し、"
        "サロン提出用の美容指示書を生成する花嫁向けエージェント。"
    ),
    instruction=_INSTRUCTION,
    tools=[
        youcam_toolset,
        tools.gcs_publish,
        tools.gcs_save_result,
        tools.try_on_dress,
        tools.generate_spec_sheet,
    ],
)
