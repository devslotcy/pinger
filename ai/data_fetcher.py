"""
Pinger v2.0 - Historical OHLCV Data Fetcher
CCXT ile geçmiş veri çeker, cache'ler, normalize eder.
LSTM eğitimi + canlı tahmin için kullanılır.
"""

import time
import json
import hashlib
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import ccxt

from utils.logger import setup_logger

logger = setup_logger(__name__)

# Disk cache dizini
CACHE_DIR = Path("data/ohlcv_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class OHLCVFetcher:
    """
    Binance'ten OHLCV verisi çeker.
    - Rate limit yönetimi
    - Disk cache (JSON)
    - DataFrame dönüşümü
    - Feature engineering (returns, log_volume, vb.)
    """

    def __init__(self, exchange: ccxt.Exchange, config: dict):
        self.exchange = exchange
        self.cfg = config.get("lstm", {})
        self.timeframe = self.cfg.get("timeframe", "1m")
        self.train_days = self.cfg.get("train_days", 90)
        self.rate_delay = config.get("exchange", {}).get("rate_limit_delay", 0.1)

        # CCXT rate limit
        self.exchange.enableRateLimit = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_training_data(
        self,
        symbol: str,
        days: Optional[int] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Eğitim için geçmiş OHLCV verisini çeker.

        Args:
            symbol: Örn "BTC/USDT"
            days: Kaç günlük (None → config'den)
            use_cache: Disk cache kullan

        Returns:
            DataFrame: [timestamp, open, high, low, close, volume] + feature kolonları
        """
        days = days or self.train_days
        cache_key = self._cache_key(symbol, self.timeframe, days)
        cache_path = CACHE_DIR / f"{cache_key}.json"

        # Cache kontrolü (24 saatten eski değilse kullan)
        if use_cache and cache_path.exists():
            age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
            if age_hours < 24:
                logger.debug(f"Cache hit: {symbol} ({age_hours:.1f}h old)")
                return self._load_from_cache(cache_path)

        logger.info(f"Fetching {days}d OHLCV for {symbol} ({self.timeframe})")
        df = self._fetch_paginated(symbol, days)

        if df.empty:
            logger.warning(f"No data for {symbol}")
            return df

        df = self._add_features(df)

        if use_cache:
            self._save_to_cache(df, cache_path)

        return df

    def fetch_live_sequence(
        self,
        symbol: str,
        seq_len: int,
    ) -> Optional[pd.DataFrame]:
        """
        Canlı tahmin için son seq_len bar'ı çeker.

        Args:
            symbol: Örn "ETH/USDT"
            seq_len: Kaç bar (örn 60)

        Returns:
            DataFrame veya None (hata durumunda)
        """
        try:
            # Son seq_len + buffer kadar bar çek
            fetch_limit = seq_len + 10
            ohlcv = self.exchange.fetch_ohlcv(
                symbol,
                timeframe=self.timeframe,
                limit=fetch_limit,
            )
            if not ohlcv or len(ohlcv) < seq_len:
                logger.warning(f"Insufficient live bars for {symbol}: {len(ohlcv)}")
                return None

            df = self._to_dataframe(ohlcv)
            df = self._add_features(df)
            return df.tail(seq_len)

        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching live data for {symbol}: {e}")
            return None
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error for {symbol}: {e}")
            return None

    # ------------------------------------------------------------------
    # Feature Engineering
    # ------------------------------------------------------------------

    def _add_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ham OHLCV'ye teknik feature'lar ekler.
        LSTM için normalize edilmiş giriş kolonları.
        """
        df = df.copy()

        # Temel return'ler
        df["returns"] = df["close"].pct_change()
        df["log_returns"] = np.log(df["close"] / df["close"].shift(1))

        # Fiyat normalizasyonu (pencere içi)
        df["close_norm"] = df["close"] / df["close"].rolling(20).mean()
        df["high_norm"] = df["high"] / df["close"].rolling(20).mean()
        df["low_norm"] = df["low"] / df["close"].rolling(20).mean()

        # Hacim normalizasyonu
        vol_ma = df["volume"].rolling(20).mean()
        df["volume_ratio"] = df["volume"] / (vol_ma + 1e-8)
        df["log_volume"] = np.log(df["volume"] + 1)

        # Fiyat aralığı (volatilite proxy)
        df["hl_range"] = (df["high"] - df["low"]) / (df["close"] + 1e-8)

        # Momentum (close vs N bar önce)
        for n in [5, 10, 20]:
            df[f"mom_{n}"] = df["close"].pct_change(n)

        # Hacim trendi
        df["vol_trend"] = df["volume"].pct_change(5)

        # NaN temizle
        df.dropna(inplace=True)
        df.reset_index(drop=True, inplace=True)

        return df

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_paginated(self, symbol: str, days: int) -> pd.DataFrame:
        """
        CCXT limit (1000 bar/istek) aşıldığında sayfalı çeker.
        """
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        since_ms = int(since_dt.timestamp() * 1000)

        all_ohlcv = []
        max_retries = 3

        while True:
            for attempt in range(max_retries):
                try:
                    batch = self.exchange.fetch_ohlcv(
                        symbol,
                        timeframe=self.timeframe,
                        since=since_ms,
                        limit=1000,
                    )
                    break
                except ccxt.RateLimitExceeded:
                    wait = 2 ** attempt * 5
                    logger.warning(f"Rate limit hit, waiting {wait}s...")
                    time.sleep(wait)
                except ccxt.NetworkError as e:
                    logger.error(f"Network error (attempt {attempt+1}): {e}")
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    return pd.DataFrame()
            else:
                logger.error(f"Max retries exceeded for {symbol}")
                break

            if not batch:
                break

            all_ohlcv.extend(batch)
            last_ts = batch[-1][0]

            # Tüm veri geldiyse dur
            if len(batch) < 1000:
                break

            # Sonraki sayfa
            since_ms = last_ts + 1
            time.sleep(self.rate_delay)

        if not all_ohlcv:
            return pd.DataFrame()

        return self._to_dataframe(all_ohlcv)

    def _to_dataframe(self, ohlcv: list) -> pd.DataFrame:
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates(subset="timestamp")
        df = df.sort_values("timestamp")
        df = df.reset_index(drop=True)
        # Float'a çevir
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df

    def _cache_key(self, symbol: str, timeframe: str, days: int) -> str:
        raw = f"{symbol}_{timeframe}_{days}"
        return hashlib.md5(raw.encode()).hexdigest()[:12] + f"_{symbol.replace('/', '_')}"

    def _save_to_cache(self, df: pd.DataFrame, path: Path) -> None:
        try:
            data = df.copy()
            data["timestamp"] = data["timestamp"].astype(str)
            path.write_text(json.dumps(data.to_dict(orient="records"), indent=2))
            logger.debug(f"Cached {len(df)} rows to {path.name}")
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def _load_from_cache(self, path: Path) -> pd.DataFrame:
        try:
            records = json.loads(path.read_text())
            df = pd.DataFrame(records)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            return df
        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
            return pd.DataFrame()


# ------------------------------------------------------------------
# Normalization helpers (LSTM input için)
# ------------------------------------------------------------------

FEATURE_COLS = [
    "close_norm", "high_norm", "low_norm",
    "volume_ratio", "log_volume",
    "hl_range", "returns", "log_returns",
    "mom_5", "mom_10", "mom_20",
    "vol_trend",
]


def prepare_sequences(
    df: pd.DataFrame,
    seq_len: int,
    target_col: str = "returns",
    predict_bars: int = 30,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    DataFrame'i LSTM için (X, y) numpy array'lerine dönüştürür.

    Args:
        df: Feature'lı DataFrame
        seq_len: Girdi uzunluğu (60 bar)
        target_col: Tahmin edilecek değişken
        predict_bars: Kaç bar ilerisi için tahmin

    Returns:
        X shape: (N, seq_len, n_features)
        y shape: (N,) — binary (1: pozitif getiri, 0: negatif)
    """
    # Sadece mevcut feature kolonlarını al
    available_features = [c for c in FEATURE_COLS if c in df.columns]
    feature_data = df[available_features].values.astype(np.float32)

    # Hedef: predict_bars sonraki close'un şimdikinden yüksek olup olmadığı
    future_returns = df["close"].pct_change(predict_bars).shift(-predict_bars).values

    X, y = [], []
    for i in range(len(df) - seq_len - predict_bars):
        X.append(feature_data[i : i + seq_len])
        # Binary classification: fiyat artacak mı?
        y.append(1 if future_returns[i + seq_len] > 0 else 0)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def normalize_sequence(seq: np.ndarray) -> np.ndarray:
    """
    Tek bir sekansı min-max normalize eder.
    NaN/Inf temizler.
    """
    seq = np.where(np.isfinite(seq), seq, 0.0)
    min_vals = seq.min(axis=0, keepdims=True)
    max_vals = seq.max(axis=0, keepdims=True)
    range_vals = max_vals - min_vals + 1e-8
    return (seq - min_vals) / range_vals
