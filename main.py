"""
Pinger v2.0 - Ana Orkestratör
==============================
Mod seçenekleri (.env'de TRADE_MODE):
  paper    → Sahte parayla gerçek zamanlı test (ÖNERİLEN başlangıç)
  backtest → Geçmiş 90 günde strateji testi yap, Telegram'a rapor gönder
  live     → Gerçek auto-trade (SADECE backtest + paper trading'den sonra!)
  alarm    → Sadece alarm, işlem yok (eski davranış)

Çalıştırma:
  python main.py
"""

import os
import sys
import time
import signal
import threading
import yaml
from datetime import datetime
from typing import Optional

import ccxt
from dotenv import load_dotenv

from core.scanner import MarketScanner
from core.signals import MarketSignal
from core.orderbook import OrderBookAnalyzer
from backtest.engine import Backtester, format_backtest_report, format_portfolio_report
from trading.paper_trader import PaperTrader
from ai.lstm_model import LSTMTrainer
from notifications.telegram import TelegramNotifier
from utils.logger import setup_logger

logger = setup_logger(__name__)


# ======================================================================
# Config & Env
# ======================================================================

def load_config(path="config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_env() -> dict:
    load_dotenv()
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.critical(f"Eksik env: {missing} — .env dosyasını kontrol et")
        sys.exit(1)
    return {
        "BINANCE_API_KEY": os.getenv("BINANCE_API_KEY", ""),
        "BINANCE_SECRET": os.getenv("BINANCE_SECRET", ""),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID"),
        "TRADE_MODE": os.getenv("TRADE_MODE", "paper"),
    }


def create_exchange(env: dict) -> ccxt.Exchange:
    params = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot", "adjustForTimeDifference": True},
    }
    if env["BINANCE_API_KEY"]:
        params["apiKey"] = env["BINANCE_API_KEY"]
        params["secret"] = env["BINANCE_SECRET"]
    ex = ccxt.binance(params)
    try:
        ex.load_markets()
        logger.info(f"Binance bağlantısı OK")
    except ccxt.NetworkError as e:
        logger.critical(f"Binance bağlanamadı: {e}")
        sys.exit(1)
    return ex


# ======================================================================
# Backtest modu
# ======================================================================

def run_backtest_mode(exchange, config, notifier):
    """
    Geçmiş 90 günde en iyi coinleri test eder.
    Sonucu Telegram'a gönderir.
    """
    logger.info("BACKTEST MODU başladı")
    notifier.send_text(
        "🔬 <b>Backtest başlatıldı</b>\n"
        "En hacimli 20 coin için son 90 gün test ediliyor...\n"
        "Bu 5-10 dakika sürebilir."
    )

    # En hacimli 20 coini al
    try:
        tickers = exchange.fetch_tickers()
        # Stablecoin ve gereksizleri filtrele
        skip = {"USDC", "FDUSD", "BUSD", "TUSD", "DAI", "EUR", "USD1", "PAXG", "USDP"}
        usdt = {
            k: v for k, v in tickers.items()
            if k.endswith("/USDT")
            and v.get("quoteVolume")
            and k.split("/")[0] not in skip
        }
        top20 = sorted(usdt, key=lambda s: float(usdt[s].get("quoteVolume") or 0), reverse=True)[:20]
        logger.info(f"Test edilecek semboller: {top20}")
    except Exception as e:
        logger.error(f"Ticker çekme hatası: {e}")
        notifier.send_text("❌ Backtest başlatılamadı, ticker verisi alınamadı.")
        return

    backtester = Backtester(exchange, config)

    # Önce tek tek raporlar
    all_results = []
    for symbol in top20:
        result = backtester.run(symbol, days=90, timeframe="15m")
        if result and result.total_trades >= 3:
            all_results.append(result)
            # Her sonucu Telegram'a gönder
            report = format_backtest_report(result)
            notifier.send_text(report)
            time.sleep(1)

    # Portfolio özeti
    if all_results:
        from backtest.engine import PortfolioResult
        portfolio = backtester._aggregate_portfolio(all_results)
        portfolio_report = format_portfolio_report(portfolio)
        notifier.send_text(portfolio_report)

    logger.info("Backtest tamamlandı")


