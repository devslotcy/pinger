"""
Pinger v2.0 - Telegram Bildirim Modülü
HTML formatlı rich mesajlar, Binance/TradingView/CMC linkleri.
Rate limiting, retry, mesaj kuyruğu dahil.
"""

import time
import threading
from queue import Queue, Empty
from typing import Optional
from datetime import datetime

import requests

from core.signals import MarketSignal
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Telegram API
TG_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    """
    Telegram Bot ile sinyal bildirimleri gönderir.
    - HTML parse mode
    - Emoji + link formatı
    - Rate limit koruması (Telegram: 30 msg/sec, 20 msg/min per chat)
    - Retry mekanizması
    - Arka plan mesaj kuyruğu
    """

    def __init__(self, config: dict, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        tg_cfg = config.get("telegram", {})
        self.parse_mode = tg_cfg.get("parse_mode", "HTML")
        self.disable_preview = tg_cfg.get("disable_web_page_preview", True)

        # Rate limiting: Telegram'a max 1 msg/sn gönder
        self._msg_queue: Queue = Queue()
        self._last_send_time = 0.0
        self._min_send_interval = 1.1  # saniye

        # Arka plan gönderim thread'i
        self._send_thread = threading.Thread(
            target=self._send_worker,
            daemon=True,
            name="TelegramSender",
        )
        self._send_thread.start()

        self._sent_count = 0
        self._error_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_signal(self, signal: MarketSignal) -> None:
        """Sinyal bildirimi kuyruğa ekle."""
        msg = self._format_signal_message(signal)
        self._msg_queue.put(msg)

    def send_text(self, text: str, disable_preview: bool = True) -> None:
        """Ham metin mesajı gönder."""
        self._msg_queue.put({"text": text, "disable_preview": disable_preview})

    def send_listing_alert(self, symbol: str, signal: MarketSignal) -> None:
        """Yeni Binance listing bildirimi."""
        base = symbol.replace("/USDT", "")
        msg_text = (
            f"🚨 <b>YENİ BİNANCE LİSTİNG!</b> 🚨\n\n"
            f"💎 <b>{symbol}</b>\n"
            f"⚡ Anında işlem fırsatı!\n\n"
            f"🔗 <a href='https://www.binance.com/en/trade/{base}_USDT'>Binance Trade</a> | "
            f"<a href='https://coinmarketcap.com/currencies/{base.lower()}/'>CMC</a>\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        self._msg_queue.put({"text": msg_text, "disable_preview": True})

    def send_startup_message(self) -> None:
        """Bot başlangıç bildirimi."""
        msg = (
            "🤖 <b>Pinger v2.0 Başladı!</b>\n\n"
            "✅ LSTM AI Modülü aktif\n"
            "✅ Momentum Stratejisi aktif\n"
            "✅ Hacim Spike Tarayıcı aktif\n"
            "✅ Binance Listing Monitor aktif\n\n"
            f"📊 500+ USDT çifti taranıyor\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._msg_queue.put({"text": msg, "disable_preview": True})

    def send_stats(self, stats: dict) -> None:
        """İstatistik raporu gönder."""
        msg = (
            f"📈 <b>Pinger İstatistikleri</b>\n\n"
            f"🔍 Taranan çift: {stats.get('pairs_scanned', 0)}\n"
            f"📡 Toplam sinyal: {stats.get('signals_generated', 0)}\n"
            f"❌ Hata sayısı: {stats.get('errors', 0)}\n"
            f"🕐 Son tarama: {stats.get('last_scan', 'N/A')}\n\n"
            f"🚫 Cooldown'daki coin: {stats.get('filter_stats', {}).get('active_cooldowns', 0)}\n"
            f"⛔ Kara listede: {stats.get('filter_stats', {}).get('active_blacklists', 0)}\n\n"
            f"📩 Gönderilen mesaj: {self._sent_count}\n"
            f"⚡ Telegram hatası: {self._error_count}"
        )
        self._msg_queue.put({"text": msg, "disable_preview": True})

    # ------------------------------------------------------------------
    # Mesaj formatlama
    # ------------------------------------------------------------------

    def _format_signal_message(self, signal: MarketSignal) -> dict:
        """MarketSignal'i zengin HTML mesajına dönüştürür."""
        base = signal.symbol.replace("/USDT", "")
        price_str = self._format_price(signal.price)

        # Skor çubuğu görsel
        score_bar = self._score_bar(signal.score)

        # Yön göstergesi
        dir_text = "LONG 📈" if signal.direction == "long" else "SHORT 📉" if signal.direction == "short" else "WATCH 👀"

        # LSTM satırı
        lstm_line = ""
        if signal.lstm_probability is not None:
            lstm_pct = signal.lstm_probability * 100
            lstm_emoji = "🟢" if lstm_pct >= 65 else "🟡" if lstm_pct >= 50 else "🔴"
            lstm_line = f"\n🧠 <b>AI Tahmin:</b> {lstm_emoji} {lstm_pct:.1f}%"

        # Strateji detayları
        strategy_lines = ""
        for strat_sig in signal.strategy_signals[:2]:
            strategy_lines += f"\n📐 <b>{strat_sig.strategy_name}:</b> {strat_sig.reason[:80]}"
            # RSI/MACD değerleri varsa göster
            if "rsi" in strat_sig.indicators:
                strategy_lines += f" (RSI:{strat_sig.indicators['rsi']:.1f})"

        # Nedenler
        reasons_text = ""
        if signal.signal_reasons:
            reasons_text = "\n💡 " + " • ".join(signal.signal_reasons[:4])

        msg = (
            f"{signal.score_emoji} <b>{signal.symbol}</b> — Skor: {signal.score}/9\n"
            f"{score_bar}\n\n"
            f"💰 Fiyat: <code>{price_str}</code> USDT\n"
            f"📊 15dk: {signal.price_change_15m:+.2f}%\n"
            f"💧 Hacim 24h: ${signal.volume_24h/1_000_000:.1f}M\n"
            f"📦 Hacim Spike: x{signal.volume_ratio:.1f}"
            f"{lstm_line}"
            f"{strategy_lines}"
            f"{reasons_text}\n\n"
            f"🎯 Yön: <b>{dir_text}</b>\n"
            f"✅ TP: +{signal.take_profit_pct:.0f}% | ❌ SL: -{signal.stop_loss_pct:.0f}% | R:R {signal.risk_reward:.1f}\n\n"
            f"🔗 <a href='https://www.binance.com/en/trade/{base}_USDT'>Binance</a> | "
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT'>TradingView</a> | "
            f"<a href='https://coinmarketcap.com/currencies/{base.lower()}/'>CMC</a>\n\n"
            f"⏰ {signal.timestamp.strftime('%H:%M:%S')}"
        )

        return {"text": msg, "disable_preview": True}

    def _score_bar(self, score: int) -> str:
        """Görsel skor çubuğu (1-9)."""
        filled = "█" * score
        empty = "░" * (9 - score)
        return f"[{filled}{empty}]"

    def _format_price(self, price: float) -> str:
        """Fiyatı uygun precision ile formatlar."""
        if price == 0:
            return "N/A"
        if price >= 1000:
            return f"{price:,.2f}"
        elif price >= 1:
            return f"{price:.4f}"
        elif price >= 0.01:
            return f"{price:.6f}"
        else:
            return f"{price:.8f}"

    # ------------------------------------------------------------------
    # Gönderim altyapısı
    # ------------------------------------------------------------------

    def _send_worker(self) -> None:
        """Arka planda mesaj kuyruğunu boşaltır."""
        while True:
            try:
                msg_data = self._msg_queue.get(timeout=5)
                self._send_with_retry(msg_data)
                self._msg_queue.task_done()
            except Empty:
                continue
            except Exception as e:
                logger.error(f"Send worker error: {e}")

    def _send_with_retry(self, msg_data: dict, max_retries: int = 3) -> bool:
        """
        Rate limit uyarak mesaj gönderir, hata durumunda retry.
        """
        # Rate limit: son göndermeden en az 1.1 sn geçmeli
        now = time.time()
        elapsed = now - self._last_send_time
        if elapsed < self._min_send_interval:
            time.sleep(self._min_send_interval - elapsed)

        url = TG_API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": msg_data["text"],
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": msg_data.get("disable_preview", True),
        }

        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json=payload, timeout=15)
                self._last_send_time = time.time()

                if resp.status_code == 200:
                    self._sent_count += 1
                    return True

                elif resp.status_code == 429:
                    # Telegram rate limit
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                    logger.warning(f"Telegram rate limit. Waiting {retry_after}s")
                    time.sleep(retry_after)

                elif resp.status_code == 400:
                    # Bad request — muhtemelen HTML parse hatası, düz metin dene
                    logger.error(f"Telegram 400: {resp.text[:200]}")
                    payload["parse_mode"] = "Markdown"
                    payload["text"] = self._strip_html(msg_data["text"])

                else:
                    logger.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
                    time.sleep(2 * (attempt + 1))

            except requests.Timeout:
                logger.warning(f"Telegram timeout (attempt {attempt+1})")
                time.sleep(3)
            except requests.ConnectionError as e:
                logger.error(f"Telegram connection error: {e}")
                time.sleep(5)

        self._error_count += 1
        return False

    def _strip_html(self, text: str) -> str:
        """HTML tag'leri kaldırır (fallback için)."""
        import re
        clean = re.sub(r"<[^>]+>", "", text)
        return clean
