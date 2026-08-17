import os
import re
import logging
import grpc
from pydub import AudioSegment
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc

logger = logging.getLogger(__name__)

IAM_TOKEN = os.getenv("YANDEX_IAM_TOKEN")
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

# --- ЗАГРУЗКА И ПОДГОТОВКА ФОНОВОГО ШУМА ---
BG_NOISE_PATH = "background_noise.wav"
BG_NOISE = None

if os.path.exists(BG_NOISE_PATH):
    try:
        # Загружаем шум и приглушаем на -28dB, чтобы звучало естественным фоном
        loaded_noise = AudioSegment.from_file(BG_NOISE_PATH)
        BG_NOISE = loaded_noise - 28
        logger.info("Фоновый шум успешно загружен.")
    except Exception as e:
        logger.warning(f"Не удалось загрузить фоновый шум: {e}")
else:
    logger.warning("Файл background_noise.wav не найден, синтез будет идти без фона.")

def mix_background_noise(raw_pcm_speech: bytes) -> bytes:
    """Накладывает тихий фоновый шум на сырой PCM-поток речи."""
    if not raw_pcm_speech or BG_NOISE is None:
        return raw_pcm_speech

    try:
        # Речь из Яндекса (24kHz, 16-bit, mono RAW PCM)
        speech = AudioSegment(
            data=raw_pcm_speech,
            sample_width=2,
            frame_rate=24000,
            channels=1
        )

        # Зацикливаем шум под точную длину сгенерированной фразы
        repeat_count = (len(speech) // len(BG_NOISE)) + 1
        noise_loop = (BG_NOISE * repeat_count)[:len(speech)]

        # Накладываем речь поверх фона
        mixed = speech.overlay(noise_loop)
        return mixed.raw_data
    except Exception as e:
        logger.error(f"Ошибка при микшировании фона: {e}")
        return raw_pcm_speech

def humanize_text_for_tts(text: str) -> str:
    """Очищает текст от символов и вставляет естественные паузы для TTS."""
    if not text:
        return ""
  
    # Убираем дефисы и спецсимволы, чтобы Яндекс не заикался
    text = re.sub(r'[\-\–\—]', ' ', text)
    text = re.sub(r'[*#_\"\'`:]', '', text)
  
    # Расставляем паузы по знакам препинания
    text = text.replace("? ", " <break time='350ms'/> ")
    text = text.replace(", ", " <break time='180ms'/> ")
    text = text.replace(". ", " <break time='300ms'/> ")
  
    return text.strip()

def split_text_into_chunks(text: str, max_chars: int = 200) -> list[str]:
    """Разбивает длинный текст на логические куски до 200 символов."""
    if len(text) <= max_chars:
        return [text]
  
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= max_chars:
            current_chunk = (current_chunk + " " + sentence).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence[:max_chars]

    if current_chunk:
        chunks.append(current_chunk)
      
    return chunks

def synthesize_speech_v3(text: str) -> bytes:
    """Синтезирует речь через Yandex SpeechKit v3 с фоновым шумом."""
    if not text or not text.strip():
        return b""

    humanized = humanize_text_for_tts(text)
    chunks = split_text_into_chunks(humanized, max_chars=200)
    full_audio = bytearray()

    try:
        cred = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel('tts.api.cloud.yandex.net:443', cred)
        stub = tts_service_pb2_grpc.SynthesizerStub(channel)

        for chunk in chunks:
            request = tts_pb2.UtteranceSynthesisRequest(
                text=chunk,
                output_audio_spec=tts_pb2.AudioFormatOptions(
                    container_audio=tts_pb2.ContainerAudio(
                        container_audio_type=tts_pb2.ContainerAudio.RAW
                    )
                ),
                hints=[
                    tts_pb2.Hints(voice="marat"),  # Голос Марата
                    tts_pb2.Hints(role="good"),   # Дружелюбный тон
                    tts_pb2.Hints(speed=0.98)    # Естественная скорость
                ],
                loudness_normalization_type=tts_pb2.UtteranceSynthesisRequest.LUFS
            )

            metadata = (
                ('authorization', f'Bearer {IAM_TOKEN}'),
                ('x-folder-id', FOLDER_ID)
            )

            response_stream = stub.UtteranceSynthesis(request, metadata=metadata)
          
            for response in response_stream:
                if response.HasField('audio_chunk'):
                    full_audio.extend(response.audio_chunk.data)

        # Склеиваем с фоновым шумом перед отдачей в поток
        return mix_background_noise(bytes(full_audio))

    except Exception as e:
        logger.error(f"Исключение TTS v3 gRPC: {e}")
        return b""

# Связываем имя функции с тем, что ждет sip_worker.py
synthesize_speech_yandex = synthesize_speech_v3
