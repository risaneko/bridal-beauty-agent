# bridal_beauty_agent — 花嫁の美容指示書ジェネレーター

記事:
[「似合うメイク」を見て選べる花嫁の美容指示書ジェネレーターを爆速開発｜YouCam API × Virtual Try-on × ADK](https://zenn.dev/rrrrrrrrrrr/articles/b3a4d257487b82)
 Zennfes Spring 2026 応募記事のソースコードです。
 テーマ：[YouCam APIを活用した実装事例とアイデア（パーフェクト株式会社）](https://zenn.dev/contests/zennfes-spring-2026-perfect?tab=overview)


自撮り＋ドレス画像から、**似合うヘアメイクを可視化**し、サロンに提出できる構造化された
**「美容指示書」**を生成する単一 ADK エージェントです。

YouCam API（パーフェクト株式会社）の AI/AR 機能（顔分析・肌トーン・肌分析・メイク試着）と、
Google Cloud の Virtual Try-on（ドレス試着合成）、Gemini を組み合わせています。

Zenn コンテスト「YouCam API を活用した実装事例とアイデア」（`zennfes-spring-2026-perfect`）
への応募作品です（3日 MVP 前提）。

> **原体験**：メイク経験の浅い花嫁が、前撮り・挙式リハで「やりたいメイクが言語化できない／
> やりたくないメイク（NG）だけははっきりある」という困りごとを抱える。担当者ガチャ対策として、
> NG を最優先制約に据えた指示書を作れるよう伴走します。

---

## 何ができるか

1. 自撮り画像から顔の形状特徴（輪郭・目・眉・唇）とパーソナルカラー（肌/目/眉/唇の色）を分析
2. ドレス情報・パーソナルカラー・NG メイクを踏まえ、似合うルック案を根拠つきで提案
3. メイク試着（YouCam VTO）で各案を顔に適用してプレビュー
4. ドレス試着（Google Virtual Try-on）でドレス×メイクの全体像を合成
5. サロン提出用「美容指示書」（部位別オーダー表＋NG欄＋参考画像）を Markdown で生成

---

## アーキテクチャ

**単一エージェント＋複数ツールの逐次オーケストレーション**（マルチエージェントではありません）。
`bridal_beauty_agent/agent.py` の `root_agent` が、instruction の 9 ステップに沿って
Gemini が毎ターン「次にどのツールを呼ぶか」を判断する ReAct 型のツール往復です。

```
beautyapi/
├── bridal_beauty_agent/
│   ├── agent.py        # root_agent 定義（YouCam MCP toolset + 自前ツール + 429対策モデル）
│   ├── tools.py        # 自前ツール: gcs_publish / gcs_save_result / try_on_dress / generate_spec_sheet
│   ├── spec_sheet.py   # 美容指示書の Markdown 生成（純粋関数）
│   └── __init__.py
├── restart_web.sh      # adk web の確実な再起動スクリプト（--reload 付き）
├── requirements.txt
├── .env                # 認証情報（git 管理外）
├── plan.md / spec.md / blog.md   # 企画・実装メモ・技術記事ドラフト
└── CLAUDE.md           # 開発者・Claude Code 向けの詳細ガイド
```

### ツールの2系統

- **YouCam MCP toolset**（`agent.py`）— Streamable HTTP + Bearer 認証で
  `https://mcp-api-01.makeupar.com/mcp` に接続。公開 66 ツールのうち 7 つに `tool_filter` で絞る
  （顔分析・肌トーン・肌分析・メイク VTO・ルック VTO ＋各一覧）。
- **自前ツール**（`tools.py`）:

  | ツール | 役割 |
  |---|---|
  | `gcs_publish` | ローカル画像 → 公開 GCS URL（YouCam の `src_file_url` 用） |
  | `gcs_save_result` | YouCam の署名付き一時 URL（約2hで失効）→ 公開 GCS に永続化 |
  | `try_on_dress` | Google Virtual Try-on でドレス試着合成（結果は永続化済みの公開URL） |
  | `generate_spec_sheet` | サロン提出用「美容指示書」を Markdown 生成し公開URL化 |

### 設計上の要点

- **画像の入出力フロー**：YouCam の MCP ツールは画像を**公開URL**でやり取りする。
  入力（自撮り）は `gcs_publish` で公開URL化。生成結果は YouCam 側 S3 の
  **署名付き一時URL（`X-Amz-Expires=7200` ＝約2時間で失効）**で返るため、
  **必ず `gcs_save_result` で永続化してから**指示書に埋め込む。
- **リージョン分離**：エージェント本体の Gemini（`gemini-3-flash-preview`）は `location=global` 必須。
  Google Virtual Try-on（`virtual-try-on-001`, GA）は global 非対応のため、
  `try_on_dress` 内で独立した genai クライアントを立てる。
- **429（QPM超過）対策**：preview 枠は QPM が低く、ツール往復で LLM 呼び出しが連続して
  429 に当たりやすい。`agent.py` でモデルを `Gemini` オブジェクトにし、
  `http_status_codes=[408,429,500,502,503,504]` を明示した指数バックオフ
  （2→4→8→16秒, attempts=5）を付与している（**この設定を消すと再発する**）。

---

## セットアップ

### 1. 依存インストール（`.venv` 前提）

```bash
.venv/bin/pip install -r requirements.txt
```

主な依存: `google-adk>=1.8.0`, `google-genai`, `mcp>=1.8.0`, `requests`,
`google-cloud-storage`, `python-dotenv`。

### 2. 環境変数（`.env`）

`bridal_beauty_agent/.env` か親の `beautyapi/.env` に配置（どちらかを探索）。

| 変数 | 値の例 | 用途 |
|---|---|---|
| `YOUCAM_API_KEY` | （各自取得） | YouCam MCP の Bearer 認証 |
| `GOOGLE_CLOUD_PROJECT` | `your-project-name` | GCP プロジェクト |
| `GOOGLE_CLOUD_LOCATION` | `global` | エージェント本体の Gemini |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` | Vertex AI 経由で genai を使う |
| `VTO_LOCATION` | `us-central1` | Virtual Try-on 専用リージョン |

### 3. 認証（ADC）

`storage.Client()` も genai も ADC を参照します。`risa-publicbucket` への**書き込み権限を持つ
アカウント**で ADC を通してください（ズレていると Gemini は通るのに GCS 書き込みだけ 403 になります）。

```bash
gcloud auth application-default login
```

---

## 起動

```bash
# 開発サーバ起動（必ずこれを使う。--reload 付きでクリーン再起動）
bash restart_web.sh              # http://127.0.0.1:8000/dev-ui/
PORT=8001 bash restart_web.sh    # ポート変更
# ログは /tmp/adk_web.log

# CLI 実行
.venv/bin/adk run bridal_beauty_agent
```

> **`adk web` を直接叩かないこと。** 素の起動は旧プロセスがポートを占有して再起動が空振りし、
> 古いコード（429 リトライ設定が効かない旧 `agent.py`）が応答し続ける事故があります。
> `restart_web.sh` は `pkill` → ポート解放確認 → `--reload` 起動 → ヘルスチェックまで行います。

---

## 使い方（チャット例）

dev-ui のチャットに、画像の**参照をテキストで**書いて渡します
（adk web の画像添付ボタンは会話のインライン画像になるだけで、ツールの文字列引数には渡りません）。

```
自撮り: /Users/me/Desktop/selfie.jpg
全身写真: gs://risa-publicbucket/me_full.jpg
着たいドレス: https://example.com/dress.png
NGメイク: 囲み目, 濃い眉, つけまつ毛
パーソナルカラーと似合うヘアメイク、ベストな格好を教えて
```

画像引数は **公開URL / ローカルパス / `gs://` URI** のいずれも使えます
（`_load_image` が吸収）。ドレス画像は公開不要（`gs://` を認証付きで読み込み）。

---

## 開発メモ

このリポジトリの README・エージェント実装・設計ドキュメントは
**[Claude Code](https://claude.com/claude-code)（Anthropic）を用いて生成・整備**しています。
