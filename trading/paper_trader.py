"""
Pinger v2.0 - Paper Trading (Sahte Para ile Gerçek Zamanlı Test)
=================================================================
Ne yapar:
  - Gerçek sinyalleri alır ama gerçek para kullanmaz
  - Sanal portföy tutar (başlangıç: $1000)
  - Her işlemi loglar: giriş fiyatı, çıkış, kazanç/kayıp
  - Telegram'a anlık işlem bildirimi gönderir
  - Günlük performans raporu gönderir

Neden önemli:
  Sistemi 2 hafta paper trade yaparsın.
  Win rate %55+ ve portföy büyüyorsa → gerçek paraya geç.
  Küçülüyorsa → stratejiyi düzelt.
"""

import time
import threading
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict

import ccxt

from core.signals import MarketSignal
from utils.logger import setup_logger

logger = setup_logger(__name__)

PAPER_STATE_FILE = Path("data/paper_trading_state.json")
PAPER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


# ======================================================================
# Veri yapıları
# ======================================================================

@dataclass
class PaperPosition:
    """Açık bir sanal pozisyon."""
    symbol: str
    direction: str           # long / short
    entry_price: float
    entry_time: str          # ISO format
    quantity: float          # Kaç adet coin
    size_usdt: float         # Kaç dolar girdi
    tp_price: float
    sl_price: float
    signal_score: int
    signal_reasons: List[str] = field(default_factory=list)


@dataclass
class PaperTrade:
    """Kapanmış bir işlem kaydı."""
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    exit_reason: str         # TP / SL / MANUAL
    size_usdt: float
    pnl_usdt: float
    pnl_pct: float
    won: bool
    signal_score: int


# ======================================================================
# Paper Trader
# ======================================================================

