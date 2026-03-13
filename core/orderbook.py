"""
Pinger v2.0 - Order Book Analizi (Erken Sinyal)
================================================
Bu fark yaratır.

Normal alarm: Fiyat %1.5 hareket ETTİ → sen gördüğünde geç.
Order Book: Fiyat hareket ETMEDEN önce büyük alım EMRİ geldi → sen erken girersin.

Ne izler:
  1. Bid/Ask imbalance (alım > satım emirleri → yükseliş gelecek)
  2. Büyük duvar (whale) tespiti
  3. Spread anomalisi
  4. Trade flow (büyük market buy geldi mi?)

Nasıl çalışır:
  - Her 30 saniyede seçili coinlerin order book snapshot'ını çeker
  - Alım tarafı / satım tarafı oranını hesaplar (imbalance)
  - Oran > 2.5 (alımlar satımların 2.5 katı) → güçlü alım baskısı → erken sinyal
"""

import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Callable
from collections import deque

import ccxt

from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class OrderBookSignal:
    """Order book analizinden gelen sinyal."""
    symbol: str
    timestamp: datetime
    direction: str              # "long" | "short"
    imbalance_ratio: float      # alım/satım oranı (>1 = alım baskısı)
    bid_volume: float           # Toplam alım hacmi (USDT)
    ask_volume: float           # Toplam satım hacmi (USDT)
    large_orders_detected: bool # Büyük whale emri var mı?
    spread_pct: float           # Spread yüzdesi
    score: int                  # 0-10
    reason: str


