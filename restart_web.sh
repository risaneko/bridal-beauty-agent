#!/usr/bin/env bash
# adk web を「確実に」再起動する。
# 目的: 旧プロセスがポートを占有して再起動が空振りし、古いコードが応答し続ける事故を防ぐ。
#
# 使い方:
#   bash restart_web.sh            # 8000番で起動（--reload 付き）
#   PORT=8001 bash restart_web.sh  # ポート変更
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8000}"
PY=".venv/bin/adk"
LOG="/tmp/adk_web.log"

echo "▶ 既存の adk web を停止..."
pkill -f "adk web" 2>/dev/null || true
sleep 1
# ポートを掴んだままの残骸を強制終了
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "✘ port $PORT がまだ解放されていません。手動で確認してください。" >&2
  exit 1
fi
echo "✔ port $PORT 解放OK"

echo "▶ 起動（--reload 付き: 以後 .py 編集で自動リロード）..."
nohup "$PY" web --host 127.0.0.1 --port "$PORT" --reload > "$LOG" 2>&1 &
NEWPID=$!

# 起動待ち
for _ in $(seq 1 30); do
  if grep -qiE "Uvicorn running|Application startup complete" "$LOG" 2>/dev/null; then break; fi
  if grep -qiE "Address already in use|Traceback" "$LOG" 2>/dev/null; then
    echo "✘ 起動失敗。$LOG を確認してください。" >&2; tail -5 "$LOG" >&2; exit 1
  fi
  sleep 1
done

SERVING_PID="$(lsof -ti:"$PORT" 2>/dev/null | head -1 || true)"
if [ -z "$SERVING_PID" ]; then SERVING_PID="$NEWPID"; fi
echo "✔ 起動完了 pid=${SERVING_PID}（launcher=${NEWPID}）"
ps -o lstart= -p "$SERVING_PID" 2>/dev/null | sed 's/^/  起動時刻: /' || true
curl -sS -o /dev/null -w "  health: dev-ui -> %{http_code}\n" -m 5 "http://127.0.0.1:$PORT/dev-ui/" || true
echo "  URL: http://127.0.0.1:$PORT   (ログ: $LOG)"
