"""
Pinger v2.0 - Backtesting Motoru
==================================
Ne yapar:
  - CCXT ile geçmiş OHLCV verisini çeker (90 gün)
  - Mevcut sinyal kurallarını o geçmiş veriye uygular
  - Her sinyalin TP/SL'e ulaşıp ulaşmadığını kontrol eder
  - Win rate, ROI, max drawdown, Sharpe ratio hesaplar
  - Telegram'a güzel bir rapor gönderir

Kullanım:
  from backtest.engine import Backtester
  bt = Backtester(exchange, config)
  result = bt.run("BTC/USDT", days=90)
  bt.run_all_top_symbols(n=20)  # En hacimli 20 coin
"""

import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple

import ccxt

from strategies.momentum import MomentumStrategy, compute_rsi, compute_macd
from utils.logger import setup_logger

logger = setup_logger(__name__)


# ======================================================================
# Veri yapıları
# ======================================================================

@dataclass
class BacktestTrade:
    """Tek bir backtest işlemi."""
    symbol: str
    entry_time: datetime
    entry_price: float
    direction: str          # long / short
    tp_price: float
    sl_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""   # "TP" | "SL" | "TIMEOUT"
    pnl_pct: float = 0.0
    won: bool = False
    signal_score: int = 0
    signal_reason: str = ""


@dataclass
class BacktestResult:
    """Tek bir sembol için backtest sonucu."""
    symbol: str
    timeframe: str
    days: int
    total_signals: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    timeout_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    total_roi_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    def summary(self) -> str:
        """Kısa özet string."""
        return (
            f"{self.symbol} | {self.total_trades} işlem | "
            f"WR:{self.win_rate:.0f}% | ROI:{self.total_roi_pct:+.1f}% | "
            f"DD:{self.max_drawdown_pct:.1f}%"
        )


@dataclass
class PortfolioResult:
    """Çok sembol backtest toplam sonucu."""
    symbol_results: List[BacktestResult] = field(default_factory=list)
    total_trades: int = 0
    overall_win_rate: float = 0.0
    best_symbol: str = ""
    worst_symbol: str = ""
    avg_roi_per_trade: float = 0.0
    total_roi_pct: float = 0.0


# ======================================================================
# Ana Backtesting Motoru
# ======================================================================

