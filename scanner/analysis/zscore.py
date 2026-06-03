"""Z-score computation utilities for anomaly detection."""

import math
from typing import Sequence


def compute_zscore(current_value: float, historical_values: Sequence[float]) -> float | None:
    """Compute the z-score of a current value against historical values.
    
    Z-score = (current - mean) / std_dev
    
    Args:
        current_value: The current observation to evaluate
        historical_values: Historical observations for baseline
    
    Returns:
        Z-score as float, or None if computation is not possible
        (e.g., insufficient data or zero standard deviation)
    """
    if len(historical_values) < 3:
        return None
    
    n = len(historical_values)
    mean = sum(historical_values) / n
    
    variance = sum((x - mean) ** 2 for x in historical_values) / n
    std_dev = math.sqrt(variance)
    
    if std_dev < 1e-10:  # Effectively zero
        return None
    
    return (current_value - mean) / std_dev


def compute_rolling_zscore(
    values: Sequence[float], window: int = 24
) -> list[float | None]:
    """Compute rolling z-scores for a sequence of values.
    
    Args:
        values: Time series of observations
        window: Rolling window size
    
    Returns:
        List of z-scores (None for positions without enough history)
    """
    results = []
    for i in range(len(values)):
        if i < window:
            results.append(None)
            continue
        
        historical = values[i - window:i]
        current = values[i]
        results.append(compute_zscore(current, historical))
    
    return results
