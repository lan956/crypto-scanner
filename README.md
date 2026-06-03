# 🚨 Crypto Market Anomaly Scanner

Hourly GitHub Actions scanner monitoring **Binance Futures**, **Hyperliquid**, and **[trade.xyz](https://trade.xyz)** (HIP-3 builder markets) for anomalous funding rates, volume spikes, and open interest changes. Alerts are pushed to a Telegram channel with optional Gemini AI analysis.

## Architecture

```
GitHub Actions (hourly cron)
    │
    ├── Binance Futures API ──────── funding, volume, OI
    │
    ├── Hyperliquid Info API
    │   ├── HL Native Markets ──── funding, volume, OI
    │   └── xyz: Namespace ──────── trade.xyz (HIP-3) markets
    │
    ├── Z-Score Engine ─────────── anomaly detection
    │
    ├── Gemini AI (optional) ───── market analysis
    │
    └── Telegram Bot ──────────── alert delivery
```

## What It Detects

| Anomaly Type | Detection Method | Default Threshold |
|---|---|---|
| 🔴 **Extreme Funding** | Annualized funding rate | ±100% |
| 📊 **Volume Spike** | Z-score vs 7-day hourly history | Z > 2.0 |
| 📈 **OI Spike** | Z-score vs historical OI | Z > 2.0 |

Markets must have **≥$1,000 daily volume** to be scanned (filters dust/inactive markets).

## Setup

### 1. Create a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot`
3. Save the bot token
4. Create a channel and add your bot as admin
5. Get the channel chat ID (forward a message to [@userinfobot](https://t.me/userinfobot))

### 2. Get a Gemini API Key (Free)

1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Create an API key
3. Free tier allows ~15-30 requests/minute

### 3. Configure GitHub Secrets & Variables

Go to your repo → **Settings** → **Secrets and variables** → **Actions**

#### Secrets (required)

| Name | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Channel ID (e.g., `-100xxxxxxxxxx`) |
| `GEMINI_API_KEY` | Google AI Studio API key |

#### Variables (optional — have defaults)

| Name | Default | Description |
|---|---|---|
| `Z_SCORE_THRESHOLD` | `2.0` | Z-score threshold for volume/OI spikes |
| `FUNDING_THRESHOLD_ANNUALIZED` | `100` | Annualized funding rate threshold (±%) |
| `LOOKBACK_HOURS` | `168` | Hours of history for z-score (168 = 7 days) |
| `MIN_VOLUME_USD` | `1000` | Minimum 24h volume to scan a market |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite-preview-06-17` | Gemini model identifier |
| `GEMINI_MAX_RPM` | `10` | Conservative RPM cap for Gemini free tier |
| `MAX_CANDLE_FETCHES` | `50` | Max historical data fetches per run |

### 4. Enable GitHub Actions

The repo must be **public** for free GitHub Actions minutes. The workflow runs automatically every hour via cron.

To trigger manually: **Actions** → **Market Anomaly Scanner** → **Run workflow**

## Data Sources

### Binance Futures
- **Funding**: `GET /fapi/v1/premiumIndex` — 8-hour funding interval
- **Volume**: `GET /fapi/v1/ticker/24hr` — 24h quote volume
- **OI**: `GET /fapi/v1/openInterest` — per-symbol
- **History**: `GET /fapi/v1/klines` — hourly candles for z-score

### Hyperliquid (Native + trade.xyz)
- **All data**: `POST /info` with `{"type": "metaAndAssetCtxs"}` — 1-hour funding interval
- **trade.xyz**: Same endpoint with `{"type": "metaAndAssetCtxs", "dex": "xyz"}` — HIP-3 builder markets
- **History**: `POST /info` with `{"type": "candleSnapshot"}` — hourly candles

trade.xyz markets use the `xyz:` namespace (e.g., `xyz:TSLA`, `xyz:NVDA`) and are served entirely from the Hyperliquid API.

## Example Alert

```
🚨 MARKET ANOMALY ALERT — 2026-06-03 09:00 UTC

━━━ 💰 EXTREME FUNDING ━━━
🔴 Binance | ETHUSDT
   Funding: 0.0342% (annualized: +299.8%)
   Vol 24h: $1.20B | OI: $4.30B

🟢 Hyperliquid | SOL
   Funding: -0.0152% (annualized: -133.2%)
   Vol 24h: $89.00M | OI: $312.00M

━━━ 📊 VOLUME SPIKE ━━━
📊 trade.xyz | xyz:TSLA
   Vol 24h: $2.10M (z-score: 3.42)
   Funding: 0.0210% | OI: $890.00K

━━━ 🤖 AI ANALYSIS ━━━
ETH shows extremely elevated long funding suggesting
crowded longs; potential mean-reversion setup...
```

## Rate Limit Awareness

| API | Strategy |
|---|---|
| **Binance** | Monitors `X-MBX-USED-WEIGHT-1m` header, stays under 2400/min |
| **Hyperliquid** | 200ms courtesy delays between candle fetches |
| **Gemini** | In-memory RPM counter, exponential backoff on 429 |
| **Telegram** | 1s delay between message chunks, retry on 429 |

## Project Structure

```
├── .github/workflows/scan.yml    # Hourly cron workflow
├── scanner/
│   ├── main.py                   # Entry point & orchestrator
│   ├── config.py                 # Environment-based configuration
│   ├── exchanges/
│   │   ├── binance.py            # Binance Futures client
│   │   └── hyperliquid.py        # Hyperliquid + trade.xyz client
│   ├── analysis/
│   │   ├── zscore.py             # Z-score computation
│   │   └── detector.py           # Anomaly detection engine
│   ├── ai/
│   │   └── gemini.py             # Gemini AI integration
│   └── notifier/
│       └── telegram.py           # Telegram alert formatting & sending
└── requirements.txt              # Python dependencies (requests only)
```

## License

MIT
