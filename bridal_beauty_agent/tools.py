"""bridal_beauty_agent の自前ツール群.

- gcs_publish        : ローカル画像 → 公開GCS URL（YouCam MCP の src_file_url 用）【Day1・実装済】
- gcs_save_result    : 一時URL（YouCam署名付きS3, 約2h失効）→ 公開GCS URL に永続化【Day1・実装済】
- try_on_dress       : Google Virtual Try-on でドレス試着合成【Day2・実装済（2026-06-23 実画像で検証）】
- generate_spec_sheet: サロン提出用「美容指示書」生成（Markdown→公開GCS恒久URL）【Day3・実装済】
"""
import os
import uuid
from pathlib import Path

# 公開バケット（plan: risa-publicbucket。スパイクで公開アクセス可を確認済）
PUBLIC_BUCKET = "risa-publicbucket"
_UPLOAD_PREFIX = "bridal_beauty"

# Virtual Try-on は global 非対応 → us-central1 専用。
# エージェント本体の Gemini は global を使うため、VTO だけリージョンを分離する。
VTO_LOCATION = os.environ.get("VTO_LOCATION", "us-central1")
GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "r-risa")


def gcs_publish(local_path: str, bucket_name: str = PUBLIC_BUCKET) -> dict:
    """ローカル画像を公開GCSバケットにアップロードし、公開URLを返す.

    YouCam の各MCPツールは `src_file_url` に「publicly accessible」なURLを要求するため、
    自撮り・全身写真などのローカル画像はまずこのツールで公開URL化してから渡す。

    Args:
        local_path: アップロードするローカル画像のパス（jpg/png, 10MB以下）。
        bucket_name: 公開バケット名。既定は risa-publicbucket。

    Returns:
        成功時: {"status": "success", "public_url": <URL>, "blob": <オブジェクト名>}
        失敗時: {"status": "error", "message": <理由>}
    """
    from google.cloud import storage

    p = Path(local_path)
    if not p.exists():
        return {"status": "error", "message": f"ファイルが見つかりません: {local_path}"}
    if p.stat().st_size > 10 * 1024 * 1024:
        return {"status": "error", "message": "画像が10MBを超えています（YouCam API制限）。"}

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob_name = f"{_UPLOAD_PREFIX}/{p.name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(p))
        # risa-publicbucket は公開設定済みのため、URLを組み立てるだけでアクセス可能。
        public_url = f"https://storage.googleapis.com/{bucket_name}/{blob_name}"
        return {"status": "success", "public_url": public_url, "blob": blob_name}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"アップロード失敗: {e}"}


def gcs_save_result(image_url: str, name: str = "") -> dict:
    """一時URLの画像をダウンロードし、公開GCSに保存して恒久的な公開URLを返す.

    YouCam のVTO系（AI-Makeup-Virtual-TryOn / AI-Look-Virtual-TryOn 等）の生成結果は、
    YouCam側S3の **署名付きURL（X-Amz-Expires=7200 ＝約2時間で失効）** として返る。
    指示書への画像埋め込みや後工程で使う結果画像は、必ずこのツールで永続化すること。

    Args:
        image_url: 保存したい画像のURL（YouCamの署名付きS3 URLなど）。
        name: 保存ファイル名（任意）。拡張子は Content-Type から自動付与する。

    Returns:
        成功時: {"status": "success", "public_url": <恒久URL>, "blob": <オブジェクト名>}
        失敗時: {"status": "error", "message": <理由>}
    """
    import requests
    from google.cloud import storage

    try:
        resp = requests.get(image_url, timeout=60)
        resp.raise_for_status()
        data = resp.content
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"画像取得失敗: {e}"}

    if len(data) > 10 * 1024 * 1024:
        return {"status": "error", "message": "画像が10MBを超えています。"}

    # S3一時URLは Content-Type を binary/octet-stream で返すことがあるため、
    # URLの拡張子も併用して image MIME を確定（インライン表示できるように）。
    content_type = resp.headers.get("Content-Type", "").lower()
    url_path = image_url.split("?", 1)[0].lower()
    is_png = "png" in content_type or url_path.endswith(".png")
    mime, ext = ("image/png", ".png") if is_png else ("image/jpeg", ".jpg")
    stem = (Path(name).stem if name else "") or uuid.uuid4().hex
    blob_name = f"{_UPLOAD_PREFIX}/results/{stem}{ext}"

    try:
        client = storage.Client()
        bucket = client.bucket(PUBLIC_BUCKET)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=mime)
        public_url = f"https://storage.googleapis.com/{PUBLIC_BUCKET}/{blob_name}"
        return {"status": "success", "public_url": public_url, "blob": blob_name}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"保存失敗: {e}"}


