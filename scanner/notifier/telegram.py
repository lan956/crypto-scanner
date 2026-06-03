"""Telegram Bot API client for sending alerts to a channel."""

import logging
import time
from typing import Any

import requests

from scanner.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096


def _send_raw(text: str, parse_mode: str = "Markdown") -> dict | None:
    """Send a single message via Telegram Bot API.

    Args:
        text: Message text (max 4096 chars)
        parse_mode: 'Markdown', 'MarkdownV2', or 'HTML'

    Returns:
        API response dict, or None on failure
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured")
        return None

    url = f"{API_URL}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()

            if resp.status_code == 429:
                retry_after = data.get("parameters", {}).get("retry_after", 5)
                logger.warning(
                    f"Telegram rate limited, retrying in {retry_after}s"
                )
                time.sleep(retry_after)
                continue

            if not data.get("ok"):
                error_desc = data.get("description", "Unknown error")
                logger.error(f"Telegram API error: {error_desc}")
                # If Markdown parsing fails, retry with plain text
                if "parse" in error_desc.lower() and parse_mode != "":
                    logger.info("Retrying without parse_mode...")
                    payload["parse_mode"] = ""
                    continue
                return None

            logger.info("Telegram message sent successfully")
            return data

        except requests.exceptions.Timeout:
            logger.warning(f"Telegram timeout (attempt {attempt + 1})")
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Telegram connection error: {e}")
            time.sleep(2 ** attempt)

    logger.error("Telegram API failed after all retries")
    return None


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message into chunks respecting the Telegram limit.

    Tries to split on newlines to avoid breaking formatting.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Find last newline within limit
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1 or split_pos < max_len // 2:
            split_pos = max_len

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


def _format_number(value: float) -> str:
    """Format large numbers with K/M/B suffixes."""
    abs_val = abs(value)
    if abs_val >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    elif abs_val >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif abs_val >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_alert(anomalies: list[Any], ai_analysis: str | None = None) -> str:
    """Format anomalies into a rich Telegram alert message with emoji.

    Args:
        anomalies: List of Anomaly objects
        ai_analysis: Optional AI analysis text

    Returns:
        Formatted Markdown message string
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🚨 *MARKET ANOMALY ALERT* — {now}\n"]

    # Group by anomaly type
    funding_anomalies = [a for a in anomalies if a.anomaly_type == "extreme_funding"]
    volume_anomalies = [a for a in anomalies if a.anomaly_type == "volume_spike"]
    oi_anomalies = [a for a in anomalies if a.anomaly_type == "oi_spike"]

    if funding_anomalies:
        lines.append("━━━ 💰 EXTREME FUNDING ━━━")
        for a in funding_anomalies[:10]:  # Limit per category
            d = a.details
            emoji = "🔴" if d.get("funding_rate", 0) > 0 else "🟢"
            lines.append(
                f"{emoji} *{a.exchange}* | `{a.symbol}`\n"
                f"   Funding: {d.get('funding_rate_pct', 'N/A')} "
                f"(annualized: {d.get('annualized_pct', 'N/A')})\n"
                f"   Vol 24h: {_format_number(d.get('volume_24h_usd', 0))} | "
                f"OI: {_format_number(d.get('open_interest_usd', 0))}"
            )
        lines.append("")

    if volume_anomalies:
        lines.append("━━━ 📊 VOLUME SPIKE ━━━")
        for a in volume_anomalies[:10]:
            d = a.details
            lines.append(
                f"📊 *{a.exchange}* | `{a.symbol}`\n"
                f"   Vol 24h: {_format_number(d.get('volume_24h_usd', 0))} "
                f"(z-score: {d.get('z_score', 'N/A')})\n"
                f"   Funding: {d.get('funding_rate', 0) * 100:.4f}% | "
                f"OI: {_format_number(d.get('open_interest_usd', 0))}"
            )
        lines.append("")

    if oi_anomalies:
        lines.append("━━━ 📈 OI SPIKE ━━━")
        for a in oi_anomalies[:10]:
            d = a.details
            lines.append(
                f"📈 *{a.exchange}* | `{a.symbol}`\n"
                f"   OI: {_format_number(d.get('open_interest_usd', 0))} "
                f"(z-score: {d.get('z_score', 'N/A')})\n"
                f"   Vol 24h: {_format_number(d.get('volume_24h_usd', 0))} | "
                f"Funding: {d.get('funding_rate', 0) * 100:.4f}%"
            )
        lines.append("")

    # Summary line
    total = len(anomalies)
    lines.append(
        f"📋 *Total*: {total} anomalies across "
        f"{len(set(a.exchange for a in anomalies))} exchanges, "
        f"{len(set(a.symbol for a in anomalies))} markets"
    )

    if ai_analysis:
        lines.append("\n━━━ 🤖 AI ANALYSIS ━━━")
        lines.append(ai_analysis)

    return "\n".join(lines)


def send_alert(anomalies: list[Any], ai_analysis: str | None = None) -> bool:
    """Format and send anomaly alert(s) to Telegram.

    Automatically splits long messages into multiple parts.

    Args:
        anomalies: List of Anomaly objects
        ai_analysis: Optional AI analysis text

    Returns:
        True if at least one message was sent successfully
    """
    if not anomalies:
        logger.info("No anomalies to report")
        return False

    message = format_alert(anomalies, ai_analysis)
    chunks = _split_message(message)

    success = False
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(1)  # Respect Telegram rate limits between chunks

        result = _send_raw(chunk)
        if result:
            success = True

    return success
