import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "mesync-dev-secret-key-2024")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
    MONGO_URI = os.environ.get(
        "MONGO_URI",
        "mongodb+srv://greensync:LljysdQhhLFxyG5t@cluster0.y31xe.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    )
    MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "MeSync")
    OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
    OPENAI_CHAT_MODEL = "gpt-4o-mini"
    OPENAI_WHISPER_MODEL = "whisper-1"
    OPENAI_TTS_MODEL = "tts-1"
    OPENAI_TTS_VOICE = "alloy"
    ELEVENLABS_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # --- Integrations ---
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000")

    SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    SPOTIFY_SCOPES = "user-top-read user-read-recently-played user-read-private"

    GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
    GITHUB_SCOPES = "read:user"

    GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
    GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
    GMAIL_SCOPES = (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/gmail.send "
        "https://www.googleapis.com/auth/userinfo.email"
    )

    # Node sidecar hosting whatsapp-web.js (see whatsapp-bridge/README.md)
    WHATSAPP_BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3011")