def _mime_from(name: str, content_type: str = "") -> str:
    """ファイル名 or Content-Type から image MIME を推定（png 以外は jpeg 扱い）。"""
    if content_type and "png" in content_type.lower():
        return "image/png"
    return "image/png" if name.lower().endswith(".png") else "image/jpeg"


def _load_image(ref: str):
    """画像参照（ローカルパス / gs:// URI / http(s) URL）を genai の Image に変換する。

    YouCam ツールが返す公開URL・`gcs_publish` の公開URL・gs:// URI・ローカルパスの
    いずれでもドレス試着に渡せるよう吸収する。

    Raises:
        FileNotFoundError / requests 例外 / GCS 例外（呼び出し側で握る）。
    """
    from google.genai.types import Image as GenaiImage

    # ローカルパス
    if not ref.startswith(("http://", "https://", "gs://")):
        p = Path(ref)
        if not p.exists():
            raise FileNotFoundError(f"画像が見つかりません: {ref}")
        return GenaiImage.from_file(location=str(p))

    # gs:// URI（公開でなくても読める）
    if ref.startswith("gs://"):
        from google.cloud import storage

        bucket_name, _, blob_path = ref[5:].partition("/")
        blob = storage.Client().bucket(bucket_name).blob(blob_path)
        data = blob.download_as_bytes()
        return GenaiImage(image_bytes=data, mime_type=_mime_from(blob_path, blob.content_type or ""))

    # http(s) 公開URL
    import requests

    resp = requests.get(ref, timeout=60)
    resp.raise_for_status()
    return GenaiImage(
        image_bytes=resp.content,
        mime_type=_mime_from(ref, resp.headers.get("Content-Type", "")),
    )


def try_on_dress(person_image_url: str, dress_image_url: str, number_of_images: int = 1) -> dict:
    """Google Virtual Try-on（virtual-try-on-001, GA）でドレス試着合成を行う.

    人物（全身/上半身）画像にドレス画像を着せ替えた合成画像を生成する。生成結果は
    公開バケット（risa-publicbucket）に保存し、恒久的な公開URLとして返すため、
    そのまま指示書への埋め込み・プレビュー提示に使える。

    VTO は global 非対応のため、`location=VTO_LOCATION`（既定 us-central1）の専用
    genai クライアントを使う（エージェント本体の Gemini=global とは独立）。

    Args:
        person_image_url: 人物画像の URL / gs:// URI / ローカルパス（jpg/png, 10MB以下）。
            顔アップではなく全身/上半身が望ましい。
        dress_image_url: ドレス画像の URL / gs:// URI / ローカルパス。
        number_of_images: 生成枚数（1〜4）。既定 1。

    Returns:
        成功時: {"status":"success", "public_url":<代表URL>, "public_urls":[...], "count":n}
        失敗時: {"status":"error", "message":<理由>}
    """
    from google import genai
    from google.cloud import storage
    from google.genai.types import ProductImage, RecontextImageConfig, RecontextImageSource

    # 1) 入力画像の読み込み
    try:
        person_img = _load_image(person_image_url)
        dress_img = _load_image(dress_image_url)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"入力画像の読み込み失敗: {e}"}

    # 2) Virtual Try-on 実行（us-central1 専用クライアント）
    try:
        client = genai.Client(vertexai=True, project=GCP_PROJECT, location=VTO_LOCATION)
        response = client.models.recontext_image(
            model="virtual-try-on-001",
            source=RecontextImageSource(
                person_image=person_img,
                product_images=[ProductImage(product_image=dress_img)],
            ),
            config=RecontextImageConfig(
                number_of_images=max(1, min(4, number_of_images)),
                # 本人写真の着せ替えを通すため成人を許可・セーフティは高リスクのみブロック
                person_generation="allow_adult",
                safety_filter_level="block_only_high",
            ),
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"Virtual Try-on 実行失敗: {e}"}

    generated = getattr(response, "generated_images", None) or []
    if not generated:
        return {
            "status": "error",
            "message": "生成画像が返りませんでした（セーフティ等でブロックの可能性）。",
        }

    # 3) 結果を公開バケットに保存して恒久URL化
    try:
        bucket = storage.Client().bucket(PUBLIC_BUCKET)
        urls = []
        for gi in generated:
            data = gi.image.image_bytes
            ct = getattr(gi.image, "mime_type", None) or "image/png"
            ext = ".png" if "png" in ct else ".jpg"
            blob_name = f"{_UPLOAD_PREFIX}/dress/{uuid.uuid4().hex}{ext}"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(data, content_type=ct)
            urls.append(f"https://storage.googleapis.com/{PUBLIC_BUCKET}/{blob_name}")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"結果保存失敗: {e}"}

    return {
        "status": "success",
        "public_url": urls[0],
        "public_urls": urls,
        "count": len(urls),
    }


