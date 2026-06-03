"""Hyperliquid API client for funding rates, volume, and open interest.

Serves both Hyperliquid native markets and trade.xyz (HIP-3 builder markets
under the xyz: namespace). All data comes from the same API endpoint.
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
SESSION = requests.Session()
SESSION.headers.update({"Content-Type": "application/json"})


def _post(body: dict, max_retries: int = 3) -> Any:
    """Make a POST request to Hyperliquid info endpoint with retry.
    
    Rate limit: 1,200 weight/min. Most info requests cost 20 weight.
    No API key required for read-only requests.
    """
    for attempt in range(max_retries):
        try:
            resp = SESSION.post(INFO_URL, json=body, timeout=15)
            
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"Hyperliquid rate limited, retrying in {wait}s")
                time.sleep(wait)
                continue
            
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.warning(f"Hyperliquid timeout (attempt {attempt + 1})")
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Hyperliquid connection error: {e}")
            time.sleep(2 ** attempt)
    
    raise RuntimeError(f"Hyperliquid API failed after {max_retries} attempts")


def fetch_meta_and_contexts(dex: str = "") -> tuple[dict, list[dict]]:
    """Fetch perpetuals metadata and asset contexts.
    
    Args:
        dex: Perp dex name. Empty string for HL native, 'xyz' for trade.xyz.
    
    Returns:
        Tuple of (meta_dict, asset_contexts_list)
        - meta_dict has 'universe' key with list of {name, szDecimals, maxLeverage}
        - asset_contexts_list has dicts with: funding, openInterest, dayNtlVlm, 
          markPx, midPx, oraclePx, premium, prevDayPx
    """
    body = {"type": "metaAndAssetCtxs"}
    if dex:
        body["dex"] = dex
    
    data = _post(body)
    
    if not isinstance(data, list) or len(data) < 2:
        raise ValueError(f"Unexpected metaAndAssetCtxs response format: {type(data)}")
    
    meta = data[0]
    ctxs = data[1]
    return meta, ctxs


def fetch_candle_snapshot(
    coin: str, interval: str = "1h",
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> list[dict]:
    """Fetch candle/kline data for a specific coin.
    
    Args:
        coin: Coin name e.g. 'BTC' or 'xyz:TSLA' for HIP-3 markets
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 1d, etc.)
        start_time_ms: Start time in milliseconds
        end_time_ms: End time in milliseconds
    
    Returns list of candle dicts with keys:
        t: open time (ms), T: close time (ms), s: symbol, i: interval,
        o: open, h: high, l: low, c: close, v: volume, n: number of trades
    """
    now_ms = int(time.time() * 1000)
    if end_time_ms is None:
        end_time_ms = now_ms
    if start_time_ms is None:
        start_time_ms = end_time_ms - (168 * 3600 * 1000)  # 7 days
    
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
        },
    }
    return _post(body)


def _determine_exchange_label(coin_name: str) -> str:
    """Determine the exchange label based on coin naming.
    
    Returns 'trade.xyz' for xyz: prefixed coins, 'Hyperliquid' otherwise.
    """
    if coin_name.startswith("xyz:"):
        return "trade.xyz"
    return "Hyperliquid"


def fetch_all_market_data(min_volume_usd: float = 1000.0) -> list[dict]:
    """Fetch and normalize market data from both HL native and xyz (trade.xyz) markets.
    
    Makes 2 API calls:
    1. metaAndAssetCtxs (no dex) -> HL native perps
    2. metaAndAssetCtxs (dex='xyz') -> trade.xyz HIP-3 markets
    
    Returns list of normalized market dicts:
      - exchange: 'Hyperliquid' or 'trade.xyz'
      - symbol: str (e.g. 'BTC' or 'xyz:TSLA')
      - coin: str (same as symbol for HL)
      - funding_rate: float
      - funding_interval_hours: 1 (HL uses hourly funding)
      - volume_24h_usd: float
      - open_interest_usd: float
      - mark_price: float
    """
    markets = []
    
    # Fetch HL native markets
    for dex_name in ["", "xyz"]:
        label = "HL native" if dex_name == "" else f"xyz (trade.xyz)"
        try:
            logger.info(f"Fetching Hyperliquid markets (dex='{dex_name}')...")
            meta, ctxs = fetch_meta_and_contexts(dex=dex_name)
            universe = meta.get("universe", [])
            
            if len(universe) != len(ctxs):
                logger.warning(
                    f"Universe/ctx length mismatch for dex='{dex_name}': "
                    f"{len(universe)} vs {len(ctxs)}"
                )
            
            count = 0
            for i, asset in enumerate(universe):
                if i >= len(ctxs):
                    break
                
                ctx = ctxs[i]
                name = asset.get("name", "")
                
                # Skip delisted assets
                if asset.get("isDelisted", False):
                    continue
                
                # Parse numeric values safely
                try:
                    funding_rate = float(ctx.get("funding", 0) or 0)
                    volume_24h = float(ctx.get("dayNtlVlm", 0) or 0)
                    mark_price = float(ctx.get("markPx", 0) or 0)
                    oi_raw = float(ctx.get("openInterest", 0) or 0)
                    oi_usd = oi_raw * mark_price if mark_price > 0 else 0.0
                except (ValueError, TypeError) as e:
                    logger.debug(f"Skipping {name}: parse error {e}")
                    continue
                
                # Filter by minimum volume
                if volume_24h < min_volume_usd:
                    continue
                
                exchange = _determine_exchange_label(name)
                
                markets.append({
                    "exchange": exchange,
                    "symbol": name,
                    "coin": name,
                    "funding_rate": funding_rate,
                    "funding_interval_hours": 1,  # Hyperliquid uses hourly funding
                    "volume_24h_usd": volume_24h,
                    "open_interest_usd": oi_usd,
                    "mark_price": mark_price,
                })
                count += 1
            
            logger.info(f"{label}: {count} markets after filtering")
            
            # Small delay between the two API calls
            if dex_name == "":
                time.sleep(0.2)
                
        except Exception as e:
            logger.error(f"Failed to fetch {label} markets: {e}")
    
    return markets


def fetch_historical_volumes(coin: str, lookback_hours: int = 168) -> list[float]:
    """Fetch hourly historical volumes for z-score computation.
    
    Args:
        coin: Coin name e.g. 'BTC' or 'xyz:TSLA'
        lookback_hours: Number of hours to look back
    
    Returns list of hourly volumes.
    """
    try:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (lookback_hours * 3600 * 1000)
        
        candles = fetch_candle_snapshot(
            coin=coin, interval="1h",
            start_time_ms=start_ms, end_time_ms=now_ms,
        )
        
        # 'v' field is base volume, we need to compute notional
        volumes = []
        for c in candles:
            try:
                base_vol = float(c.get("v", 0) or 0)
                close_px = float(c.get("c", 0) or 0)
                volumes.append(base_vol * close_px)
            except (ValueError, TypeError):
                volumes.append(0.0)
        
        return volumes
    except Exception as e:
        logger.warning(f"Failed to fetch candles for {coin}: {e}")
        return []


def fetch_historical_oi(coin: str, lookback_hours: int = 168) -> list[float]:
    """Hyperliquid doesn't provide historical OI via public API.
    
    Returns empty list - OI anomaly detection will rely on the current 
    snapshot compared against volume-based heuristics.
    """
    # No historical OI endpoint available in Hyperliquid public API.
    # The candleSnapshot only contains price/volume data.
    return []
