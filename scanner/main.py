"""Main entry point for the funding rate / volume / OI scanner.

Orchestrates the full pipeline:
1. Fetch market data from Binance + Hyperliquid (including trade.xyz)
2. Fetch historical data for z-score computation
3. Detect anomalies (extreme funding, volume spikes, OI spikes)
4. Optionally run Gemini AI analysis
5. Send Telegram alert if anomalies found
"""

import logging
import sys
import time

from scanner import config
from scanner.exchanges import binance, hyperliquid
from scanner.analysis.detector import detect_all_anomalies
from scanner.ai.gemini import analyze_anomalies
from scanner.notifier.telegram import send_alert

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_all_markets() -> list[dict]:
    """Fetch market data from all exchanges."""
    all_markets = []

    # Binance Futures
    try:
        binance_markets = binance.fetch_all_market_data(
            min_volume_usd=config.MIN_VOLUME_USD
        )
        all_markets.extend(binance_markets)
        logger.info(f"Binance: {len(binance_markets)} markets loaded")
    except Exception as e:
        logger.error(f"Binance data fetch failed: {e}")

    # Small delay between exchanges
    time.sleep(0.5)

    # Hyperliquid (native + trade.xyz)
    try:
        hl_markets = hyperliquid.fetch_all_market_data(
            min_volume_usd=config.MIN_VOLUME_USD
        )
        all_markets.extend(hl_markets)
        hl_native = sum(1 for m in hl_markets if m["exchange"] == "Hyperliquid")
        hl_xyz = sum(1 for m in hl_markets if m["exchange"] == "trade.xyz")
        logger.info(
            f"Hyperliquid: {hl_native} native + {hl_xyz} trade.xyz markets loaded"
        )
    except Exception as e:
        logger.error(f"Hyperliquid data fetch failed: {e}")

    logger.info(f"Total markets loaded: {len(all_markets)}")
    return all_markets


def fetch_historical_data(
    markets: list[dict],
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Fetch historical volume and OI data for z-score computation.

    Only fetches for a limited number of markets to respect rate limits.
    Prioritizes markets with higher volume for historical data fetching.

    Returns:
        Tuple of (historical_volumes, historical_oi) dicts
        Keys are 'exchange:symbol', values are lists of hourly values
    """
    historical_volumes: dict[str, list[float]] = {}
    historical_oi: dict[str, list[float]] = {}

    # Sort markets by volume (highest first) to prioritize important ones
    sorted_markets = sorted(
        markets, key=lambda m: m.get("volume_24h_usd", 0), reverse=True
    )

    fetch_count = 0
    max_fetches = config.MAX_CANDLE_FETCHES

    for m in sorted_markets:
        if fetch_count >= max_fetches:
            logger.info(
                f"Reached candle fetch limit ({max_fetches}), stopping historical fetches"
            )
            break

        key = f"{m['exchange']}:{m['symbol']}"
        exchange = m["exchange"]
        symbol = m["symbol"]

        try:
            if exchange == "Binance":
                vols = binance.fetch_historical_volumes(
                    symbol, config.LOOKBACK_HOURS
                )
                oi_hist = binance.fetch_historical_oi_changes(
                    symbol, config.LOOKBACK_HOURS
                )
            else:
                # Hyperliquid and trade.xyz both use the HL API
                coin = m["coin"]
                vols = hyperliquid.fetch_historical_volumes(
                    coin, config.LOOKBACK_HOURS
                )
                oi_hist = hyperliquid.fetch_historical_oi(
                    coin, config.LOOKBACK_HOURS
                )

            if vols:
                historical_volumes[key] = vols
            if oi_hist:
                historical_oi[key] = oi_hist

            fetch_count += 1

            # Courtesy delay between candle requests
            time.sleep(config.CANDLE_FETCH_DELAY)

        except Exception as e:
            logger.warning(f"Failed to fetch history for {key}: {e}")

    logger.info(
        f"Historical data: {len(historical_volumes)} volume series, "
        f"{len(historical_oi)} OI series"
    )
    return historical_volumes, historical_oi


def run_scan() -> None:
    """Execute the full scan pipeline."""
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("Starting market anomaly scan")
    logger.info(
        f"Config: z_threshold={config.Z_SCORE_THRESHOLD}, "
        f"funding_threshold={config.FUNDING_THRESHOLD_ANNUALIZED}%, "
        f"min_volume=${config.MIN_VOLUME_USD}, "
        f"lookback={config.LOOKBACK_HOURS}h"
    )
    logger.info("=" * 60)

    # Step 1: Fetch current market data
    markets = fetch_all_markets()
    if not markets:
        logger.warning("No market data available, aborting scan")
        return

    # Step 2: Fetch historical data for z-score computation
    hist_volumes, hist_oi = fetch_historical_data(markets)

    # Step 3: Detect anomalies
    anomalies = detect_all_anomalies(
        markets=markets,
        historical_volumes=hist_volumes,
        historical_oi=hist_oi,
        funding_threshold=config.FUNDING_THRESHOLD_ANNUALIZED,
        z_threshold=config.Z_SCORE_THRESHOLD,
    )

    if not anomalies:
        elapsed = time.time() - start_time
        logger.info(f"No anomalies detected. Scan completed in {elapsed:.1f}s")
        return

    logger.info(f"Detected {len(anomalies)} anomalies")

    # Step 4: AI analysis (if configured and within rate limits)
    ai_text = None
    if config.GEMINI_API_KEY:
        logger.info("Running Gemini AI analysis...")
        ai_text = analyze_anomalies(anomalies)
        if ai_text:
            logger.info("AI analysis completed")
        else:
            logger.info("AI analysis skipped or failed")

    # Step 5: Send Telegram alert
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        success = send_alert(anomalies, ai_text)
        if success:
            logger.info("Telegram alert sent successfully")
        else:
            logger.error("Failed to send Telegram alert")
    else:
        logger.warning("Telegram not configured, printing alert to stdout")
        from scanner.notifier.telegram import format_alert

        print(format_alert(anomalies, ai_text))

    elapsed = time.time() - start_time
    logger.info(f"Scan completed in {elapsed:.1f}s")


def main() -> None:
    """Entry point with error handling."""
    try:
        run_scan()
    except KeyboardInterrupt:
        logger.info("Scan interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Scan failed with unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