def generate_spec_sheet(spec_json: str, make_pdf: bool = False) -> dict:
    """サロン提出用「美容指示書」を生成し、公開GCSに保存して恒久URLで返す.

    部位別の色/質感・NG欄・参考画像を含む日本語の構造化指示書を Markdown で出力する。
    生成物は `risa-publicbucket/bridal_beauty/spec_sheets/` に保存し、恒久公開URLを返すので、
    そのままユーザーに共有・印刷できる（PDF化が不要でも Markdown＋ブラウザ印刷で提出可）。

    参考画像URLが一時URL（YouCam の署名付きS3＝約2hで失効）だった場合は `warnings` に載せる。
    その場合は **先に `gcs_save_result` で恒久URL化してから** spec_json に入れ直すこと。

    Args:
        spec_json: 指示書の元になる構造化データ（JSON文字列）。期待する形は
            `spec_sheet.build_markdown` の docstring を参照。最低限 `parts`（部位別）と
            `ng_makeup`（NG欄）があると指示書らしくなる。
        make_pdf: True かつ weasyprint/markdown が導入済みのとき、PDFも生成して
            `pdf_url` を返す（best-effort。未導入なら warnings に記して Markdown のみ返す）。

    Returns:
        成功時: {"status":"success", "public_url":<Markdownの恒久URL>, "blob":...,
                 "markdown":<本文>, "warnings":[...], "pdf_url":<任意>}
        失敗時: {"status":"error", "message":<理由>}
    """
    import json

    from google.cloud import storage

    from . import spec_sheet

    # 1) 入力JSONをパース（壊れていても理由を返して落ちない）
    try:
        spec = json.loads(spec_json) if isinstance(spec_json, str) else dict(spec_json)
        if not isinstance(spec, dict):
            raise ValueError("spec_json はオブジェクト（dict）である必要があります。")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"spec_json の解析に失敗: {e}"}

    # 2) Markdown生成（純粋関数）
    markdown = spec_sheet.build_markdown(spec)

    # 3) 参考画像の一時URLガード（恒久URLでないと指示書が後でリンク切れする）
    warnings: list[str] = []
    for r in (spec.get("reference_images") or []):
        url = str((r or {}).get("url", ""))
        if url and spec_sheet.is_temporary_url(url):
            warnings.append(
                f"参考画像が一時URLの可能性: {url} → gcs_save_result で恒久URL化してから渡してください。"
            )

    # 4) 公開バケットへ保存（恒久URL化）
    stem = (Path(str(spec.get("bride_name", ""))).stem or "spec") or "spec"
    blob_name = f"{_UPLOAD_PREFIX}/spec_sheets/{stem}_{uuid.uuid4().hex[:8]}.md"
    try:
        client = storage.Client()
        bucket = client.bucket(PUBLIC_BUCKET)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(markdown.encode("utf-8"), content_type="text/markdown; charset=utf-8")
        public_url = f"https://storage.googleapis.com/{PUBLIC_BUCKET}/{blob_name}"
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"指示書の保存に失敗: {e}"}

    result = {
        "status": "success",
        "public_url": public_url,
        "blob": blob_name,
        "markdown": markdown,
        "warnings": warnings,
    }

    # 5) PDF化（任意・best-effort。依存が無ければ Markdown のみで成功扱い）
    if make_pdf:
        try:
            import markdown as md_lib  # type: ignore
            from weasyprint import HTML  # type: ignore

            html = md_lib.markdown(markdown, extensions=["tables"])
            pdf_bytes = HTML(string=html).write_pdf()
            pdf_blob_name = blob_name.rsplit(".", 1)[0] + ".pdf"
            pdf_blob = bucket.blob(pdf_blob_name)
            pdf_blob.upload_from_string(pdf_bytes, content_type="application/pdf")
            result["pdf_url"] = f"https://storage.googleapis.com/{PUBLIC_BUCKET}/{pdf_blob_name}"
        except Exception as e:  # noqa: BLE001
            warnings.append(
                f"PDF化はスキップ（Markdown＋ブラウザ印刷で提出可）: {e}"
            )

    return result
