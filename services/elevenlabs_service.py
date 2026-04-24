from elevenlabs.client import ElevenLabs
import logging

logger = logging.getLogger(__name__)

_client = None

def _get_client():
    global _client
    if _client is None:
        from config import Config
        if Config.ELEVENLABS_API_KEY:
            _client = ElevenLabs(api_key=Config.ELEVENLABS_API_KEY)
    return _client


def clone_voice(user_id: str, audio_file_path: str, voice_name: str = None) -> str:
    client = _get_client()
    if not client:
        raise Exception("ElevenLabs API key not configured")

    if voice_name is None:
        voice_name = f"user_{user_id}"

    with open(audio_file_path, "rb") as f:
        audio_data = f.read()

    logger.info(f"[11Labs] Clone request: name={voice_name}, audio_size={len(audio_data)}")

    result = client.voices.ivc.create(
        name=voice_name,
        files=[audio_data],
    )
    voice_id = result.voice_id
    logger.info(f"[11Labs] Clone success: voice_id={voice_id}")
    return voice_id


def wait_for_voice_ready(voice_id: str, timeout: int = 60) -> bool:
    client = _get_client()
    if not client:
        return False

    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            voice = client.voices.get(voice_id)
            status = voice.status
            logger.info(f"[11Labs] Voice {voice_id} status={status}")
            if status in ("producing", "created"):
                time.sleep(2)
                continue
            return True
        except Exception as e:
            logger.warning(f"[11Labs] Voice get error: {e}")
            time.sleep(2)
    logger.warning(f"[11Labs] wait_for_voice_ready timed out")
    return False


def generate_speech_11labs(
    text: str,
    voice_id: str,
    model: str = "eleven_v2",
    stability: float = 0.5,
    similarity_boost: float = 0.75,
) -> bytes:
    client = _get_client()
    if not client:
        raise Exception("ElevenLabs API key not configured")

    logger.info(f"[11Labs] TTS voice_id={voice_id}, model={model}, text_len={len(text)}")

    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=model,
    )
    full_audio = b"".join(audio)
    logger.info(f"[11Labs] TTS success, bytes={len(full_audio)}")
    return full_audio


def delete_voice(voice_id: str) -> bool:
    client = _get_client()
    if not client:
        return False

    try:
        client.voices.delete(voice_id=voice_id)
        logger.info(f"[11Labs] Deleted voice {voice_id}")
        return True
    except Exception as e:
        logger.warning(f"[11Labs] Delete failed: {e}")
        return False


def save_voice_id(user_id: str, voice_id: str) -> None:
    from services.database_service import users_col
    from bson import ObjectId
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"voice_id": voice_id}}
    )