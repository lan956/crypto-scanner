"""Anomaly detection engine for funding rates, volume, and open interest."""

import logging
from dataclasses import dataclass, field
from typing import Any

from scanner.analysis.zscore import compute_zscore

logger = logging.getLogger(__name__)


@dataclass
class Anomaly:
    """Represents a detected market anomaly."""
    exchange: str
    symbol: str
    anomaly_type: str  # 'extreme_funding', 'volume_spike', 'oi_spike'
    severity: float  # Higher = more extreme
    details: dict = field(default_factory=dict)
    
    def __str__(self) -> str:
        return f"{self.exchange} | {self.symbol} | {self.anomaly_type} (severity={self.severity:.2f})"


def annualize_funding_rate(rate: float, interval_hours: int) -> float:
    """Convert a periodic funding rate to annualized percentage.
    
    Args:
        rate: The funding rate per period (e.g., 0.0001 = 0.01%)
        interval_hours: Hours between funding payments
            - Binance: 8 hours (3 payments/day)
            - Hyperliquid: 1 hour (24 payments/day)
    
    Returns:
        Annualized funding rate as percentage (e.g., 109.5 means 109.5%)
    """
    periods_per_year = 8760 / interval_hours  # 8760 hours in a year
    return rate * periods_per_year * 100


def detect_funding_anomalies(
    markets: list[dict],
    threshold_annualized: float = 100.0,
) -> list[Anomaly]:
    """Detect markets with extreme annualized funding rates.
    
    Args:
        markets: List of normalized market dicts
        threshold_annualized: Absolute annualized rate threshold (±%)
    
    Returns:
        List of Anomaly objects for markets exceeding the threshold
    """
    anomalies = []
    
    for m in markets:
        rate = m.get("funding_rate", 0)
        interval = m.get("funding_interval_hours", 8)
        annualized = annualize_funding_rate(rate, interval)
        
        if abs(annualized) >= threshold_annualized:
            anomalies.append(Anomaly(
                exchange=m["exchange"],
                symbol=m["symbol"],
                anomaly_type="extreme_funding",
                severity=abs(annualized),
                details={
                    "funding_rate": rate,
                    "funding_rate_pct": f"{rate * 100:.4f}%",
                    "annualized_pct": f"{annualized:+.1f}%",
                    "interval_hours": interval,
                    "volume_24h_usd": m.get("volume_24h_usd", 0),
                    "open_interest_usd": m.get("open_interest_usd", 0),
                    "mark_price": m.get("mark_price", 0),
                },
            ))
    
    logger.info(f"Found {len(anomalies)} funding anomalies")
    return anomalies


def detect_volume_anomalies(
    markets: list[dict],
    historical_volumes: dict[str, list[float]],
    z_threshold: float = 2.0,
) -> list[Anomaly]:
    """Detect markets with unusual volume spikes using z-scores.
    
    Args:
        markets: List of normalized market dicts
        historical_volumes: Dict mapping symbol -> list of hourly volumes
        z_threshold: Minimum absolute z-score to flag
    
    Returns:
        List of Anomaly objects for markets with volume spikes
    """
    anomalies = []
    
    for m in markets:
        key = f"{m['exchange']}:{m['symbol']}"
        hist = historical_volumes.get(key, [])
        
        if not hist:
            continue
        
        current_vol = m.get("volume_24h_usd", 0)
        # Convert 24h volume to hourly estimate for comparison with hourly candles
        current_hourly = current_vol / 24.0
        
        z = compute_zscore(current_hourly, hist)
        
        if z is not None and abs(z) >= z_threshold:
            anomalies.append(Anomaly(
                exchange=m["exchange"],
                symbol=m["symbol"],
                anomaly_type="volume_spike",
                severity=abs(z),
                details={
                    "z_score": round(z, 2),
                    "volume_24h_usd": current_vol,
                    "avg_hourly_volume": round(sum(hist) / len(hist), 2) if hist else 0,
                    "funding_rate": m.get("funding_rate", 0),
                    "open_interest_usd": m.get("open_interest_usd", 0),
                    "mark_price": m.get("mark_price", 0),
                },
            ))
    
    logger.info(f"Found {len(anomalies)} volume anomalies")
    return anomalies


def detect_oi_anomalies(
    markets: list[dict],
    historical_oi: dict[str, list[float]],
    z_threshold: float = 2.0,
) -> list[Anomaly]:
    """Detect markets with unusual open interest spikes using z-scores.
    
    Since historical OI data is limited, this uses whatever history is available.
    If no history, the anomaly is skipped (not flagged).
    
    Args:
        markets: List of normalized market dicts
        historical_oi: Dict mapping symbol -> list of historical OI values
        z_threshold: Minimum absolute z-score to flag
    
    Returns:
        List of Anomaly objects for markets with OI spikes
    """
    anomalies = []
    
    for m in markets:
        key = f"{m['exchange']}:{m['symbol']}"
        hist = historical_oi.get(key, [])
        
        if not hist:
            continue
        
        current_oi = m.get("open_interest_usd", 0)
        z = compute_zscore(current_oi, hist)
        
        if z is not None and abs(z) >= z_threshold:
            anomalies.append(Anomaly(
                exchange=m["exchange"],
                symbol=m["symbol"],
                anomaly_type="oi_spike",
                severity=abs(z),
                details={
                    "z_score": round(z, 2),
                    "open_interest_usd": current_oi,
                    "avg_oi": round(sum(hist) / len(hist), 2) if hist else 0,
                    "volume_24h_usd": m.get("volume_24h_usd", 0),
                    "funding_rate": m.get("funding_rate", 0),
                    "mark_price": m.get("mark_price", 0),
                },
            ))
    
    logger.info(f"Found {len(anomalies)} OI anomalies")
    return anomalies


def detect_all_anomalies(
    markets: list[dict],
    historical_volumes: dict[str, list[float]],
    historical_oi: dict[str, list[float]],
    funding_threshold: float = 100.0,
    z_threshold: float = 2.0,
) -> list[Anomaly]:
    """Run all anomaly detectors and return combined results.
    
    A market needs to trigger AT LEAST ONE of:
    - Extreme funding (annualized > ±threshold%)
    - Volume spike (z-score > threshold)
    - OI spike (z-score > threshold)
    """
    all_anomalies = []
    
    all_anomalies.extend(
        detect_funding_anomalies(markets, funding_threshold)
    )
    all_anomalies.extend(
        detect_volume_anomalies(markets, historical_volumes, z_threshold)
    )
    all_anomalies.extend(
        detect_oi_anomalies(markets, historical_oi, z_threshold)
    )
    
    # Sort by severity (highest first)
    all_anomalies.sort(key=lambda a: a.severity, reverse=True)
    
    logger.info(f"Total anomalies detected: {len(all_anomalies)}")
    return all_anomalies
