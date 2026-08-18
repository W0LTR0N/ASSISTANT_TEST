#!/bin/bash
# Без set -e: коды выхода дочерних процессов обрабатываем вручную,
# иначе crash-loop воркера никогда не перезапустится.
# exit code 2 от sip_worker = фатальная конфигурация (PUBLIC_IP) — контейнер останавливается.

STOP=0
PID_UVICORN=0
PID_SIP=0

cleanup() {
    if [ "$STOP" -eq 1 ]; then
        return
    fi
    STOP=1
    echo "Завершение процессов..."
    kill -TERM "$PID_UVICORN" 2>/dev/null
    kill -TERM "$PID_SIP" 2>/dev/null
    wait "$PID_UVICORN" 2>/dev/null
    wait "$PID_SIP" 2>/dev/null
}

trap 'cleanup; exit 0' SIGTERM SIGINT

echo "Запуск FastAPI..."
uvicorn bot:app --host 0.0.0.0 --port 8000 &
PID_UVICORN=$!
sleep 2

run_sip_worker() {
    # Форвардим SIGTERM/SIGINT в python-ребёнка, чтобы воркер завершался
    # gracefully, а не оставался сиротой до убийства контейнера.
    trap 'kill -TERM "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null; exit 0' SIGTERM SIGINT
    while true; do
        echo "Запуск SIP/RTP Движка..."
        python sip_worker.py &
        CHILD=$!
        wait "$CHILD"
        EXIT_CODE=$?
        echo "SIP/RTP Движок завершился с кодом $EXIT_CODE."
        if [ "$EXIT_CODE" -eq 2 ]; then
            echo "FATAL: невалидная конфигурация (PUBLIC_IP не задан или 127.0.0.1). Остановка."
            exit 2
        fi
        sleep 3 &
        wait $! 2>/dev/null
        # При сигнале trap выше завершит субшелл; иначе — новый круг рестарта.
    done
}

run_sip_worker &
PID_SIP=$!

wait -n "$PID_UVICORN" "$PID_SIP"
EXIT_CODE=$?
cleanup

if [ "$EXIT_CODE" -ne 0 ]; then
    echo "Процесс умер с кодом $EXIT_CODE, завершаем контейнер для рестарта..."
    exit "$EXIT_CODE"
fi
exit 0
