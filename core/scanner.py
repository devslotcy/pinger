"""
Pinger v2.0 - Market Tarayıcı
Binance USDT spot çiftlerini tarar, filtreleyip sinyal kuyruğuna ekler.
- Configurable interval (VIP: 1s, Free: 15s)
- Binance yeni listing scrape
- Rate limit yönetimi
- Async-ready tasarım (threading ile)
"""

import time
import threading
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional, Dict, Callable, Set
from queue import Queue

import ccxt

from core.signals import SignalScorer, MarketSignal
from core.filters import SignalFilter
from ai.data_fetcher import OHLCVFetcher
from strategies.momentum import MomentumStrategy
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Binance yeni listing URL
BINANCE_LISTING_URL = "https://www.binance.com/en/support/announcement/new-cryptocurrency-listing"


class MarketScanner:
    """
    Ana tarama motoru.
    - Tüm USDT spotları periyodik tarar
    - Her sembol için OHLCV çeker
    - Strateji + LSTM + sinyal hesaplar
    - Filtrelenmiş sinyalleri callback ile iletir
    """

    def __init__(
        self,
        exchange: ccxt.Exchange,
        config: dict,
        lstm_trainer=None,
        signal_callback: Optional[Callable] = None,
    ):
        self.exchange = exchange
        self.config = config
        self.lstm_trainer = lstm_trainer
        self.signal_callback = signal_callback

        scan_cfg = config.get("scanning", {})
        self.interval = scan_cfg.get("interval_seconds", 15)
        self.max_pairs = scan_cfg.get("max_pairs", 500)

        # Bileşenler
        self.signal_filter = SignalFilter(config)
        self.signal_scorer = SignalScorer(config)
        self.ohlcv_fetcher = OHLCVFetcher(exchange, config)
        self.momentum_strategy = MomentumStrategy(config)

        # State
        self._running = False
        self._scan_thread: Optional[threading.Thread] = None
        self._listing_thread: Optional[threading.Thread] = None
        self._known_listings: Set[str] = set()
        self._scan_count = 0
        self._signal_count = 0

        # Sinyal kuyruğu (main loop için)
        self.signal_queue: Queue = Queue()

        # Stats
        self._stats: Dict = {
            "last_scan": None,
            "pairs_scanned": 0,
            "signals_generated": 0,
            "errors": 0,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Taramayı arka planda başlat."""
        if self._running:
            logger.warning("Scanner already running")
            return

        self._running = True
        logger.info(f"Scanner starting (interval={self.interval}s)")

        # Ana tarama thread'i
        self._scan_thread = threading.Thread(
            target=self._scan_loop,
            daemon=True,
            name="PingerScanner",
        )
        self._scan_thread.start()

        # Yeni listing thread'i
        self._listing_thread = threading.Thread(
            target=self._listing_loop,
            daemon=True,
            name="PingerListing",
        )
        self._listing_thread.start()

        logger.info("Scanner started successfully")

    def stop(self) -> None:
        """Taramayı durdur."""
        self._running = False
        logger.info("Scanner stopping...")

    def get_stats(self) -> dict:
        """Tarama istatistikleri."""
        return {
            **self._stats,
            "filter_stats": self.signal_filter.get_stats(),
            "scan_count": self._scan_count,
            "signal_count": self._signal_count,
        }

    # ------------------------------------------------------------------
    # Ana tarama döngüsü
    # ------------------------------------------------------------------

    def _scan_loop(self) -> None:
        """Periyodik tarama ana döngüsü (ayrı thread'de çalışır)."""
        while self._running:
            try:
                scan_start = time.time()
                self._run_single_scan()
                elapsed = time.time() - scan_start

                # Sonraki taramaya kadar bekle
                wait = max(0, self.interval - elapsed)
                if wait > 0:
                    time.sleep(wait)

            except Exception as e:
                logger.error(f"Scan loop error: {e}", exc_info=True)
                self._stats["errors"] += 1
                time.sleep(5)

    def _run_single_scan(self) -> None:
        """Tek bir tarama turu."""
        self._scan_count += 1
        scan_time = datetime.now()

        try:
            # Tüm USDT tickerları çek (tek API call - verimli)
            tickers = self._fetch_tickers()
            if not tickers:
                return

            # USDT spot çiftlerini filtrele
            usdt_pairs = [
                sym for sym in tickers
                if sym.endswith("/USDT") and "/" in sym
            ][:self.max_pairs]

            self._stats["pairs_scanned"] = len(usdt_pairs)
            logger.debug(f"Scan #{self._scan_count}: {len(usdt_pairs)} pairs")

            # Cooldown temizliği (her 10 turda bir)
            if self._scan_count % 10 == 0:
                self.signal_filter.cleanup_expired()

            # Her çifti analiz et
            for symbol in usdt_pairs:
                if not self._running:
                    break
                self._analyze_symbol(symbol, tickers[symbol])

            self._stats["last_scan"] = scan_time.isoformat()

        except ccxt.NetworkError as e:
            logger.error(f"Network error in scan: {e}")
            time.sleep(10)
        except ccxt.RateLimitExceeded:
            logger.warning("Rate limit exceeded, waiting 30s...")
            time.sleep(30)

    def _analyze_symbol(self, symbol: str, ticker: dict) -> None:
        """
        Tek bir sembolü analiz eder.
        Filtreler → OHLCV → Strateji → LSTM → Skor → Callback
        """
        try:
            volume_usdt = float(ticker.get("quoteVolume") or 0)

            # Hızlı ön-filtre (OHLCV çekmeden)
            should_skip, reason = self.signal_filter.should_skip(symbol, volume_usdt)
            if should_skip:
                logger.debug(f"Skip {symbol}: {reason}")
                return

            # Kaba fiyat değişimi kontrolü (ticker'dan)
            price_change = float(ticker.get("percentage") or 0)
            sig_cfg = self.config.get("signals", {})
            if abs(price_change) < sig_cfg.get("price_change_min", 1.5) * 0.5:
                # Çok küçük hareket, OHLCV çekmeye değmez
                return

            # OHLCV çek (1m, son 100 bar)
            ohlcv_df = self._fetch_ohlcv_safe(symbol, limit=100)

            # Strateji sinyalleri
            strategy_signals = []
            if ohlcv_df is not None and len(ohlcv_df) >= self.momentum_strategy.requires_min_bars():
                mom_signal = self.momentum_strategy.analyze(symbol, ohlcv_df)
                if mom_signal:
                    strategy_signals.append(mom_signal)

            # LSTM tahmin (model varsa)
            lstm_prob = None
            if self.lstm_trainer:
                lstm_prob = self.lstm_trainer.predict(symbol)

            # Final skor hesapla
            market_signal = self.signal_scorer.compute_score(
                symbol=symbol,
                ticker=ticker,
                ohlcv_df=ohlcv_df,
                lstm_prob=lstm_prob,
                strategy_signals=strategy_signals,
            )

            if market_signal is None:
                return

            # Sinyal filter (cooldown kaydet)
            self.signal_filter.record_signal(symbol)
            self._signal_count += 1
            self._stats["signals_generated"] = self._signal_count

            # Callback veya kuyruğa ekle
            if self.signal_callback:
                self.signal_callback(market_signal)
            else:
                self.signal_queue.put(market_signal)

        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")

    # ------------------------------------------------------------------
    # Yeni listing tarayıcı
    # ------------------------------------------------------------------

    def _listing_loop(self) -> None:
        """Binance yeni listing duyurularını periyodik kontrol eder."""
        # İlk yükleme - mevcut listing'leri baseline olarak kaydet
        self._known_listings = self._scrape_binance_listings()
        logger.info(f"Listing monitor started. Known listings: {len(self._known_listings)}")

        while self._running:
            try:
                time.sleep(300)  # 5 dakikada bir kontrol
                new_listings = self._scrape_binance_listings()
                new_symbols = new_listings - self._known_listings

                if new_symbols:
                    logger.info(f"New listings detected: {new_symbols}")
                    for sym in new_symbols:
                        self._handle_new_listing(sym)
                    self._known_listings = new_listings

            except Exception as e:
                logger.error(f"Listing loop error: {e}")
                time.sleep(60)

    def _scrape_binance_listings(self) -> set:
        """Binance yeni listing sayfasını scrape eder."""
        symbols = set()
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                )
            }
            resp = requests.get(BINANCE_LISTING_URL, headers=headers, timeout=10)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Coin sembollerini bul (genellikle başlıklarda parantez içinde)
            for link in soup.find_all("a", href=True):
                text = link.get_text(strip=True)
                # "Will List XYZ (XYZ)" formatını yakala
                if "(" in text and ")" in text:
                    start = text.rfind("(") + 1
                    end = text.rfind(")")
                    if start < end:
                        potential_sym = text[start:end].strip().upper()
                        if 2 <= len(potential_sym) <= 10 and potential_sym.isalpha():
                            symbols.add(f"{potential_sym}/USDT")

        except requests.RequestException as e:
            logger.warning(f"Binance listing scrape failed: {e}")
        except Exception as e:
            logger.error(f"Listing parse error: {e}")

        return symbols

    def _handle_new_listing(self, symbol: str) -> None:
        """Yeni listing tespitinde özel sinyal oluştur."""
        logger.info(f"NEW LISTING ALERT: {symbol}")
        listing_signal = MarketSignal(
            symbol=symbol,
            score=9,
            direction="long",
            signal_reasons=["NEW BINANCE LISTING", "High priority alert"],
        )
        listing_signal.price = 0.0
        listing_signal.volume_24h = 0.0

        if self.signal_callback:
            self.signal_callback(listing_signal)
        else:
            self.signal_queue.put(listing_signal)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_tickers(self) -> dict:
        """Tüm USDT tickerlarını çeker."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                tickers = self.exchange.fetch_tickers()
                return tickers
            except ccxt.RateLimitExceeded:
                logger.warning(f"Rate limit (attempt {attempt+1})")
                time.sleep(5 * (attempt + 1))
            except ccxt.NetworkError as e:
                logger.error(f"Network error fetching tickers: {e}")
                time.sleep(3)
            except Exception as e:
                logger.error(f"Unexpected ticker error: {e}")
                return {}
        return {}

    def _fetch_ohlcv_safe(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> Optional[object]:
        """OHLCV çeker, hata durumunda None döner."""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 30:
                return None

            import pandas as pd
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df

        except Exception as e:
            logger.debug(f"OHLCV fetch failed for {symbol}: {e}")
            return None
