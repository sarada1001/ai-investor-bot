#!/usr/bin/env bash
# GPUサーバーのOllama(11434)をlocalhost:11434にトンネルするスクリプト
# 使い方: ./scripts/start_ollama_tunnel.sh [stop|status]

TUNNEL_HOST="ollama-tunnel"
LOCAL_PORT=11434
PIDFILE="/tmp/ollama_tunnel.pid"

is_running() {
    if [ -f "$PIDFILE" ]; then
        local pid
        pid=$(cat "$PIDFILE")
        kill -0 "$pid" 2>/dev/null && return 0
    fi
    pgrep -f "ssh.*${TUNNEL_HOST}" > /dev/null 2>&1
}

port_open() {
    curl -s --connect-timeout 2 http://localhost:${LOCAL_PORT}/api/tags > /dev/null 2>&1
}

start_tunnel() {
    if is_running; then
        echo "[ollama-tunnel] 既に起動中です"
        return 0
    fi

    echo "[ollama-tunnel] トンネルを起動中 (dmgpt → uema2lab-gpu:11434)..."
    ssh -fN "$TUNNEL_HOST" 2>/dev/null
    local exit_code=$?

    if [ $exit_code -ne 0 ]; then
        echo "[ollama-tunnel] SSH接続失敗 (exit: $exit_code)" >&2
        return 1
    fi

    pgrep -f "ssh.*${TUNNEL_HOST}" | tail -1 > "$PIDFILE"

    local i=0
    while [ $i -lt 15 ]; do
        port_open && break
        sleep 1
        i=$((i + 1))
    done

    if port_open; then
        echo "[ollama-tunnel] 起動完了 → localhost:${LOCAL_PORT} で Ollama に接続可能"
        curl -s http://localhost:${LOCAL_PORT}/api/tags | \
            python3 -c "import sys,json; d=json.load(sys.stdin); print('  モデル:', [m['name'] for m in d['models']])" 2>/dev/null
    else
        echo "[ollama-tunnel] 警告: トンネルは起動しましたがOllamaへの疎通が確認できません" >&2
    fi
}

stop_tunnel() {
    if is_running; then
        pkill -f "ssh.*${TUNNEL_HOST}" && echo "[ollama-tunnel] 停止しました"
        rm -f "$PIDFILE"
    else
        echo "[ollama-tunnel] 起動していません"
    fi
}

status_tunnel() {
    if is_running; then
        echo "[ollama-tunnel] 稼働中"
        port_open && echo "  Ollama: 応答OK" || echo "  Ollama: 応答なし（トンネルは生きているが疎通なし）"
    else
        echo "[ollama-tunnel] 停止中"
    fi
}

case "${1:-start}" in
    start)   start_tunnel ;;
    stop)    stop_tunnel ;;
    restart) stop_tunnel; sleep 1; start_tunnel ;;
    status)  status_tunnel ;;
    *)       echo "使い方: $0 [start|stop|restart|status]" ;;
esac
