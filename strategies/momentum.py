"""
Pinger v2.0 - Momentum Stratejisi
RSI + MACD + Hacim Onayı ile trend yakalama.

Mantık:
  LONG sinyal:
    - RSI < oversold (30) ve yukarı döndü (son bar artış)
    - MACD histogram pozitife döndü (crossover)
    - Hacim spike (1.2x ortalama)
    - Son 3 bar'da fiyat artışı var

  SHORT sinyal (futures için):
    - RSI > overbought (70) ve aşağı döndü
    - MACD histogram negatife döndü
    - Hacim spike
    - Son 3 bar'da fiyat düşüşü var

  Skor hesaplama (0-10):
    - RSI aşırı zone: +2
    - MACD crossover: +2
    - Hacim spike: +2
    - Fiyat momentum: +2
    - RSI divergence (isteğe bağlı): +2
"""

import numpy as np
import pandas as pd
from typing import Optional

from strategies.base import BaseStrategy, StrategySignal
from utils.logger import setup_logger

logger = setup_logger(__name__)


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI hesaplar."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder smoothing (EMA ile)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD hesaplar.
    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


class MomentumStrategy(BaseStrategy):
    """
    RSI + MACD + Hacim tabanlı momentum stratejisi.
    Hem spot (long only) hem futures (long+short) için çalışır.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        mom_cfg = config.get("momentum", {})

        self.rsi_period = mom_cfg.get("rsi_period", 14)
        self.rsi_oversold = mom_cfg.get("rsi_oversold", 30)
        self.rsi_overbought = mom_cfg.get("rsi_overbought", 70)
        self.macd_fast = mom_cfg.get("macd_fast", 12)
        self.macd_slow = mom_cfg.get("macd_slow", 26)
        self.macd_signal_period = mom_cfg.get("macd_signal", 9)
        self.min_score = mom_cfg.get("min_momentum_score", 6)

        vol_spike = config.get("signals", {}).get("volume_spike_multiplier", 1.2)
        self.vol_spike_threshold = vol_spike

    def requires_min_bars(self) -> int:
        return max(self.macd_slow + self.macd_signal_period + 5, self.rsi_period + 5)

    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[StrategySignal]:
        """
        Momentum analizi yap.

        Args:
            symbol: Örn "SOL/USDT"
            df: En az 50 bar OHLCV DataFrame

        Returns:
            StrategySignal veya None
        """
        if len(df) < self.requires_min_bars():
            logger.debug(f"[Momentum] {symbol}: Not enough bars ({len(df)})")
            return None

        try:
            # Indikatörler hesapla
            rsi = compute_rsi(df["close"], self.rsi_period)
            macd_line, signal_line, histogram = compute_macd(
                df["close"],
                self.macd_fast,
                self.macd_slow,
                self.macd_signal_period,
            )

            # Son bar değerleri
            curr_rsi = rsi.iloc[-1]
            prev_rsi = rsi.iloc[-2]
            curr_hist = histogram.iloc[-1]
            prev_hist = histogram.iloc[-2]
            curr_macd = macd_line.iloc[-1]
            curr_signal = signal_line.iloc[-1]

            # Hacim kontrolü
            vol_ratio = self._volume_ratio(df)

            # Son N bar fiyat trendi
            price_trend_3 = self._price_trend(df, n=3)
            price_trend_5 = self._price_trend(df, n=5)

            # --------------------------------------------------------
            # LONG sinyali
            # --------------------------------------------------------
            if self._is_long_setup(
                curr_rsi, prev_rsi,
                curr_hist, prev_hist,
                curr_macd, curr_signal,
                vol_ratio, price_trend_3,
            ):
                score, reasons = self._score_long(
                    curr_rsi, prev_rsi,
                    curr_hist, prev_hist,
                    vol_ratio,
                    price_trend_3, price_trend_5,
                )
                if score >= self.min_score:
                    return StrategySignal(
                        strategy_name="Momentum",
                        symbol=symbol,
                        direction="long",
                        score=min(score, 10),
                        confidence=min(score / 10.0, 1.0),
                        reason=" | ".join(reasons),
                        indicators={
                            "rsi": round(curr_rsi, 2),
                            "rsi_prev": round(prev_rsi, 2),
                            "macd": round(curr_macd, 6),
                            "macd_signal": round(curr_signal, 6),
                            "macd_hist": round(curr_hist, 6),
                            "macd_hist_prev": round(prev_hist, 6),
                            "volume_ratio": round(vol_ratio, 2),
                            "price_trend_3": round(price_trend_3, 3),
                        },
                    )

            # --------------------------------------------------------
            # SHORT sinyali (futures kullanımı için)
            # --------------------------------------------------------
            elif self._is_short_setup(
                curr_rsi, prev_rsi,
                curr_hist, prev_hist,
                curr_macd, curr_signal,
                vol_ratio, price_trend_3,
            ):
                score, reasons = self._score_short(
                    curr_rsi, prev_rsi,
                    curr_hist, prev_hist,
                    vol_ratio,
                    price_trend_3, price_trend_5,
                )
                if score >= self.min_score:
                    return StrategySignal(
                        strategy_name="Momentum",
                        symbol=symbol,
                        direction="short",
                        score=min(score, 10),
                        confidence=min(score / 10.0, 1.0),
                        reason=" | ".join(reasons),
                        indicators={
                            "rsi": round(curr_rsi, 2),
                            "rsi_prev": round(prev_rsi, 2),
                            "macd": round(curr_macd, 6),
                            "macd_hist": round(curr_hist, 6),
                            "volume_ratio": round(vol_ratio, 2),
                            "price_trend_3": round(price_trend_3, 3),
                        },
                    )

        except Exception as e:
            logger.error(f"[Momentum] {symbol} analysis error: {e}", exc_info=True)

        return None

    # ------------------------------------------------------------------
    # Koşul kontrolleri
    # ------------------------------------------------------------------

    def _is_long_setup(
        self,
        curr_rsi, prev_rsi,
        curr_hist, prev_hist,
        curr_macd, curr_signal,
        vol_ratio, price_trend,
    ) -> bool:
        """Temel LONG koşulları."""
        rsi_oversold_zone = curr_rsi < (self.rsi_oversold + 15)  # 30-45 arası
        rsi_turning_up = curr_rsi > prev_rsi                      # RSI yukarı dönüyor
        macd_bullish = curr_hist > prev_hist                       # Histogram artıyor
        price_positive = price_trend > 0                          # Fiyat yukarı

        # En az 3/4 koşul sağlanmalı
        conditions = [rsi_oversold_zone, rsi_turning_up, macd_bullish, price_positive]
        return sum(conditions) >= 3

    def _is_short_setup(
        self,
        curr_rsi, prev_rsi,
        curr_hist, prev_hist,
        curr_macd, curr_signal,
        vol_ratio, price_trend,
    ) -> bool:
        """Temel SHORT koşulları."""
        rsi_overbought_zone = curr_rsi > (self.rsi_overbought - 15)  # 55-70+ arası
        rsi_turning_down = curr_rsi < prev_rsi
        macd_bearish = curr_hist < prev_hist
        price_negative = price_trend < 0

        conditions = [rsi_overbought_zone, rsi_turning_down, macd_bearish, price_negative]
        return sum(conditions) >= 3

    # ------------------------------------------------------------------
    # Skor hesaplama
    # ------------------------------------------------------------------

    def _score_long(
        self,
        curr_rsi, prev_rsi,
        curr_hist, prev_hist,
        vol_ratio,
        price_trend_3, price_trend_5,
    ) -> tuple[int, list]:
        score = 0
        reasons = []

        # RSI aşırı satım bölgesi
        if curr_rsi < self.rsi_oversold:
            score += 3
            reasons.append(f"RSI oversold ({curr_rsi:.1f})")
        elif curr_rsi < self.rsi_oversold + 10:
            score += 1
            reasons.append(f"RSI near oversold ({curr_rsi:.1f})")

        # RSI yukarı döndü
        if curr_rsi > prev_rsi:
            score += 1
            reasons.append("RSI turning up")

        # MACD bullish crossover (histogram negatiften pozitife geçti)
        if prev_hist < 0 < curr_hist:
            score += 3
            reasons.append("MACD bullish crossover")
        elif curr_hist > prev_hist and curr_hist > 0:
            score += 2
            reasons.append("MACD histogram expanding")
        elif curr_hist > prev_hist:
            score += 1
            reasons.append("MACD histogram improving")

        # Hacim spike
        if vol_ratio >= 2.0:
            score += 2
            reasons.append(f"Volume spike x{vol_ratio:.1f}")
        elif vol_ratio >= self.vol_spike_threshold:
            score += 1
            reasons.append(f"Volume up x{vol_ratio:.1f}")

        # Fiyat momentum
        if price_trend_3 > 0.02:
            score += 2
            reasons.append(f"Strong 3-bar momentum +{price_trend_3*100:.1f}%")
        elif price_trend_3 > 0:
            score += 1
            reasons.append(f"Positive momentum +{price_trend_3*100:.2f}%")

        return score, reasons

    def _score_short(
        self,
        curr_rsi, prev_rsi,
        curr_hist, prev_hist,
        vol_ratio,
        price_trend_3, price_trend_5,
    ) -> tuple[int, list]:
        score = 0
        reasons = []

        if curr_rsi > self.rsi_overbought:
            score += 3
            reasons.append(f"RSI overbought ({curr_rsi:.1f})")
        elif curr_rsi > self.rsi_overbought - 10:
            score += 1
            reasons.append(f"RSI near overbought ({curr_rsi:.1f})")

        if curr_rsi < prev_rsi:
            score += 1
            reasons.append("RSI turning down")

        if prev_hist > 0 > curr_hist:
            score += 3
            reasons.append("MACD bearish crossover")
        elif curr_hist < prev_hist and curr_hist < 0:
            score += 2
            reasons.append("MACD histogram expanding (bearish)")
        elif curr_hist < prev_hist:
            score += 1
            reasons.append("MACD histogram deteriorating")

        if vol_ratio >= 2.0:
            score += 2
            reasons.append(f"Volume spike x{vol_ratio:.1f}")
        elif vol_ratio >= self.vol_spike_threshold:
            score += 1
            reasons.append(f"Volume up x{vol_ratio:.1f}")

        if price_trend_3 < -0.02:
            score += 2
            reasons.append(f"Strong 3-bar drop {price_trend_3*100:.1f}%")
        elif price_trend_3 < 0:
            score += 1
            reasons.append(f"Negative momentum {price_trend_3*100:.2f}%")

        return score, reasons

    # ------------------------------------------------------------------
    # Yardımcı metodlar
    # ------------------------------------------------------------------

    def _volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        """Mevcut hacmin MA'ya oranı."""
        vol = df["volume"]
        if len(vol) < window:
            return 1.0
        avg = vol.iloc[-window:-1].mean()
        if avg == 0:
            return 1.0
        return float(vol.iloc[-1] / avg)

    def _price_trend(self, df: pd.DataFrame, n: int = 3) -> float:
        """Son n bar'ın toplam getirisi."""
        if len(df) <= n:
            return 0.0
        start_price = df["close"].iloc[-(n + 1)]
        end_price = df["close"].iloc[-1]
        if start_price == 0:
            return 0.0
        return float((end_price - start_price) / start_price)
