# Pinger v2.0 — Crypto Alert System

Real-time cryptocurrency scanner for Binance. Monitors 500+ USDT pairs 24/7 and sends instant Telegram alerts on volume spikes, price breakouts, and momentum signals. Includes LSTM AI predictions, paper trading, and backtesting.

## Features

- **Real-time scanning:** All Binance USDT pairs every 15 seconds
- **Volume spike detection:** Alerts when volume exceeds 2x the 20-period average
- **Price breakout alerts:** Configurable min/max % thresholds
- **LSTM AI predictions:** Machine learning model for early signal detection
- **Momentum strategy:** EMA, RSI, MACD-based signal scoring
- **Paper trading:** Simulate trades with $1000 virtual balance
- **Backtesting:** Walk-forward engine on 90-day historical data
- **Telegram notifications:** Instant alerts with coin details and Binance link
- **Spam protection:** 60-minute cooldown per coin
- **Low-volume filter:** Ignores pairs under $500K daily volume

## Modes

| Mode | Description |
|------|-------------|
| `alarm` | Scan & alert only, no trades |
| `paper` | Simulate trades with virtual balance |
| `backtest` | Test strategy on 90-day history |
| `live` | Real trading (use after testing!) |

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

# 3. Run
./start.sh
# or: python main.py
```

> See [KURULUM.md](KURULUM.md) for full setup guide including Telegram bot creation and VPS deployment.

## Configuration

All parameters in `config.yaml` — no secrets there:

```yaml
signals:
  volume_spike_multiplier: 2.0   # Alert when volume > 2x average
  price_change_min: 2.5          # Minimum % move to trigger alert
  min_signal_score: 7            # AI/momentum score threshold

risk:
  take_profit_pct: 10.0
  stop_loss_pct: 7.0
  max_daily_trades: 5
```

## Project Structure

```
pinger/
├── ai/                  # LSTM model & data fetcher
├── backtest/            # Walk-forward backtesting engine
├── core/                # Scanner, signals, filters, orderbook
├── notifications/       # Telegram alert sender
├── strategies/          # Momentum strategy
├── trading/             # Paper trader
├── utils/               # Logger, database
├── main.py              # Entry point
├── config.yaml          # All settings (no secrets)
└── .env.example         # Secret keys template
```

## Tech Stack

- Python 3.11+
- Binance REST API + WebSocket
- TensorFlow / Keras (LSTM)
- SQLite

## Disclaimer

This project is for **educational purposes only**. Crypto trading carries significant financial risk. Never trade with money you cannot afford to lose.
