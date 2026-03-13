"""
Pinger - Hızlı Momentum Tarayıcı
Coin hızlı yükselmeye BAŞLARKEN Telegram'a haber ver.
"""

import os, sys, time, ccxt, requests
from datetime import datetime
from collections import deque
from dotenv import load_dotenv

load_dotenv()

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")

# ── Ayarlar ────────────────────────────────────
MIN_VOL_USDT  = 1_000_000  # Minimum 24h hacim
PRICE_JUMP    = 1.0        # 10 sn'de en az %1 fiyat artışı (iki tur arası)
VOL_SPIKE     = 1.5        # Hacim 1.5x artmalı
COOLDOWN_MIN  = 45         # Aynı coinden kaç dk sonra tekrar haber
MAX_24H_RISE  = 20.0       # Zaten %20'den fazla yükselmişse atla (geç kaldın)
TP_PCT        = 5.0        # Hedef kar %
SL_PCT        = 3.0        # Stop loss %
SCAN_INTERVAL = 5          # fetch_tickers zaten 15-20sn sürüyor, bu sadece tur arası bekleme
STABLES = {"USDC","FDUSD","BUSD","TUSD","DAI","EUR","USD1","PAXG","USDP"}

def tg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
    except: pass

def fmt(p):
    if p >= 100:  return f"{p:,.2f}"
    if p >= 1:    return f"{p:.4f}"
    if p >= 0.01: return f"{p:.6f}"
    return f"{p:.8f}"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ── Ana döngü ───────────────────────────────────

def main():
    log("Bağlanıyor...")
    ex = ccxt.binance({"enableRateLimit": True})
    ex.load_markets()
    log("Binance bağlantısı OK")

    # Fiyat geçmişi: son 12 tur (~1 dakika) tutuyoruz
    # Karşılaştırma: şu anki fiyat vs 12 tur önceki (≈1 dakika önce)
    from collections import deque
    history  = {}   # {symbol: deque(maxlen=12) of (price, volume)}
    cooldowns = {}  # {symbol: timestamp}

    tg(
        "🚀 <b>Momentum Scanner Başladı!</b>\n\n"
        f"⏱ Her {SCAN_INTERVAL} saniye tüm coinler taranıyor\n"
        f"📈 Eşik: %{PRICE_JUMP} hareket + {VOL_SPIKE}x hacim\n"
        f"💰 Min hacim: ${MIN_VOL_USDT/1_000_000:.0f}M\n\n"
        "Hızlı yükselen coin yakalandığında haber vereceğim! 🔔"
    )
    log("Telegram mesajı gönderildi, tarama başlıyor...")

    scan = 0
    while True:
        try:
            scan += 1
            now = time.time()
            tickers = ex.fetch_tickers()
            found = 0

            for sym, t in tickers.items():
                if not sym.endswith("/USDT"):
                    continue
                base = sym.split("/")[0]
                if base in STABLES:
                    continue

                try:
                    price  = float(t.get("last") or 0)
                    vol24  = float(t.get("quoteVolume") or 0)
                    pct24  = float(t.get("percentage") or 0)

                    if price <= 0 or vol24 < MIN_VOL_USDT:
                        continue

                    # Cooldown
                    if now - cooldowns.get(sym, 0) < COOLDOWN_MIN * 60:
                        continue

                    # Geçmişe ekle
                    if sym not in history:
                        history[sym] = deque(maxlen=12)
                    history[sym].append((price, vol24))

                    # En az 6 tur geçmeden karşılaştırma yapma (~30 saniye)
                    if len(history[sym]) < 6:
                        continue

                    # 6 tur öncesiyle karşılaştır (≈30 saniye önce)
                    p0, v0 = history[sym][0]

                    change = (price - p0) / (p0 + 1e-8) * 100
                    vol_x  = vol24 / (v0 + 1e-8)

                    # Koşullar
                    if change < PRICE_JUMP:    continue
                    if vol_x  < VOL_SPIKE:     continue
                    if pct24  > MAX_24H_RISE:  continue  # Zaten geç

                    # ALARM
                    cooldowns[sym] = now
                    found += 1

                    tp = price * (1 + TP_PCT / 100)
                    sl = price * (1 - SL_PCT / 100)

                    if   change >= 3 and vol_x >= 3: guc = "🔥🔥🔥 ÇOK GÜÇLÜ"
                    elif change >= 2 and vol_x >= 2: guc = "🔥🔥 GÜÇLÜ"
                    else:                             guc = "🔥 ORTA"

                    msg = (
                        f"⚡ <b>YUKARI HAREKET!</b> {guc}\n\n"
                        f"💎 <b>{sym}</b>\n\n"
                        f"💰 Fiyat: <code>{fmt(price)}</code> USDT\n"
                        f"📈 Son {SCAN_INTERVAL}sn: <b>+{change:.2f}%</b>\n"
                        f"📊 24h toplam: +{pct24:.1f}%\n"
                        f"🔊 Hacim spike: <b>x{vol_x:.1f}</b>\n"
                        f"💵 24h hacim: ${vol24/1_000_000:.1f}M\n\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"🟢 <b>Giriş:</b> <code>{fmt(price)}</code>\n"
                        f"✅ <b>Hedef +{TP_PCT:.0f}%:</b> <code>{fmt(tp)}</code>\n"
                        f"❌ <b>Stop -{SL_PCT:.0f}%:</b> <code>{fmt(sl)}</code>\n"
                        f"⏱ <b>Çıkış:</b> 30-60 dakika içinde\n\n"
                        f"🔗 <a href='https://www.binance.com/en/trade/{base}_USDT'>Binance</a>  "
                        f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT'>TradingView</a>\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    )
                    tg(msg)
                    log(f"🚨 ALARM: {sym} +{change:.1f}% | Vol x{vol_x:.1f}")

                except Exception:
                    continue

            log(f"Tarama #{scan} tamamlandı | {found} sinyal | {len(history)} coin izleniyor")

        except KeyboardInterrupt:
            log("Durduruldu.")
            break
        except Exception as e:
            log(f"Hata: {e}")
            time.sleep(10)

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
