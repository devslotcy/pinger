"""
Pinger v2.0 - Strateji Base Class
Tüm stratejiler bu class'tan türetilir.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class StrategySignal:
    """Bir stratejinin ürettiği sinyal."""
    strategy_name: str
    symbol: str
    direction: str              # "long" | "short" | "neutral"
    score: int                  # 0-10 arası katkı skoru
    confidence: float           # 0.0-1.0
    reason: str                 # İnsan okunabilir açıklama
    indicators: dict = field(default_factory=dict)  # RSI, MACD değerleri vb.

    @property
    def is_bullish(self) -> bool:
        return self.direction == "long"

    @property
    def is_bearish(self) -> bool:
        return self.direction == "short"


class BaseStrategy(ABC):
    """
    Tüm strateji sınıflarının temel class'ı.
    Her strateji analyze() metodunu implement etmeli.
    """

    def __init__(self, config: dict):
        self.config = config
        self.name = self.__class__.__name__

    @abstractmethod
    def analyze(self, symbol: str, df: pd.DataFrame) -> Optional[StrategySignal]:
        """
        DataFrame üzerinde strateji analizi yapar.

        Args:
            symbol: Örn "BTC/USDT"
            df: OHLCV + feature kolonları içeren DataFrame

        Returns:
            StrategySignal veya None (sinyal yok)
        """
        pass

    def requires_min_bars(self) -> int:
        """Strateji için gereken minimum bar sayısı."""
        return 50
