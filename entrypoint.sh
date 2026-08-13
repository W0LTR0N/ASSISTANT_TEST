#!/bin/bash
set -e

cleanup() {
    echo "Завершение процессов..."
    kill -TERM "$PID_UVICORN" "$PID_SIP" 2>/dev/null || true
    wait "$PID_UVICORN" "$PID_SIP" 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT

echo "Запуск FastAPI..."
uvicorn bot:app --host 0.0.0.0 --port 8000 &
PID_UVICORN=$!

sleep 2

echo "Запуск SIP/RTP Движка..."
python sip_worker.py &
PID_SIP=$!

wait -n "$PID_UVICORN" "$PID_SIP" || cleanup
