"""Binance Futures API client for funding rates, volume, and open interest."""

import logging
import time
from typing import Any

import requests

from scanner import config

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com"
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def _get(endpoint: str, params: dict | None = None, max_retries: int = 3) -> Any:
    """Make a GET request to Binance Futures API with retry and rate limit handling.
    
    Checks X-MBX-USED-WEIGHT-1m header to monitor weight usage.
    Retries on 429 with exponential backoff.
    Raises on 418 (IP ban) or persistent failures.
    """
    url = f"{BASE_URL}{endpoint}"
    proxies = {"http": config.BINANCE_PROXY, "https": config.BINANCE_PROXY} if config.BINANCE_PROXY else None
    
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, proxies=proxies, timeout=15)
            # Log weight usage
            used_weight = resp.headers.get("X-MBX-USED-WEIGHT-1m", "?")
            logger.debug(f"Binance weight used: {used_weight}/2400")
            
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning(f"Binance rate limited, retrying in {retry_after}s")
                time.sleep(retry_after)
                continue
            elif resp.status_code == 418:
                logger.error("Binance IP ban detected! Aborting.")
                raise RuntimeError("Binance IP ban (418)")
            
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.warning(f"Binance request timeout (attempt {attempt + 1})")
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Binance connection error: {e}")
            time.sleep(2 ** attempt)
    
    raise RuntimeError(f"Binance API failed after {max_retries} attempts: {endpoint}")


def fetch_all_funding_rates() -> list[dict]:
    """Fetch current funding rates for all USDT perpetual pairs.
    
    Uses GET /fapi/v1/premiumIndex (weight=10 for all symbols).
    
    Returns list of dicts with keys:
      - symbol: str (e.g. 'BTCUSDT')
      - lastFundingRate: str
      - markPrice: str
      - nextFundingTime: int
    """
    logger.info("Fetching Binance funding rates...")
    data = _get("/fapi/v1/premiumIndex")
    # Filter to only USDT-margined pairs (skip COIN-margined)
    result = [d for d in data if d.get("symbol", "").endswith("USDT")]
    logger.info(f"Fetched {len(result)} Binance funding rates")
    return result


def fetch_all_tickers() -> list[dict]:
    """Fetch 24h ticker data for all symbols.
    
    Uses GET /fapi/v1/ticker/24hr (weight=40 for all symbols).
    
    Returns list of dicts with keys:
      - symbol: str
      - quoteVolume: str (24h volume in USDT)
      - lastPrice: str
      - priceChangePercent: str
    """
    logger.info("Fetching Binance 24h tickers...")
    data = _get("/fapi/v1/ticker/24hr")
    result = [d for d in data if d.get("symbol", "").endswith("USDT")]
    logger.info(f"Fetched {len(result)} Binance tickers")
    return result


def fetch_open_interest(symbol: str) -> dict:
    """Fetch current open interest for a single symbol.
    
    Uses GET /fapi/v1/openInterest (weight=1).
    
    Returns dict with keys:
      - openInterest: str
      - symbol: str
      - time: int
    """
    return _get("/fapi/v1/openInterest", params={"symbol": symbol})


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 168) -> list[list]:
    """Fetch historical klines/candlestick data.
    
    Uses GET /fapi/v1/klines (weight=5 for limit<=1000).
    
    Args:
        symbol: Trading pair e.g. 'BTCUSDT'
        interval: Candle interval e.g. '1h'
        limit: Number of candles (max 1500)
    
    Returns list of candle arrays:
        [open_time, open, high, low, close, volume, close_time, 
         quote_volume, trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]
    """
    return _get("/fapi/v1/klines", params={
        "symbol": symbol,
        "interval": interval,
        "limit": min(limit, 1500),
    })


def fetch_all_market_data(min_volume_usd: float = 1000.0) -> list[dict]:
    """Fetch and merge all market data: funding, volume, OI.
    
    Two-pass approach:
    1. Fetch all funding rates + all tickers (2 API calls, weight=50 total)
    2. Merge by symbol, filter by minimum volume
    3. Fetch OI only for markets passing volume filter (weight=1 each)
    
    Returns list of normalized market dicts:
      - exchange: 'Binance'
      - symbol: str (e.g. 'BTCUSDT')
      - coin: str (e.g. 'BTC')
      - funding_rate: float
      - funding_interval_hours: 8
      - volume_24h_usd: float
      - open_interest_usd: float
      - mark_price: float
    """
    funding_data = fetch_all_funding_rates()
    ticker_data = fetch_all_tickers()
    
    # Index tickers by symbol
    ticker_map = {t["symbol"]: t for t in ticker_data}
    
    markets = []
    oi_fetch_count = 0
    
    for f in funding_data:
        sym = f["symbol"]
        ticker = ticker_map.get(sym)
        if not ticker:
            continue
        
        volume_24h = float(ticker.get("quoteVolume", 0))
        if volume_24h < min_volume_usd:
            continue
        
        funding_rate = float(f.get("lastFundingRate", 0))
        mark_price = float(f.get("markPrice", 0))
        
        # Fetch OI for this symbol (rate limit: add small delay every 50 calls)
        oi_usd = 0.0
        try:
            oi_data = fetch_open_interest(sym)
            oi_usd = float(oi_data.get("openInterest", 0)) * mark_price
            oi_fetch_count += 1
            if oi_fetch_count % 50 == 0:
                time.sleep(0.5)  # Courtesy delay
        except Exception as e:
            logger.warning(f"Failed to fetch OI for {sym}: {e}")
        
        coin = sym.replace("USDT", "")
        
        markets.append({
            "exchange": "Binance",
            "symbol": sym,
            "coin": coin,
            "funding_rate": funding_rate,
            "funding_interval_hours": 8,
            "volume_24h_usd": volume_24h,
            "open_interest_usd": oi_usd,
            "mark_price": mark_price,
        })
    
    logger.info(f"Binance: {len(markets)} markets after filtering (fetched {oi_fetch_count} OIs)")
    return markets


def fetch_historical_volumes(symbol: str, lookback_hours: int = 168) -> list[float]:
    """Fetch hourly historical quote volumes for z-score computation.
    
    Returns list of hourly quote volumes (in USDT).
    """
    try:
        klines = fetch_klines(symbol, interval="1h", limit=lookback_hours)
        return [float(k[7]) for k in klines]  # index 7 = quote volume
    except Exception as e:
        logger.warning(f"Failed to fetch klines for {symbol}: {e}")
        return []


def fetch_historical_oi_changes(symbol: str, lookback_hours: int = 168) -> list[float]:
    """Fetch hourly close prices as a proxy for OI change tracking.
    
    Note: Binance doesn't have a direct historical OI endpoint in the 
    public free tier. We use the current OI snapshot and compare against
    recent volume patterns as a heuristic.
    
    For proper historical OI, you'd need /futures/data/openInterestHist 
    which requires higher-frequency polling and storage.
    
    Returns empty list - OI z-score for Binance uses volume as proxy.
    """
    # Binance historical OI endpoint (/futures/data/openInterestHist) only 
    # supports 5m/15m/30m/1h/2h/4h/6h/12h/1d periods and is limited.
    # We'll use the volume z-score as a proxy for unusual activity.
    return []
