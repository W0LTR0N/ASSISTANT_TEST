import os
import wave
import audioop
import logging
from typing import Optional
from google.protobuf.json_format import ParseDict
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc

logger = logging.getLogger("woltron")

# =====================================================================
# ЗАГРУЗКА И НАСТРОЙКА ФОНОВОГО АУДИОШУМА
# =====================================================================
BACKGROUND_FILE = "background_noise.wav"
BACKGROUND_PCM = b""

if os.path.exists(BACKGROUND_FILE):
    try:
        with wave.open(BACKGROUND_FILE, 'rb') as wf:
            BACKGROUND_PCM = wf.readframes(wf.getnframes())
            logger.info(f"Фоновый шум {BACKGROUND_FILE} успешно загружен ({len(BACKGROUND_PCM)} байт).")
    except Exception as e:
        logger.error(f"Ошибка чтения файла фонового шума {BACKGROUND_FILE}: {e}")
else:
    logger.warning(f"Файл фонового шума {BACKGROUND_FILE} не найден в корневом каталоге. Синтез будет выполнен без фона.")

def mix_background(speech_pcm: bytes, bg_pcm: bytes, bg_volume: float = 0.25) -> bytes:
    """
    Подмешивает фоновый шум к синтезированной речи для создания эффекта реального помещения.
    """
    if not bg_pcm or not speech_pcm:
        return speech_pcm

    try:
        # Корректируем громкость фонового шума
        adjusted_bg = audioop.mul(bg_pcm, 2, bg_volume)
       
        speech_len = len(speech_pcm)
        bg_len = len(adjusted_bg)
       
        # Подгоняем длину фонового шума под длину речи (зацикливание)
        if bg_len < speech_len:
            repeats = (speech_len // bg_len) + 1
            adjusted_bg = (adjusted_bg * repeats)[:speech_len]
        else:
            adjusted_bg = adjusted_bg[:speech_len]

        # Накладываем аудиопотоки друг на друга (16-bit PCM, sample_width=2)
        mixed_pcm = audioop.add(speech_pcm, adjusted_bg, 2)
        return mixed_pcm
    except Exception as e:
        logger.error(f"Ошибка при микшировании фонового шума: {e}")
        return speech_pcm

# =====================================================================
# ОСНОВНАЯ ФУНКЦИЯ СИНТЕЗА РЕЧИ (TTS v3 gRPC)
# =====================================================================
async def synthesize_speech(
    text: str,
    stub: tts_service_pb2_grpc.SynthesizerStub,
    folder_id: str
) -> bytes:
    """
    Синтезирует речь через Yandex SpeechKit v3 gRPC.
    Использует голос 'marat' с параметром model='page' для предотвращения PERMISSION_DENIED.
    """
    # Первичная очистка текста от спецсимволов и форматирования
    clean_text = text.replace('*', '').replace('#', '').replace('-', ' ').strip()
   
    if not clean_text:
        logger.warning("Получен пустой текст для синтеза речи.")
        return b""

    # Формируем JSON-запрос с указанием премиум-модели page для голоса marat
    request_dict = {
        "text": clean_text,
        "model": "page",
        "outputAudioSpec": {
            "rawAudio": {
                "audioEncoding": "LINEAR16_PCM",
                "sampleRateHertz": 8000
            }
        },
        "hints": [
            {"voice": "marat"}
        ]
    }

    try:
        # Преобразуем словарь в Protobuf-сообщение
        req = ParseDict(request_dict, tts_pb2.UtteranceSynthesisRequest())
        metadata = (('x-folder-id', folder_id),)

        speech_pcm = bytearray()

        # Асинхронно считываем чанки аудио от gRPC-сервера
        stream = stub.UtteranceSynthesis(req, metadata=metadata)
        async for response in stream:
            if response.HasField("audio_chunk"):
                speech_pcm.extend(response.audio_chunk.data)

        raw_speech = bytes(speech_pcm)

        if not raw_speech:
            logger.error("Yandex TTS вернул пустой аудиопоток.")
            return b""

        # Подмешиваем фоновый шум
        final_pcm = mix_background(raw_speech, BACKGROUND_PCM, bg_volume=0.25)
        return final_pcm

    except Exception as e:
        logger.error(f"Исключение TTS v3 gRPC: {e}")
        return b""