# ======================================================================
# Ana Bot
# ======================================================================

class PingerBot:
    def __init__(self):
        self.config = load_config()
        self.env = load_env()
        self.mode = self.env["TRADE_MODE"]

        self._running = False
        self.exchange: Optional[ccxt.Exchange] = None
        self.notifier: Optional[TelegramNotifier] = None
        self.lstm_trainer: Optional[LSTMTrainer] = None
        self.scanner: Optional[MarketScanner] = None
        self.paper_trader: Optional[PaperTrader] = None
        self.ob_analyzer: Optional[OrderBookAnalyzer] = None

        self._start_time = datetime.now()
        self._total_signals = 0

        # Graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        self._shutdown_flag = False

    def setup(self):
        logger.info("=" * 55)
        logger.info(f"  Pinger v2.0  |  MOD: {self.mode.upper()}")
        logger.info("=" * 55)

        self.exchange = create_exchange(self.env)

        self.notifier = TelegramNotifier(
            config=self.config,
            bot_token=self.env["TELEGRAM_BOT_TOKEN"],
            chat_id=self.env["TELEGRAM_CHAT_ID"],
        )

        # BACKTEST modunda scanner lazım değil
        if self.mode == "backtest":
            return

        self.lstm_trainer = LSTMTrainer(self.exchange, self.config)

        # Paper trader
        if self.mode in ("paper", "live"):
            self.paper_trader = PaperTrader(
                exchange=self.exchange,
                config=self.config,
                notifier=self.notifier,
                initial_balance=1000.0,
            )
            self.paper_trader.start()

        # Order book analyzer
        self.ob_analyzer = OrderBookAnalyzer(
            exchange=self.exchange,
            config=self.config,
            signal_callback=self._on_ob_signal,
        )

        # Scanner
        self.scanner = MarketScanner(
            exchange=self.exchange,
            config=self.config,
            lstm_trainer=self.lstm_trainer,
            signal_callback=self._on_signal,
        )

    def run(self):
        self.setup()

        # BACKTEST özel mod
        if self.mode == "backtest":
            run_backtest_mode(self.exchange, self.config, self.notifier)
            return

        # Başlangıç mesajı
        mode_text = {
            "paper": "🧪 PAPER TRADE (Sahte para, gerçek sinyal)",
            "alarm": "🔔 ALARM MODU (Sadece bildirim)",
            "live": "💰 CANLI TRADE (Gerçek para!)",
        }.get(self.mode, self.mode.upper())

        self.notifier.send_text(
            f"🤖 <b>Pinger v2.0 Başladı!</b>\n\n"
            f"⚙️ Mod: <b>{mode_text}</b>\n"
            f"✅ LSTM AI aktif\n"
            f"✅ Momentum Stratejisi aktif\n"
            f"✅ Order Book Analizi aktif\n"
            f"✅ Binance Listing Monitor aktif\n\n"
            f"📊 500+ USDT çifti izleniyor\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Order book analyzer başlat
        if self.ob_analyzer:
            self.ob_analyzer.start()

        # Scanner başlat
        self.scanner.start()

        # Arka planda top coinsler için LSTM eğit
        self._pretrain_background()

        logger.info("Sistem çalışıyor. Durdurmak için Ctrl+C.")
        self._running = True

        try:
            while not self._shutdown_flag:
                self._hourly_tasks()
                time.sleep(10)
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    # Sinyal callback'leri
    # ------------------------------------------------------------------

    def _on_signal(self, signal: MarketSignal) -> None:
        """Scanner'dan gelen ana sinyal."""
        self._total_signals += 1

        logger.info(
            f"SİNYAL #{self._total_signals} | {signal.symbol} | "
            f"Skor:{signal.score}/9 | Yön:{signal.direction} | "
            f"Fiyat:{signal.price}"
        )

        # Order book'u bu coin için izlemeye ekle
        if self.ob_analyzer:
            current = list(self.ob_analyzer._watch_symbols)
            if signal.symbol not in current:
                current.insert(0, signal.symbol)
                self.ob_analyzer.set_watch_symbols(current[:50])

        # Telegram bildirimi
        if self.notifier:
            if "NEW BINANCE LISTING" in signal.signal_reasons:
                self.notifier.send_listing_alert(signal.symbol, signal)
            else:
                self.notifier.send_signal(signal)

        # Paper / live trade
        if self.mode in ("paper", "live") and self.paper_trader:
            if signal.score >= 6 and signal.direction in ("long", "short"):
                self.paper_trader.open_position(signal)

        # LSTM eğitimi gerekiyorsa arka planda başlat
        if self.lstm_trainer and self.lstm_trainer.needs_training(signal.symbol):
            t = threading.Thread(
                target=self.lstm_trainer.train,
                args=(signal.symbol,),
                daemon=True,
            )
            t.start()

    def _on_ob_signal(self, ob_signal) -> None:
        """Order book'tan gelen erken sinyal."""
        logger.info(
            f"OB SİNYAL | {ob_signal.symbol} | "
            f"Imbalance:{ob_signal.imbalance_ratio:.1f}x | "
            f"Yön:{ob_signal.direction}"
        )

        if self.notifier:
            dir_emoji = "📈" if ob_signal.direction == "long" else "📉"
            msg = (
                f"⚡ <b>ERKEN SİNYAL</b> {dir_emoji} (Order Book)\n\n"
                f"💎 {ob_signal.symbol}\n"
                f"📊 Alım/Satım oranı: <b>x{ob_signal.imbalance_ratio:.1f}</b>\n"
                f"💚 Alım tarafı: ${ob_signal.bid_volume:,.0f}\n"
                f"❤️ Satım tarafı: ${ob_signal.ask_volume:,.0f}\n"
                f"{'🐋 Büyük emir tespit edildi!' if ob_signal.large_orders_detected else ''}\n"
                f"💡 {ob_signal.reason}\n\n"
                f"⚠️ Fiyat henüz hareket ETMEDİ — erken uyarı\n"
                f"⏰ {ob_signal.timestamp.strftime('%H:%M:%S')}"
            )
            self.notifier.send_text(msg)

    # ------------------------------------------------------------------
    # Periyodik görevler
    # ------------------------------------------------------------------

    def _hourly_tasks(self) -> None:
        """Saatte bir çalışan görevler."""
        elapsed = (datetime.now() - self._start_time).total_seconds()
        if elapsed > 0 and int(elapsed) % 3600 < 10:
            if self.scanner:
                stats = self.scanner.get_stats()
                uptime_h = int(elapsed / 3600)
                stats["uptime"] = f"{uptime_h}h"
                stats["total_signals"] = self._total_signals
                if self.notifier:
                    self.notifier.send_stats(stats)

    def _pretrain_background(self) -> None:
        """Top 10 coin için LSTM'yi arka planda eğit."""
        def _train():
            try:
                tickers = self.exchange.fetch_tickers()
                usdt = {k: v for k, v in tickers.items() if k.endswith("/USDT") and v.get("quoteVolume")}
                top10 = sorted(usdt, key=lambda s: float(usdt[s].get("quoteVolume") or 0), reverse=True)[:10]
                for sym in top10:
                    if self.lstm_trainer and self.lstm_trainer.needs_training(sym):
                        acc = self.lstm_trainer.train(sym)
                        if acc:
                            logger.info(f"LSTM eğitildi: {sym} acc={acc:.3f}")
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Pretrain error: {e}")

        t = threading.Thread(target=_train, daemon=True, name="Pretrain")
        t.start()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown_handler(self, signum, frame):
        logger.info("Kapatma sinyali alındı...")
        self._shutdown_flag = True

    def _cleanup(self):
        logger.info("Pinger kapatılıyor...")
        if self.scanner:
            self.scanner.stop()
        if self.ob_analyzer:
            self.ob_analyzer.stop()
        if self.paper_trader:
            self.paper_trader.stop()
            # Son paper trading durumunu gönder
            if self.notifier:
                self.paper_trader.send_daily_report()
        if self.notifier:
            self.notifier.send_text("🔴 Pinger durduruldu.")
        logger.info("Pinger durduruldu.")


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    if sys.version_info < (3, 10):
        print("Python 3.10+ gerekli")
        sys.exit(1)
    PingerBot().run()
