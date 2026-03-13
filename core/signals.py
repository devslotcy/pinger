"""
Pinger v2.0 - Sinyal Hesaplama & Skorlama
Tüm kaynakları birleştirir: hacim spike, fiyat momentum,
LSTM tahmin, momentum stratejisi → final skor 1-9.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

from strategies.base import StrategySignal
from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class MarketSignal:
    """
    Bir coin için birleştirilmiş piyasa sinyali.
    Tüm kaynaklardan gelen bilgileri içerir.
    """
    symbol: str
    timestamp: datetime = field(default_factory=datetime.now)

    # Fiyat bilgisi
    price: float = 0.0
    price_change_15m: float = 0.0   # % değişim
    price_change_1h: float = 0.0
    volume_24h: float = 0.0

    # Ham metrikler
    volume_ratio: float = 1.0       # Mevcut hacim / MA hacim
    volume_spike: bool = False

    # AI tahmin
    lstm_probability: Optional[float] = None   # 0.0-1.0
    lstm_trained: bool = False

    # Strateji sinyalleri
    strategy_signals: List[StrategySignal] = field(default_factory=list)

    # Final skor
    score: int = 0                  # 1-9
    direction: str = "neutral"      # long | short | neutral
    signal_reasons: List[str] = field(default_factory=list)

    # Risk parametreleri
    take_profit_pct: float = 10.0
    stop_loss_pct: float = 7.0
    risk_reward: float = 0.0

    @property
    def score_emoji(self) -> str:
        if self.score >= 7:
            return "🟢"
        elif self.score >= 4:
            return "🟡"
        else:
            return "🔴"

    @property
    def direction_emoji(self) -> str:
        if self.direction == "long":
            return "📈"
        elif self.direction == "short":
            return "📉"
        return "➡️"


class SignalScorer:
    """
    Ham market verisi + strateji sinyallerini alır,
    final skor ve yön hesaplar.
    """

    def __init__(self, config: dict):
        sig_cfg = config.get("signals", {})
        risk_cfg = config.get("risk", {})

        self.vol_spike_mult = sig_cfg.get("volume_spike_multiplier", 1.2)
        self.price_change_min = sig_cfg.get("price_change_min", 1.5)
        self.price_change_max = sig_cfg.get("price_change_max", 15.0)
        self.min_score = sig_cfg.get("min_signal_score", 5)

        self.tp_pct = risk_cfg.get("take_profit_pct", 10.0)
        self.sl_pct = risk_cfg.get("stop_loss_pct", 7.0)

    def compute_score(
        self,
        symbol: str,
        ticker: dict,
        ohlcv_df: Optional[pd.DataFrame],
        lstm_prob: Optional[float],
        strategy_signals: List[StrategySignal],
    ) -> Optional[MarketSignal]:
        """
        Tüm girdileri alır, birleştirilmiş MarketSignal döndürür.

        Args:
            symbol: Örn "BTC/USDT"
            ticker: CCXT ticker dict (last, quoteVolume, percentage, vb.)
            ohlcv_df: OHLCV DataFrame (None olabilir)
            lstm_prob: LSTM fiyat artış olasılığı (None olabilir)
            strategy_signals: Strateji sinyalleri listesi

        Returns:
            MarketSignal veya None (minimum skor sağlanmadıysa)
        """
        try:
            signal = MarketSignal(symbol=symbol)

            # Temel fiyat/hacim bilgileri
            signal.price = float(ticker.get("last") or ticker.get("close") or 0)
            signal.volume_24h = float(ticker.get("quoteVolume") or 0)
            signal.price_change_15m = self._extract_price_change(ticker, ohlcv_df)

            # 1. Hacim spike skoru (0-2)
            vol_score, vol_ratio = self._score_volume(ticker, ohlcv_df)
            signal.volume_ratio = vol_ratio
            signal.volume_spike = vol_ratio >= self.vol_spike_mult

            # 2. Fiyat momentum skoru (0-2)
            price_score, price_direction = self._score_price_movement(
                signal.price_change_15m,
                ohlcv_df,
            )

            # 3. LSTM skoru (0-2)
            lstm_score, lstm_direction = self._score_lstm(lstm_prob)
            signal.lstm_probability = lstm_prob
            signal.lstm_trained = lstm_prob is not None

            # 4. Strateji skoru (0-3)
            strategy_score, strat_direction, strat_reasons = self._score_strategies(strategy_signals)
            signal.strategy_signals = strategy_signals

            # --------------------------------------------------------
            # Yön belirleme (oy birliği)
            # --------------------------------------------------------
            directions = []
            if price_direction:
                directions.append(price_direction)
            if lstm_direction:
                directions.append(lstm_direction)
            if strat_direction:
                directions.append(strat_direction)

            long_votes = directions.count("long")
            short_votes = directions.count("short")

            if long_votes > short_votes:
                signal.direction = "long"
            elif short_votes > long_votes:
                signal.direction = "short"
            else:
                signal.direction = "neutral"

            # Nötr ise erken çık
            if signal.direction == "neutral" and not signal.volume_spike:
                return None

            # --------------------------------------------------------
            # Final skor
            # --------------------------------------------------------
            raw_score = vol_score + price_score + lstm_score + strategy_score

            # Bonus: tüm göstergeler aynı yönü gösteriyorsa
            if len(set(directions)) == 1 and len(directions) >= 2:
                raw_score += 1

            signal.score = max(1, min(9, raw_score))

            # Minimum skor kontrolü
            if signal.score < self.min_score:
                logger.debug(
                    f"[Scorer] {symbol}: score {signal.score} < min {self.min_score}, skip"
                )
                return None

            # Risk/Reward
            signal.take_profit_pct = self.tp_pct
            signal.stop_loss_pct = self.sl_pct
            signal.risk_reward = self.tp_pct / self.sl_pct

            # Açıklamalar
            reasons = []
            if signal.volume_spike:
                reasons.append(f"Vol spike x{vol_ratio:.1f}")
            if abs(signal.price_change_15m) > 0:
                reasons.append(f"Price {signal.price_change_15m:+.2f}%")
            if lstm_prob is not None:
                reasons.append(f"AI {lstm_prob*100:.0f}%")
            reasons.extend(strat_reasons[:3])  # Max 3 strateji nedeni
            signal.signal_reasons = reasons

            logger.info(
                f"[Scorer] {symbol}: score={signal.score} dir={signal.direction} "
                f"lstm={f'{lstm_prob:.2f}' if lstm_prob is not None else 'N/A'} "
                f"reasons={reasons}"
            )
            return signal

        except Exception as e:
            logger.error(f"[Scorer] Error computing score for {symbol}: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Alt scorer'lar
    # ------------------------------------------------------------------

    def _score_volume(
        self,
        ticker: dict,
        df: Optional[pd.DataFrame],
    ) -> tuple[int, float]:
        """Hacim skoru (0-2) ve ratio döndürür."""
        try:
            curr_vol = float(ticker.get("baseVolume") or 0)

            if df is not None and "volume" in df.columns and len(df) >= 20:
                avg_vol = df["volume"].iloc[-20:-1].mean()
                last_vol = df["volume"].iloc[-1]
                ratio = last_vol / (avg_vol + 1e-8)
            else:
                ratio = 1.0

            if ratio >= 3.0:
                return 2, ratio
            elif ratio >= self.vol_spike_mult:
                return 1, ratio
            else:
                return 0, ratio

        except Exception:
            return 0, 1.0

    def _score_price_movement(
        self,
        price_change_15m: float,
        df: Optional[pd.DataFrame],
    ) -> tuple[int, Optional[str]]:
        """Fiyat hareketi skoru (0-2) ve yön döndürür."""
        abs_change = abs(price_change_15m)

        # Pump filtresi: %15'ten fazlası manipülasyon olabilir
        if abs_change > self.price_change_max:
            return 0, None

        if abs_change < self.price_change_min:
            return 0, None

        direction = "long" if price_change_15m > 0 else "short"

        if abs_change >= 5.0:
            return 2, direction
        elif abs_change >= self.price_change_min:
            return 1, direction

        return 0, None

    def _score_lstm(
        self,
        lstm_prob: Optional[float],
    ) -> tuple[int, Optional[str]]:
        """LSTM skoru (0-2) ve yön döndürür."""
        if lstm_prob is None:
            return 0, None

        if lstm_prob >= 0.75:
            return 2, "long"
        elif lstm_prob >= 0.60:
            return 1, "long"
        elif lstm_prob <= 0.25:
            return 2, "short"
        elif lstm_prob <= 0.40:
            return 1, "short"

        return 0, None

    def _score_strategies(
        self,
        signals: List[StrategySignal],
    ) -> tuple[int, Optional[str], List[str]]:
        """Strateji sinyalleri skoru (0-3), yön, nedenler."""
        if not signals:
            return 0, None, []

        total_score = 0
        directions = []
        reasons = []

        for sig in signals:
            # Her strateji max 3 puan katkı
            contrib = min(sig.score, 3)
            total_score += contrib
            if sig.direction in ("long", "short"):
                directions.append(sig.direction)
            reasons.append(f"{sig.strategy_name}: {sig.reason[:60]}")

        # Hakim yön
        long_votes = directions.count("long")
        short_votes = directions.count("short")
        dominant = "long" if long_votes >= short_votes else "short"
        if long_votes == 0 and short_votes == 0:
            dominant = None

        # Cap at 3
        final = min(total_score, 3)
        return final, dominant, reasons

    def _extract_price_change(
        self,
        ticker: dict,
        df: Optional[pd.DataFrame],
    ) -> float:
        """15 dakikalık fiyat değişimini hesaplar."""
        try:
            # CCXT ticker'dan direct percentage varsa kullan
            pct = ticker.get("percentage")
            if pct is not None:
                return float(pct)

            # DataFrame'den hesapla
            if df is not None and len(df) >= 15:
                close_now = df["close"].iloc[-1]
                close_15m_ago = df["close"].iloc[-16]
                if close_15m_ago > 0:
                    return float((close_now - close_15m_ago) / close_15m_ago * 100)

        except Exception:
            pass
        return 0.0