class PaperTrader:
    """
    Sanal portföy yöneticisi.

    Kullanım:
      paper = PaperTrader(exchange, config, notifier)
      paper.start()
      paper.open_position(market_signal)  # Scanner'dan gelen sinyal
    """

    def __init__(
        self,
        exchange: ccxt.Exchange,
        config: dict,
        notifier=None,
        initial_balance: float = 1000.0,
    ):
        self.exchange = exchange
        self.config = config
        self.notifier = notifier

        risk_cfg = config.get("risk", {})
        self.tp_pct = risk_cfg.get("take_profit_pct", 10.0) / 100
        self.sl_pct = risk_cfg.get("stop_loss_pct", 7.0) / 100
        self.max_portfolio_risk = risk_cfg.get("max_portfolio_risk_pct", 2.0) / 100
        self.max_daily_trades = risk_cfg.get("max_daily_trades", 5)

        # Portföy durumu
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.open_positions: Dict[str, PaperPosition] = {}
        self.closed_trades: List[PaperTrade] = []
        self.today_trade_count = 0
        self._today_date = datetime.now().date()

        # Diskten state yükle
        self._load_state()

        # Monitor thread
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

        # Günlük rapor zamanı
        self._last_daily_report = datetime.now()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Pozisyon monitor thread'ini başlat."""
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="PaperMonitor",
        )
        self._monitor_thread.start()
        logger.info(f"Paper trader started. Balance: ${self.balance:.2f}")

    def stop(self) -> None:
        self._running = False
        self._save_state()
        logger.info("Paper trader stopped.")

    # ------------------------------------------------------------------
    # Pozisyon yönetimi
    # ------------------------------------------------------------------

    def open_position(self, signal: MarketSignal) -> bool:
        """
        Sinyal geldiğinde sanal pozisyon aç.

        Returns:
            True = başarıyla açıldı
        """
        symbol = signal.symbol

        # Kontroller
        if symbol in self.open_positions:
            logger.debug(f"Paper: {symbol} zaten açık pozisyon var")
            return False

        if not self._can_trade_today():
            logger.info(f"Paper: Günlük limit ({self.max_daily_trades}) doldu")
            return False

        if self.balance <= 0:
            logger.warning("Paper: Bakiye tükendi")
            return False

        # Pozisyon büyüklüğü: portföyün %2'si
        size_usdt = self.balance * self.max_portfolio_risk
        size_usdt = min(size_usdt, self.balance)  # Bakiyeden fazla olamaz

        entry_price = signal.price
        if entry_price <= 0:
            logger.warning(f"Paper: {symbol} geçersiz fiyat: {entry_price}")
            return False

        quantity = size_usdt / entry_price

        if signal.direction == "long":
            tp = entry_price * (1 + self.tp_pct)
            sl = entry_price * (1 - self.sl_pct)
        else:
            tp = entry_price * (1 - self.tp_pct)
            sl = entry_price * (1 + self.sl_pct)

        position = PaperPosition(
            symbol=symbol,
            direction=signal.direction,
            entry_price=entry_price,
            entry_time=datetime.now().isoformat(),
            quantity=quantity,
            size_usdt=size_usdt,
            tp_price=tp,
            sl_price=sl,
            signal_score=signal.score,
            signal_reasons=signal.signal_reasons[:3],
        )

        self.open_positions[symbol] = position
        self.balance -= size_usdt  # Sanal bakiyeden düş
        self.today_trade_count += 1

        logger.info(
            f"Paper OPEN | {symbol} {signal.direction.upper()} | "
            f"${size_usdt:.2f} @ {entry_price:.6f} | "
            f"TP:{tp:.6f} SL:{sl:.6f}"
        )

        # Telegram bildirimi
        if self.notifier:
            direction_emoji = "📈" if signal.direction == "long" else "📉"
            msg = (
                f"🧪 <b>PAPER TRADE AÇILDI</b> {direction_emoji}\n\n"
                f"💎 {symbol} — {signal.direction.upper()}\n"
                f"💰 Giriş: <code>{self._fmt_price(entry_price)}</code>\n"
                f"✅ TP: <code>{self._fmt_price(tp)}</code> (+{self.tp_pct*100:.0f}%)\n"
                f"❌ SL: <code>{self._fmt_price(sl)}</code> (-{self.sl_pct*100:.0f}%)\n"
                f"💵 Boyut: ${size_usdt:.2f} (Bakiye: ${self.balance+size_usdt:.2f})\n"
                f"⭐ Skor: {signal.score}/9\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            )
            self.notifier.send_text(msg)

        self._save_state()
        return True

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
    ) -> Optional[PaperTrade]:
        """Pozisyonu kapat."""
        if symbol not in self.open_positions:
            return None

        pos = self.open_positions.pop(symbol)
        quantity = pos.quantity
        direction = pos.direction
        entry_price = pos.entry_price

        # PnL hesapla
        if direction == "long":
            pnl_usdt = (exit_price - entry_price) * quantity
        else:
            pnl_usdt = (entry_price - exit_price) * quantity

        pnl_pct = (pnl_usdt / pos.size_usdt) * 100
        won = pnl_usdt > 0

        # Bakiyeye geri ekle
        self.balance += pos.size_usdt + pnl_usdt

        trade = PaperTrade(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=datetime.now().isoformat(),
            exit_reason=exit_reason,
            size_usdt=pos.size_usdt,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            won=won,
            signal_score=pos.signal_score,
        )
        self.closed_trades.append(trade)

        result_emoji = "✅" if won else "❌"
        logger.info(
            f"Paper CLOSE | {symbol} | {exit_reason} | "
            f"PnL: ${pnl_usdt:+.2f} ({pnl_pct:+.1f}%) | "
            f"Bakiye: ${self.balance:.2f}"
        )

        # Telegram bildirimi
        if self.notifier:
            msg = (
                f"{result_emoji} <b>PAPER TRADE KAPANDI</b> ({exit_reason})\n\n"
                f"💎 {symbol}\n"
                f"📥 Giriş: <code>{self._fmt_price(entry_price)}</code>\n"
                f"📤 Çıkış: <code>{self._fmt_price(exit_price)}</code>\n"
                f"{'🟢' if won else '🔴'} Kar/Zarar: <b>${pnl_usdt:+.2f} ({pnl_pct:+.1f}%)</b>\n"
                f"💰 Yeni bakiye: <b>${self.balance:.2f}</b>\n"
                f"📊 Başlangıçtan beri: <b>{self._total_return():+.1f}%</b>\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            )
            self.notifier.send_text(msg)

        self._save_state()
        return trade

    # ------------------------------------------------------------------
    # Monitor loop
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """Açık pozisyonları periyodik kontrol eder (TP/SL)."""
        while self._running:
            try:
                self._check_positions()
                self._check_daily_reset()
                self._check_daily_report()
                time.sleep(15)  # Her 15 saniye kontrol
            except Exception as e:
                logger.error(f"Paper monitor error: {e}")
                time.sleep(30)

    def _check_positions(self) -> None:
        """Tüm açık pozisyonların TP/SL kontrolü."""
        if not self.open_positions:
            return

        symbols = list(self.open_positions.keys())
        for symbol in symbols:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                price = float(ticker.get("last") or ticker.get("close") or 0)
                if price <= 0:
                    continue

                pos = self.open_positions.get(symbol)
                if not pos:
                    continue

                if pos.direction == "long":
                    if price >= pos.tp_price:
                        self._close_position(symbol, pos.tp_price, "TP")
                    elif price <= pos.sl_price:
                        self._close_position(symbol, pos.sl_price, "SL")
                else:
                    if price <= pos.tp_price:
                        self._close_position(symbol, pos.tp_price, "TP")
                    elif price >= pos.sl_price:
                        self._close_position(symbol, pos.sl_price, "SL")

                # 24 saat timeout
                entry_dt = datetime.fromisoformat(pos.entry_time)
                if (datetime.now() - entry_dt).total_seconds() > 86400:
                    self._close_position(symbol, price, "TIMEOUT_24H")

            except Exception as e:
                logger.debug(f"Paper check error {symbol}: {e}")
            time.sleep(0.1)

    def _check_daily_reset(self) -> None:
        """Gün değiştiyse günlük sayacı sıfırla."""
        today = datetime.now().date()
        if today != self._today_date:
            self.today_trade_count = 0
            self._today_date = today

    def _check_daily_report(self) -> None:
        """Her 24 saatte bir günlük rapor gönder."""
        elapsed = (datetime.now() - self._last_daily_report).total_seconds()
        if elapsed >= 86400:
            self.send_daily_report()
            self._last_daily_report = datetime.now()

    # ------------------------------------------------------------------
    # Raporlar
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Mevcut performans istatistikleri."""
        trades = self.closed_trades
        total = len(trades)
        if total == 0:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "total_return_pct": self._total_return(),
                "balance": self.balance,
                "open_positions": len(self.open_positions),
            }

        wins = [t for t in trades if t.won]
        win_rate = len(wins) / total * 100
        avg_pnl = sum(t.pnl_pct for t in trades) / total
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        losses = [t for t in trades if not t.won]
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "total_return_pct": round(self._total_return(), 2),
            "balance": round(self.balance, 2),
            "open_positions": len(self.open_positions),
            "today_trades": self.today_trade_count,
        }

    def send_daily_report(self) -> None:
        """Günlük özet raporu Telegram'a gönder."""
        if not self.notifier:
            return

        stats = self.get_stats()
        total_return = stats["total_return_pct"]
        ret_emoji = "📈" if total_return >= 0 else "📉"
        wr = stats["win_rate"]
        wr_emoji = "🟢" if wr >= 55 else "🟡" if wr >= 45 else "🔴"

        # Bugünkü işlemler
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_trades = [
            t for t in self.closed_trades
            if t.exit_time.startswith(today_str)
        ]

        msg = (
            f"📋 <b>GÜNLÜK PAPER TRADE RAPORU</b>\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d')}\n\n"
            f"<b>── PORTFÖY ─────────────</b>\n"
            f"💰 Bakiye: <b>${stats['balance']:.2f}</b>\n"
            f"{ret_emoji} Başlangıçtan beri: <b>{total_return:+.2f}%</b>\n\n"
            f"<b>── PERFORMANS ──────────</b>\n"
            f"📌 Toplam işlem: {stats['total_trades']}\n"
            f"{wr_emoji} Win rate: <b>%{wr:.1f}</b>\n"
            f"✅ Ort. kazanç: +%{stats['avg_win_pct']:.2f}\n"
            f"❌ Ort. kayıp: %{stats['avg_loss_pct']:.2f}\n\n"
        )

        if today_trades:
            msg += "<b>── BUGÜNKÜ İŞLEMLER ────</b>\n"
            for t in today_trades[-5:]:
                emoji = "✅" if t.won else "❌"
                msg += (
                    f"{emoji} {t.symbol.replace('/USDT','')} "
                    f"{t.direction.upper()} → {t.exit_reason}: "
                    f"<b>{t.pnl_pct:+.1f}%</b>\n"
                )
            msg += "\n"

        if stats["open_positions"] > 0:
            msg += f"⚡ Açık pozisyon: {stats['open_positions']}\n\n"

        # Tavsiye
        if wr >= 55 and total_return > 0 and stats["total_trades"] >= 10:
            msg += "✅ <b>Sistem karlı çalışıyor! Gerçek paraya geçmeyi düşünebilirsin.</b>"
        elif wr >= 45 and stats["total_trades"] >= 5:
            msg += "⚠️ <b>Ortalama performans. Birkaç gün daha bekle.</b>"
        elif stats["total_trades"] < 5:
            msg += "ℹ️ <b>Henüz az veri var. Birkaç gün daha bekle.</b>"
        else:
            msg += "❌ <b>Performans düşük. Strateji ayarlarını gözden geçir.</b>"

        self.notifier.send_text(msg)

    # ------------------------------------------------------------------
    # Yardımcılar
    # ------------------------------------------------------------------

    def _total_return(self) -> float:
        """Başlangıçtan itibaren % getiri."""
        # Açık pozisyonları da dahil et
        open_value = sum(p.size_usdt for p in self.open_positions.values())
        total_value = self.balance + open_value
        return (total_value - self.initial_balance) / self.initial_balance * 100

    def _can_trade_today(self) -> bool:
        return self.today_trade_count < self.max_daily_trades

    def _fmt_price(self, price: float) -> str:
        if price >= 1:
            return f"{price:.4f}"
        return f"{price:.8f}"

    # ------------------------------------------------------------------
    # State kalıcılığı
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Portföy durumunu diske kaydet."""
        try:
            state = {
                "balance": self.balance,
                "initial_balance": self.initial_balance,
                "today_trade_count": self.today_trade_count,
                "today_date": str(self._today_date),
                "open_positions": {
                    k: asdict(v) for k, v in self.open_positions.items()
                },
                "closed_trades": [asdict(t) for t in self.closed_trades[-200:]],  # Son 200
                "saved_at": datetime.now().isoformat(),
            }
            PAPER_STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.error(f"Paper state save error: {e}")

    def _load_state(self) -> None:
        """Diskten portföy durumunu yükle."""
        if not PAPER_STATE_FILE.exists():
            return
        try:
            state = json.loads(PAPER_STATE_FILE.read_text())
            self.balance = state.get("balance", self.initial_balance)
            self.initial_balance = state.get("initial_balance", self.initial_balance)
            self.today_trade_count = state.get("today_trade_count", 0)

            saved_date_str = state.get("today_date", str(datetime.now().date()))
            from datetime import date
            saved_date = date.fromisoformat(saved_date_str)
            if saved_date < datetime.now().date():
                self.today_trade_count = 0

            for sym, pos_data in state.get("open_positions", {}).items():
                self.open_positions[sym] = PaperPosition(**pos_data)

            for t_data in state.get("closed_trades", []):
                self.closed_trades.append(PaperTrade(**t_data))

            logger.info(
                f"Paper state loaded: ${self.balance:.2f} bakiye, "
                f"{len(self.closed_trades)} geçmiş işlem"
            )
        except Exception as e:
            logger.error(f"Paper state load error: {e}")