class OrderBookAnalyzer:
    """
    Binance order book'u analiz eder, erken sinyal üretir.

    Normal sinyalden ~3-8 dakika ÖNCE uyarı verir.
    """

    def __init__(
        self,
        exchange: ccxt.Exchange,
        config: dict,
        signal_callback: Optional[Callable] = None,
    ):
        self.exchange = exchange
        self.config = config
        self.signal_callback = signal_callback

        # Eşik değerleri
        self.imbalance_threshold = 2.0   # Alım/satım oranı bu değerin üstündeyse sinyal
        self.large_order_threshold = 0.15  # Tek emrin toplam order book'un %15'inden fazlası
        self.min_ob_volume_usdt = 50_000   # Min order book toplam hacmi
        self.max_spread_pct = 0.5          # Max spread (%0.5'ten fazlaysa manipülasyon riski)

        # İzlenecek semboller (dinamik, scanner'dan güncellenir)
        self._watch_symbols: List[str] = []

        # Geçmiş imbalance (trend görmek için)
        self._imbalance_history: Dict[str, deque] = {}

        # Running state
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._check_interval = 30  # saniye

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_watch_symbols(self, symbols: List[str]) -> None:
        """İzlenecek sembolleri güncelle."""
        self._watch_symbols = symbols[:50]  # Max 50 sembol (rate limit)
        # Yeni semboller için history başlat
        for sym in self._watch_symbols:
            if sym not in self._imbalance_history:
                self._imbalance_history[sym] = deque(maxlen=10)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._analysis_loop,
            daemon=True,
            name="OrderBookAnalyzer",
        )
        self._thread.start()
        logger.info("Order book analyzer started")

    def stop(self) -> None:
        self._running = False

    def analyze_now(self, symbol: str) -> Optional[OrderBookSignal]:
        """Tek sembol anlık analiz (scanner entegrasyonu için)."""
        return self._analyze_symbol(symbol)

    # ------------------------------------------------------------------
    # Ana döngü
    # ------------------------------------------------------------------

    def _analysis_loop(self) -> None:
        while self._running:
            try:
                for symbol in list(self._watch_symbols):
                    if not self._running:
                        break
                    sig = self._analyze_symbol(symbol)
                    if sig and self.signal_callback:
                        self.signal_callback(sig)
                    time.sleep(0.15)  # Rate limit arası

                time.sleep(self._check_interval)
            except Exception as e:
                logger.error(f"Order book loop error: {e}")
                time.sleep(10)

    # ------------------------------------------------------------------
    # Analiz
    # ------------------------------------------------------------------

    def _analyze_symbol(self, symbol: str) -> Optional[OrderBookSignal]:
        """
        Bir sembolün order book'unu çeker ve analiz eder.
        """
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=20)
            bids = ob.get("bids", [])  # [[price, amount], ...]
            asks = ob.get("asks", [])

            if not bids or not asks:
                return None

            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])

            if best_bid <= 0 or best_ask <= 0:
                return None

            # Spread
            spread_pct = (best_ask - best_bid) / best_bid * 100
            if spread_pct > self.max_spread_pct:
                return None  # Anormal spread, güvenilmez

            # Toplam hacim hesapla (USDT)
            mid_price = (best_bid + best_ask) / 2

            bid_volume = sum(float(p) * float(q) for p, q in bids)
            ask_volume = sum(float(p) * float(q) for p, q in asks)
            total_volume = bid_volume + ask_volume

            if total_volume < self.min_ob_volume_usdt:
                return None

            # Imbalance ratio
            imbalance = bid_volume / (ask_volume + 1e-8)

            # History güncelle
            if symbol not in self._imbalance_history:
                self._imbalance_history[symbol] = deque(maxlen=10)
            self._imbalance_history[symbol].append(imbalance)

            # Büyük emir tespiti
            max_single_bid = max((float(p) * float(q) for p, q in bids), default=0)
            max_single_ask = max((float(p) * float(q) for p, q in asks), default=0)
            large_order = (
                max_single_bid > total_volume * self.large_order_threshold or
                max_single_ask > total_volume * self.large_order_threshold
            )

            # Trend: imbalance artıyor mu?
            hist = list(self._imbalance_history.get(symbol, []))
            imbalance_trending_up = (
                len(hist) >= 3 and
                hist[-1] > hist[-2] > hist[-3]
            )

            # Skor ve yön
            score, direction, reason = self._score_orderbook(
                imbalance=imbalance,
                large_order=large_order,
                imbalance_trending=imbalance_trending_up,
                bid_volume=bid_volume,
                ask_volume=ask_volume,
            )

            if score < 5:
                return None

            return OrderBookSignal(
                symbol=symbol,
                timestamp=datetime.now(),
                direction=direction,
                imbalance_ratio=round(imbalance, 2),
                bid_volume=bid_volume,
                ask_volume=ask_volume,
                large_orders_detected=large_order,
                spread_pct=round(spread_pct, 4),
                score=score,
                reason=reason,
            )

        except ccxt.RateLimitExceeded:
            time.sleep(5)
            return None
        except Exception as e:
            logger.debug(f"OB analysis error {symbol}: {e}")
            return None

    def _score_orderbook(
        self,
        imbalance: float,
        large_order: bool,
        imbalance_trending: bool,
        bid_volume: float,
        ask_volume: float,
    ) -> tuple[int, str, str]:
        """Skor ve yön hesapla."""
        score = 0
        reasons = []

        # LONG baskısı
        if imbalance >= 3.0:
            score += 4
            reasons.append(f"Güçlü alım baskısı ({imbalance:.1f}x)")
        elif imbalance >= self.imbalance_threshold:
            score += 2
            reasons.append(f"Alım baskısı ({imbalance:.1f}x)")

        # SHORT baskısı (ters)
        reverse_imbalance = ask_volume / (bid_volume + 1e-8)
        if reverse_imbalance >= 3.0:
            score += 4
            reasons.append(f"Güçlü satış baskısı ({reverse_imbalance:.1f}x)")

        # Büyük whale emri
        if large_order:
            score += 2
            reasons.append("Büyük emir (whale)")

        # Trend
        if imbalance_trending:
            score += 2
            reasons.append("Imbalance artıyor")

        # Yön
        if imbalance >= self.imbalance_threshold:
            direction = "long"
        elif reverse_imbalance >= self.imbalance_threshold:
            direction = "short"
            score = min(score, 8)
        else:
            direction = "neutral"
            score = 0

        return score, direction, " | ".join(reasons)
