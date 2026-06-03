"""Centralized configuration loaded from environment variables."""

import os


# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Gemini AI ---
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get(
    "GEMINI_MODEL", "gemini-3.1-flash-lite"
)
GEMINI_MAX_RPM: int = int(os.environ.get("GEMINI_MAX_RPM", "10"))

# --- Anomaly Detection Thresholds ---
Z_SCORE_THRESHOLD: float = float(os.environ.get("Z_SCORE_THRESHOLD", "2.0"))
FUNDING_THRESHOLD_ANNUALIZED: float = float(
    os.environ.get("FUNDING_THRESHOLD_ANNUALIZED", "100")
)

# --- Data Fetching ---
LOOKBACK_HOURS: int = int(os.environ.get("LOOKBACK_HOURS", "168"))  # 7 days
MIN_VOLUME_USD: float = float(os.environ.get("MIN_VOLUME_USD", "1000"))

# --- Rate Limiting ---
# Max number of historical candle fetches per exchange per run
MAX_CANDLE_FETCHES: int = int(os.environ.get("MAX_CANDLE_FETCHES", "50"))
# Delay between candle fetch requests (seconds)
CANDLE_FETCH_DELAY: float = float(os.environ.get("CANDLE_FETCH_DELAY", "0.15"))
