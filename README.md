# 🔔 Pinger — Real-Time Crypto Alert System

A real-time cryptocurrency alert system powered by LSTM AI, designed to detect momentum shifts and send instant Telegram notifications.

## Features

- **LSTM Neural Network** — predicts price movement based on historical data
- **Momentum Scanner** — detects breakout signals across multiple trading pairs
- **Telegram Notifications** — instant alerts with entry/exit signals
- **Backtesting Engine** — validate strategies against historical data
- **Multi-pair Support** — monitor BTC, ETH, and altcoins simultaneously

## Tech Stack

- **Language:** Python
- **AI/ML:** TensorFlow / Keras (LSTM)
- **Exchange API:** Binance WebSocket
- **Notifications:** Telegram Bot API
- **Data:** Pandas, NumPy

## How It Works

```
Market Data (WebSocket) → Momentum Scanner → LSTM Model → Signal → Telegram Alert
```

1. Connects to Binance WebSocket for live price data
2. Momentum scanner identifies potential breakout zones
3. LSTM model validates the signal
4. Alert sent to Telegram with price, pair, and direction

## Setup

```bash
git clone https://github.com/devslotcy/pinger
cd pinger
pip install -r requirements.txt
cp .env.example .env
# Add your Binance API keys and Telegram Bot Token to .env
python main.py
```

## Environment Variables

```env
BINANCE_API_KEY=your_key
BINANCE_SECRET=your_secret
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

## Backtesting

```bash
python backtest.py --pair BTCUSDT --days 30
```

---

Built by [Mucahit Tiglioglu](https://github.com/devslotcy)
