"""Gemini AI integration for market anomaly analysis.

Uses the Google Generative Language API (REST) with rate limit awareness.
Model: configurable, defaults to gemini-2.5-flash-lite-preview-06-17.
"""

import json
import logging
import time
from typing import Any

import requests

from scanner.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MAX_RPM

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# In-memory rate tracking for this run
_call_count = 0
_first_call_time: float | None = None


def _check_rate_limit() -> bool:
    """Check if we can make another Gemini API call within rate limits.

    Returns True if safe to proceed, False if rate limited.
    """
    global _call_count, _first_call_time

    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set, skipping AI analysis")
        return False

    now = time.time()

    # Reset counter if more than 60s since first call
    if _first_call_time is not None and (now - _first_call_time) > 60:
        _call_count = 0
        _first_call_time = None

    if _call_count >= GEMINI_MAX_RPM:
        logger.warning(
            f"Gemini rate limit reached ({_call_count}/{GEMINI_MAX_RPM} RPM). "
            "Skipping AI analysis."
        )
        return False

    return True


def _record_call() -> None:
    """Record a Gemini API call for rate tracking."""
    global _call_count, _first_call_time

    if _first_call_time is None:
        _first_call_time = time.time()
    _call_count += 1


def analyze_anomalies(anomalies: list[Any]) -> str | None:
    """Send detected anomalies to Gemini for AI analysis.

    Args:
        anomalies: List of Anomaly objects to analyze

    Returns:
        AI analysis text, or None if unavailable (rate limited, error, etc.)
    """
    if not _check_rate_limit():
        return None

    if not anomalies:
        return None

    # Build a compact summary for the prompt
    anomaly_summaries = []
    for a in anomalies[:20]:  # Limit to 20 anomalies to stay within token limits
        summary = {
            "exchange": a.exchange,
            "symbol": a.symbol,
            "type": a.anomaly_type,
            "severity": round(a.severity, 2),
        }
        summary.update(a.details)
        anomaly_summaries.append(summary)

    prompt = (
        "You are a crypto market analyst. Analyze these market anomalies detected "
        "across Binance Futures, Hyperliquid, and trade.xyz perpetual markets. "
        "Provide a brief, actionable summary highlighting:\n"
        "1. The most significant anomalies and what they might indicate\n"
        "2. Any correlated patterns across exchanges\n"
        "3. Potential trading implications (bullish/bearish signals)\n\n"
        "Keep your response under 500 characters for a Telegram alert.\n\n"
        f"Anomalies:\n{json.dumps(anomaly_summaries, indent=2)}"
    )

    url = f"{API_URL}/{GEMINI_MODEL}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            _record_call()
            resp = requests.post(url, headers=headers, json=body, timeout=30)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                logger.warning(
                    f"Gemini rate limited (429), retrying in {retry_after}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(retry_after)
                continue

            if resp.status_code != 200:
                logger.error(
                    f"Gemini API error {resp.status_code}: {resp.text[:200]}"
                )
                return None

            data = resp.json()

            # Extract text from response
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
                    # Truncate for Telegram
                    if len(text) > 900:
                        text = text[:897] + "..."
                    logger.info("Gemini analysis completed successfully")
                    return text

            logger.warning("Gemini returned empty response")
            return None

        except requests.exceptions.Timeout:
            logger.warning(f"Gemini request timeout (attempt {attempt + 1})")
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Gemini connection error: {e}")
            time.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"Unexpected Gemini error: {e}")
            return None

    logger.error("Gemini API failed after all retries")
    return None