class Backtester:
    """
    Geçmiş veri üzerinde strateji testi.

    Nasıl çalışır:
      1. Sembol için 90 günlük 5 dakikalık mum verisi çeker
      2. Her mumda sinyalleri simüle eder (momentum RSI+MACD + hacim spike)
      3. Sinyal geldiğinde sanal pozisyon açar
      4. Sonraki mumlarda TP/SL kontrolü yapar
      5. İstatistikleri hesaplar
    """

    def __init__(self, exchange: ccxt.Exchange, config: dict):
        self.exchange = exchange
        self.config = config

        risk_cfg = config.get("risk", {})
        self.tp_pct = risk_cfg.get("take_profit_pct", 10.0) / 100
        self.sl_pct = risk_cfg.get("stop_loss_pct", 7.0) / 100

        sig_cfg = config.get("signals", {})
        self.vol_spike_mult = sig_cfg.get("volume_spike_multiplier", 1.2)
        self.price_change_min = sig_cfg.get("price_change_min", 1.5)
        self.min_score = sig_cfg.get("min_signal_score", 5)

        self.momentum_strategy = MomentumStrategy(config)

        # Timeout: TP/SL'e ulaşmazsa kaç bar sonra çık
        self.timeout_bars = 72  # 5m * 72 = 6 saat

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        symbol: str,
        days: int = 90,
        timeframe: str = "5m",
    ) -> Optional[BacktestResult]:
        """
        Tek sembol backtest.

        Args:
            symbol: Örn "BTC/USDT"
            days: Kaç günlük geçmiş veri
            timeframe: Mum aralığı (5m önerilir)

        Returns:
            BacktestResult veya None
        """
        logger.info(f"Backtesting {symbol} | {days} gün | {timeframe}")

        # Veri çek
        df = self._fetch_data(symbol, days, timeframe)
        if df is None or len(df) < 200:
            logger.warning(f"{symbol}: Yeterli veri yok ({len(df) if df is not None else 0} bar)")
            return None

        # İndikatörleri hesapla
        df = self._add_indicators(df)

        # Sinyalleri tara
        trades = self._simulate_trades(symbol, df)

        if not trades:
            logger.info(f"{symbol}: Sinyal bulunamadı")
            result = BacktestResult(symbol=symbol, timeframe=timeframe, days=days)
            return result

        # İstatistikleri hesapla
        result = self._compute_stats(symbol, timeframe, days, trades)
        logger.info(f"Backtest tamamlandı: {result.summary()}")
        return result

    def run_portfolio(
        self,
        symbols: List[str],
        days: int = 90,
        timeframe: str = "5m",
    ) -> PortfolioResult:
        """
        Birden fazla sembol backtest eder, toplam sonuç döndürür.
        """
        results = []
        for sym in symbols:
            try:
                r = self.run(sym, days, timeframe)
                if r and r.total_trades > 0:
                    results.append(r)
                time.sleep(0.3)  # Rate limit
            except Exception as e:
                logger.error(f"Backtest error for {sym}: {e}")

        return self._aggregate_portfolio(results)

    # ------------------------------------------------------------------
    # Veri çekme
    # ------------------------------------------------------------------

    def _fetch_data(
        self,
        symbol: str,
        days: int,
        timeframe: str,
    ) -> Optional[pd.DataFrame]:
        """CCXT ile paginated OHLCV çeker."""
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        since_ms = int(since_dt.timestamp() * 1000)

        all_ohlcv = []
        max_retries = 3

        while True:
            for attempt in range(max_retries):
                try:
                    batch = self.exchange.fetch_ohlcv(
                        symbol,
                        timeframe=timeframe,
                        since=since_ms,
                        limit=1000,
                    )
                    break
                except ccxt.RateLimitExceeded:
                    time.sleep(10)
                except Exception as e:
                    logger.error(f"Fetch error {symbol}: {e}")
                    if attempt == max_retries - 1:
                        return None
                    time.sleep(2)

            if not batch:
                break
            all_ohlcv.extend(batch)
            if len(batch) < 1000:
                break
            since_ms = batch[-1][0] + 1
            time.sleep(0.2)

        if not all_ohlcv:
            return None

        df = pd.DataFrame(
            all_ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        return df

    # ------------------------------------------------------------------
    # İndikatörler
    # ------------------------------------------------------------------

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """RSI, MACD, hacim MA ekler."""
        df = df.copy()
        df["rsi"] = compute_rsi(df["close"], 14)
        df["macd"], df["macd_signal"], df["macd_hist"] = compute_macd(
            df["close"], 12, 26, 9
        )
        df["vol_ma20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / (df["vol_ma20"] + 1e-8)
        df["price_change_pct"] = df["close"].pct_change(3) * 100  # 3 bar = 15dk (5m)
        df.dropna(inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    # ------------------------------------------------------------------
    # Ticaret simülasyonu
    # ------------------------------------------------------------------

    def _simulate_trades(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> List[BacktestTrade]:
        """
        Her bara sinyal kontrolü yapar, açık pozisyonları yönetir.
        """
        trades = []
        in_position = False
        current_trade: Optional[BacktestTrade] = None
        bars_in_trade = 0

        # En az 50 bar sonrasından başla (indikatörlerin ısınması için)
        start_idx = 50

        for i in range(start_idx, len(df)):
            row = df.iloc[i]

            # --- Açık pozisyon varsa TP/SL kontrolü ---
            if in_position and current_trade is not None:
                bars_in_trade += 1

                high = row["high"]
                low = row["low"]
                close = row["close"]

                if current_trade.direction == "long":
                    # TP kontrolü
                    if high >= current_trade.tp_price:
                        current_trade.exit_price = current_trade.tp_price
                        current_trade.exit_time = row["timestamp"]
                        current_trade.exit_reason = "TP"
                        current_trade.pnl_pct = self.tp_pct * 100
                        current_trade.won = True
                        trades.append(current_trade)
                        in_position = False
                        bars_in_trade = 0
                        continue

                    # SL kontrolü
                    if low <= current_trade.sl_price:
                        current_trade.exit_price = current_trade.sl_price
                        current_trade.exit_time = row["timestamp"]
                        current_trade.exit_reason = "SL"
                        current_trade.pnl_pct = -self.sl_pct * 100
                        current_trade.won = False
                        trades.append(current_trade)
                        in_position = False
                        bars_in_trade = 0
                        continue

                elif current_trade.direction == "short":
                    if low <= current_trade.tp_price:
                        current_trade.exit_price = current_trade.tp_price
                        current_trade.exit_time = row["timestamp"]
                        current_trade.exit_reason = "TP"
                        current_trade.pnl_pct = self.tp_pct * 100
                        current_trade.won = True
                        trades.append(current_trade)
                        in_position = False
                        bars_in_trade = 0
                        continue

                    if high >= current_trade.sl_price:
                        current_trade.exit_price = current_trade.sl_price
                        current_trade.exit_time = row["timestamp"]
                        current_trade.exit_reason = "SL"
                        current_trade.pnl_pct = -self.sl_pct * 100
                        current_trade.won = False
                        trades.append(current_trade)
                        in_position = False
                        bars_in_trade = 0
                        continue

                # Timeout
                if bars_in_trade >= self.timeout_bars:
                    current_trade.exit_price = close
                    current_trade.exit_time = row["timestamp"]
                    current_trade.exit_reason = "TIMEOUT"
                    entry = current_trade.entry_price
                    if current_trade.direction == "long":
                        current_trade.pnl_pct = (close - entry) / entry * 100
                    else:
                        current_trade.pnl_pct = (entry - close) / entry * 100
                    current_trade.won = current_trade.pnl_pct > 0
                    trades.append(current_trade)
                    in_position = False
                    bars_in_trade = 0

            # --- Pozisyon yoksa sinyal ara ---
            if not in_position:
                signal = self._check_signal(df, i)
                if signal:
                    direction, score, reason = signal
                    entry_price = row["close"]

                    if direction == "long":
                        tp = entry_price * (1 + self.tp_pct)
                        sl = entry_price * (1 - self.sl_pct)
                    else:
                        tp = entry_price * (1 - self.tp_pct)
                        sl = entry_price * (1 + self.sl_pct)

                    current_trade = BacktestTrade(
                        symbol=symbol,
                        entry_time=row["timestamp"],
                        entry_price=entry_price,
                        direction=direction,
                        tp_price=tp,
                        sl_price=sl,
                        signal_score=score,
                        signal_reason=reason,
                    )
                    in_position = True
                    bars_in_trade = 0

        # Döngü bitti, hala açık pozisyon varsa kapat
        if in_position and current_trade is not None:
            last = df.iloc[-1]
            current_trade.exit_price = last["close"]
            current_trade.exit_time = last["timestamp"]
            current_trade.exit_reason = "END"
            entry = current_trade.entry_price
            close = last["close"]
            if current_trade.direction == "long":
                current_trade.pnl_pct = (close - entry) / entry * 100
            else:
                current_trade.pnl_pct = (entry - close) / entry * 100
            current_trade.won = current_trade.pnl_pct > 0
            trades.append(current_trade)

        return trades

    def _check_signal(
        self,
        df: pd.DataFrame,
        idx: int,
    ) -> Optional[Tuple[str, int, str]]:
        """
        YENİ STRATEJİ — Çok daha sıkı 4 koşul.

        LONG için HEPSİ sağlanmalı:
          1. RSI < 35 (gerçekten aşırı satılmış)
          2. MACD histogram negatiften pozitife geçti (kesin crossover, sadece artıyor değil)
          3. Hacim son bar'da 20 bar ortalamasının 2x'i (gerçek ilgi)
          4. Son 3 bar fiyat yükseliyor (momentum başlamış)

        SHORT için HEPSİ sağlanmalı:
          1. RSI > 65
          2. MACD histogram pozitiften negatife geçti
          3. Hacim spike 2x
          4. Son 3 bar fiyat düşüyor

        Neden bu çalışır:
          - 4 koşulun hepsi aynı anda nadiren oluşur → az ama kaliteli sinyal
          - MACD crossover = trend değişimi, sadece "artıyor" değil
          - RSI + MACD birlikte → çift onay
          - Hacim spike = gerçek para girişi var
        """
        if idx < 50:
            return None

        row = df.iloc[idx]
        prev = df.iloc[idx - 1]
        prev2 = df.iloc[idx - 2]

        rsi = float(row["rsi"])
        macd_hist = float(row["macd_hist"])
        macd_hist_prev = float(prev["macd_hist"])
        vol_ratio = float(row["vol_ratio"])

        # Son 3 bar fiyat trendi
        price_now = float(row["close"])
        price_3_ago = float(df.iloc[idx - 3]["close"]) if idx >= 3 else price_now
        price_trend = (price_now - price_3_ago) / (price_3_ago + 1e-8) * 100

        # ── LONG: 4 koşulun HEPSİ ──────────────────────────────
        rsi_oversold      = rsi < 35                          # Koşul 1
        macd_crossover_up = macd_hist_prev < 0 < macd_hist   # Koşul 2: kesin crossover
        vol_spike         = vol_ratio >= 2.0                  # Koşul 3: 2x hacim
        price_rising      = price_trend >= self.price_change_min  # Koşul 4: fiyat yükseliyor

        long_conditions = [rsi_oversold, macd_crossover_up, vol_spike, price_rising]
        long_count = sum(long_conditions)

        # 4/4 = skor 9, 3/4 = skor 7
        if long_count == 4:
            reasons = [
                f"RSI aşırı satım ({rsi:.1f})",
                "MACD bullish crossover",
                f"Hacim spike x{vol_ratio:.1f}",
                f"Fiyat momentum +{price_trend:.1f}%",
            ]
            return ("long", 9, " | ".join(reasons))
        elif long_count == 3 and macd_crossover_up:
            # MACD crossover zorunlu, diğer 2'den biri olabilir
            reasons = []
            if rsi_oversold: reasons.append(f"RSI ({rsi:.1f})")
            reasons.append("MACD crossover")
            if vol_spike: reasons.append(f"Vol x{vol_ratio:.1f}")
            if price_rising: reasons.append(f"Momentum +{price_trend:.1f}%")
            return ("long", 7, " | ".join(reasons))

        # ── SHORT: 4 koşulun HEPSİ ─────────────────────────────
        rsi_overbought     = rsi > 65
        macd_crossover_dn  = macd_hist_prev > 0 > macd_hist  # Kesin bearish crossover
        price_falling      = price_trend <= -self.price_change_min

        short_conditions = [rsi_overbought, macd_crossover_dn, vol_spike, price_falling]
        short_count = sum(short_conditions)

        if short_count == 4:
            reasons = [
                f"RSI aşırı alım ({rsi:.1f})",
                "MACD bearish crossover",
                f"Hacim spike x{vol_ratio:.1f}",
                f"Fiyat düşüş {price_trend:.1f}%",
            ]
            return ("short", 9, " | ".join(reasons))
        elif short_count == 3 and macd_crossover_dn:
            reasons = []
            if rsi_overbought: reasons.append(f"RSI ({rsi:.1f})")
            reasons.append("MACD bearish crossover")
            if vol_spike: reasons.append(f"Vol x{vol_ratio:.1f}")
            if price_falling: reasons.append(f"Düşüş {price_trend:.1f}%")
            return ("short", 7, " | ".join(reasons))

        return None

    # ------------------------------------------------------------------
    # İstatistik hesaplama
    # ------------------------------------------------------------------

    def _compute_stats(
        self,
        symbol: str,
        timeframe: str,
        days: int,
        trades: List[BacktestTrade],
    ) -> BacktestResult:
        """Tüm istatistikleri hesaplar."""
        result = BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            total_trades=len(trades),
        )
        result.trades = trades

        if not trades:
            return result

        wins = [t for t in trades if t.won]
        losses = [t for t in trades if not t.won]
        timeouts = [t for t in trades if t.exit_reason == "TIMEOUT"]

        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.timeout_trades = len(timeouts)
        result.win_rate = len(wins) / len(trades) * 100

        pnls = [t.pnl_pct for t in trades]

        result.avg_win_pct = float(np.mean([t.pnl_pct for t in wins])) if wins else 0
        result.avg_loss_pct = float(np.mean([t.pnl_pct for t in losses])) if losses else 0

        # Basit ROI: Her işlemi portföyün %2'si ile
        position_size = 0.02  # %2 portföy riski
        equity = 100.0
        equity_curve = [equity]

        for t in trades:
            equity += equity * position_size * (t.pnl_pct / 100)
            equity_curve.append(equity)

        result.total_roi_pct = equity - 100
        result.equity_curve = equity_curve

        # Max drawdown
        result.max_drawdown_pct = self._max_drawdown(equity_curve)

        # Profit factor
        gross_profit = sum(t.pnl_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 1
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Sharpe (basit)
        if len(pnls) > 1:
            mean_pnl = np.mean(pnls)
            std_pnl = np.std(pnls)
            result.sharpe_ratio = float(mean_pnl / (std_pnl + 1e-8) * np.sqrt(252))

        return result

    def _max_drawdown(self, equity_curve: List[float]) -> float:
        """Equity curve'den max drawdown hesaplar (%)."""
        if len(equity_curve) < 2:
            return 0.0
        arr = np.array(equity_curve)
        peak = np.maximum.accumulate(arr)
        drawdowns = (arr - peak) / peak * 100
        return float(abs(drawdowns.min()))

    # ------------------------------------------------------------------
    # Portfolio aggregation
    # ------------------------------------------------------------------

    def _aggregate_portfolio(
        self,
        results: List[BacktestResult],
    ) -> PortfolioResult:
        """Tüm sembollerin sonuçlarını birleştirir."""
        portfolio = PortfolioResult(symbol_results=results)

        if not results:
            return portfolio

        all_trades = sum(r.total_trades for r in results)
        all_wins = sum(r.winning_trades for r in results)

        portfolio.total_trades = all_trades
        portfolio.overall_win_rate = (all_wins / all_trades * 100) if all_trades > 0 else 0

        rois = [r.total_roi_pct for r in results]
        portfolio.total_roi_pct = float(np.mean(rois)) if rois else 0

        all_pnls = []
        for r in results:
            all_pnls.extend([t.pnl_pct for t in r.trades])
        portfolio.avg_roi_per_trade = float(np.mean(all_pnls)) if all_pnls else 0

        if results:
            best = max(results, key=lambda r: r.win_rate)
            worst = min(results, key=lambda r: r.win_rate)
            portfolio.best_symbol = f"{best.symbol} (WR:{best.win_rate:.0f}%)"
            portfolio.worst_symbol = f"{worst.symbol} (WR:{worst.win_rate:.0f}%)"

        return portfolio


# ======================================================================
# Telegram Raporu
# ======================================================================

def format_backtest_report(
    result: BacktestResult,
    is_portfolio: bool = False,
) -> str:
    """
    BacktestResult'ı Telegram HTML mesajına çevirir.
    Mala anlatır gibi: basit, net, emoji'li.
    """
    r = result

    # Win rate değerlendirmesi
    if r.win_rate >= 60:
        wr_emoji = "🟢"
        wr_comment = "İyi strateji!"
    elif r.win_rate >= 45:
        wr_emoji = "🟡"
        wr_comment = "Ortalama, optimize edilebilir"
    else:
        wr_emoji = "🔴"
        wr_comment = "Zayıf, bu stratejiyle girme"

    # ROI değerlendirmesi
    roi_emoji = "📈" if r.total_roi_pct > 0 else "📉"

    # Profit factor
    pf_comment = ""
    if r.profit_factor >= 2.0:
        pf_comment = "Mükemmel"
    elif r.profit_factor >= 1.5:
        pf_comment = "İyi"
    elif r.profit_factor >= 1.0:
        pf_comment = "Başabaş"
    else:
        pf_comment = "Zararlı"

    lines = [
        f"📊 <b>BACKTEST SONUCU: {r.symbol}</b>",
        f"📅 Son {r.days} gün | {r.timeframe} mumlar",
        "",
        f"<b>── ÖZET ──────────────────</b>",
        f"📌 Toplam sinyal: <b>{r.total_trades}</b>",
        f"{wr_emoji} <b>Kazanma oranı: %{r.win_rate:.1f}</b> — {wr_comment}",
        f"{roi_emoji} Toplam ROI: <b>{r.total_roi_pct:+.1f}%</b> (portföy %2'yle)",
        f"💹 Profit Factor: <b>{r.profit_factor:.2f}</b> ({pf_comment})",
        f"⬇️ Max Düşüş: <b>%{r.max_drawdown_pct:.1f}</b>",
        f"📐 Sharpe: <b>{r.sharpe_ratio:.2f}</b>",
        "",
        f"<b>── İŞLEM DETAYI ─────────</b>",
        f"✅ Kazanan: {r.winning_trades} işlem (ort. +%{r.avg_win_pct:.1f})",
        f"❌ Kaybeden: {r.losing_trades} işlem (ort. %{r.avg_loss_pct:.1f})",
        f"⏰ Zaman doldu: {r.timeout_trades} işlem",
    ]

    # En iyi/kötü 3 işlem
    if r.trades:
        sorted_trades = sorted(r.trades, key=lambda t: t.pnl_pct, reverse=True)
        best3 = sorted_trades[:3]
        worst3 = sorted_trades[-3:]

        lines.append("")
        lines.append("<b>── EN İYİ 3 İŞLEM ───────</b>")
        for t in best3:
            lines.append(
                f"  {t.entry_time.strftime('%m/%d')} → "
                f"{t.exit_reason} <b>{t.pnl_pct:+.1f}%</b>"
            )

        lines.append("")
        lines.append("<b>── EN KÖTÜ 3 İŞLEM ──────</b>")
        for t in worst3:
            lines.append(
                f"  {t.entry_time.strftime('%m/%d')} → "
                f"{t.exit_reason} <b>{t.pnl_pct:+.1f}%</b>"
            )

    # Tavsiye
    lines.append("")
    lines.append("<b>── TAVSİYE ──────────────</b>")
    if r.win_rate >= 55 and r.profit_factor >= 1.5 and r.total_roi_pct > 0:
        lines.append("✅ <b>Bu strateji çalışıyor! Paper trading'e geç.</b>")
    elif r.win_rate >= 45 and r.total_trades >= 10:
        lines.append("⚠️ <b>Ortalama sonuç. Ayarları optimize et.</b>")
    elif r.total_trades < 5:
        lines.append("ℹ️ <b>Çok az sinyal. Eşikleri düşür.</b>")
    else:
        lines.append("❌ <b>Bu ayarlarla işlem açma. Strateji değiştir.</b>")

    lines.append(f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return "\n".join(lines)


def format_portfolio_report(portfolio: PortfolioResult) -> str:
    """Çoklu sembol portfolio backtest raporu."""
    p = portfolio
    results = sorted(
        p.symbol_results,
        key=lambda r: r.win_rate,
        reverse=True
    )

    lines = [
        "📊 <b>PORTFOLIO BACKTEST RAPORU</b>",
        f"📌 {len(results)} sembol test edildi",
        "",
        f"<b>── GENEL SONUÇ ──────────</b>",
        f"📌 Toplam işlem: <b>{p.total_trades}</b>",
        f"🎯 Genel win rate: <b>%{p.overall_win_rate:.1f}</b>",
        f"📈 Ortalama ROI: <b>{p.total_roi_pct:+.1f}%</b>",
        f"💰 İşlem başı ort: <b>{p.avg_roi_per_trade:+.2f}%</b>",
        "",
        f"🏆 En iyi: <b>{p.best_symbol}</b>",
        f"💀 En kötü: <b>{p.worst_symbol}</b>",
        "",
        "<b>── SEMBOL SIRALAMASI ────</b>",
    ]

    for r in results[:15]:  # Max 15 göster
        wr_bar = "█" * int(r.win_rate / 10) + "░" * (10 - int(r.win_rate / 10))
        emoji = "🟢" if r.win_rate >= 55 else "🟡" if r.win_rate >= 45 else "🔴"
        lines.append(
            f"{emoji} <b>{r.symbol.replace('/USDT','')}</b>: "
            f"%{r.win_rate:.0f} WR | {r.total_trades} işlem | "
            f"ROI:{r.total_roi_pct:+.0f}%"
        )

    lines.append(f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)
