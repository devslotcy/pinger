"""
Pinger v2.0 - Filtreler
- Spam/cooldown koruması
- Düşük hacim filtresi
- Kara liste yönetimi
- Duplicate sinyal önleme
"""

import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Set, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)


class SignalFilter:
    """
    Sinyal kalite filtresi.
    Rate limit, cooldown, kara liste, hacim eşiği.
    """

    def __init__(self, config: dict):
        sig_cfg = config.get("signals", {})
        exc_cfg = config.get("exchange", {})
        scan_cfg = config.get("scanning", {})

        self.cooldown_minutes: int = sig_cfg.get("cooldown_minutes", 30)
        self.min_volume_usdt: float = scan_cfg.get("min_volume_usdt", 500_000)

        # {symbol: timestamp of last signal}
        self._last_signal: Dict[str, float] = {}

        # Kara liste: {symbol: expiry_timestamp}
        self._blacklist: Dict[str, float] = {}

        # Static blacklist from config
        static_bl = config.get("blacklist", {}).get("symbols", [])
        self._static_blacklist: Set[str] = set(static_bl)

        # Blacklist TTL
        bl_hours = config.get("blacklist", {}).get("max_age_hours", 168)
        self._blacklist_ttl = bl_hours * 3600

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_skip(self, symbol: str, volume_usdt: float) -> tuple[bool, str]:
        """
        Bu symbol için sinyal atlanmalı mı?

        Returns:
            (True = atla, reason string)
        """
        # Statik kara liste
        if symbol in self._static_blacklist:
            return True, "static_blacklist"

        # Dinamik kara liste
        if self._is_blacklisted(symbol):
            return True, "blacklisted"

        # Hacim filtresi
        if volume_usdt < self.min_volume_usdt:
            return True, f"low_volume ({volume_usdt:.0f} < {self.min_volume_usdt:.0f})"

        # Cooldown
        if self._in_cooldown(symbol):
            remaining = self._cooldown_remaining(symbol)
            return True, f"cooldown ({remaining:.0f}s remaining)"

        return False, ""

    def record_signal(self, symbol: str) -> None:
        """Sinyal gönderildi olarak işaretle (cooldown başlat)."""
        self._last_signal[symbol] = time.time()
        logger.debug(f"Signal recorded for {symbol}, cooldown {self.cooldown_minutes}min")

    def add_to_blacklist(self, symbol: str, reason: str = "", hours: Optional[float] = None) -> None:
        """
        Symbol'ü dinamik kara listeye ekle.

        Args:
            symbol: Örn "LUNA/USDT"
            reason: Kara listeye ekleme nedeni
            hours: Kaç saat (None → config default)
        """
        ttl = (hours * 3600) if hours else self._blacklist_ttl
        expiry = time.time() + ttl
        self._blacklist[symbol] = expiry
        logger.warning(f"Blacklisted {symbol}: {reason} (expires in {ttl/3600:.1f}h)")

    def remove_from_blacklist(self, symbol: str) -> None:
        """Kara listeden çıkar."""
        self._blacklist.pop(symbol, None)
        logger.info(f"Removed {symbol} from blacklist")

    def get_stats(self) -> dict:
        """Filter istatistikleri."""
        active_cooldowns = sum(1 for s in self._last_signal if self._in_cooldown(s))
        active_blacklists = sum(1 for s, exp in self._blacklist.items() if exp > time.time())
        return {
            "active_cooldowns": active_cooldowns,
            "active_blacklists": active_blacklists,
            "static_blacklist_size": len(self._static_blacklist),
        }

    def cleanup_expired(self) -> None:
        """Süresi dolmuş cooldown ve blacklist kayıtlarını temizle."""
        now = time.time()
        cooldown_secs = self.cooldown_minutes * 60

        # Eski cooldown'ları temizle
        expired_cd = [
            sym for sym, ts in self._last_signal.items()
            if now - ts > cooldown_secs
        ]
        for sym in expired_cd:
            del self._last_signal[sym]

        # Eski blacklist'leri temizle
        expired_bl = [sym for sym, exp in self._blacklist.items() if exp <= now]
        for sym in expired_bl:
            del self._blacklist[sym]

        if expired_cd or expired_bl:
            logger.debug(f"Cleanup: removed {len(expired_cd)} cooldowns, {len(expired_bl)} blacklists")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _in_cooldown(self, symbol: str) -> bool:
        if symbol not in self._last_signal:
            return False
        elapsed = time.time() - self._last_signal[symbol]
        return elapsed < self.cooldown_minutes * 60

    def _cooldown_remaining(self, symbol: str) -> float:
        if symbol not in self._last_signal:
            return 0.0
        elapsed = time.time() - self._last_signal[symbol]
        return max(0.0, self.cooldown_minutes * 60 - elapsed)

    def _is_blacklisted(self, symbol: str) -> bool:
        if symbol not in self._blacklist:
            return False
        if self._blacklist[symbol] <= time.time():
            del self._blacklist[symbol]
            return False
        return True
