#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kripto Alarm Sistemi - Binance Tüm Coinler
Telegram'a anlık bildirim gönderir
"""

import ccxt
import requests
import time
from datetime import datetime
from bs4 import BeautifulSoup
import json

# ═══════════════════════════════════════════════════════════════
# AYARLAR - BURASI ÇOK ÖNEMLİ!
# ═══════════════════════════════════════════════════════════════

# Telegram Bot Ayarları (BotFather'dan alacaksın)
TELEGRAM_TOKEN = "8590088115:AAHW8TChM3iqKvSLucLjADo2SxJ17Mmtjlo"  # Örnek: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
TELEGRAM_CHAT_ID = "8396641100"  # Örnek: "987654321"

# Alarm Eşikleri (ERKEN TREND YAKALAMA!)
VOLUME_MULTIPLIER = 1.2  # Son 15dk hacim artışı (1.2 = %20 artış) [ORIJINAL: 3.0]
PRICE_CHANGE_PERCENT = 1.5  # Son 15dk fiyat değişimi (%1.5 = ERKEN YAKALAMA!) [ORIJINAL: 8.0]
MIN_VOLUME_USDT = 100000  # Minimum 24h hacim ($100k+ = daha güvenilir) [ORIJINAL: 200000]
ONLY_POSITIVE = True  # Sadece YÜKSELİŞ alarmları (düşüşleri görmezden gel)

# İzleme Ayarları (HIZLI TARAMA!)
CHECK_INTERVAL = 15  # Her 15 saniyede bir tara (ÇOK HIZLI!) [ORIJINAL: 60]
ANNOUNCEMENT_CHECK_INTERVAL = 300  # Binance duyuru kontrolü (300 = 5 dakika)

# Filtreleme (İstemediğin coinleri buraya ekle)
BLACKLIST = ['USDT', 'BUSD', 'USDC', 'DAI', 'TUSD', 'PAX']  # Stablecoinler

# ═══════════════════════════════════════════════════════════════
# SİSTEM KODLARI (Buraya dokunma)
# ═══════════════════════════════════════════════════════════════

class CryptoAlertSystem:
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        self.volume_history = {}  # Hacim geçmişi
        self.last_announcement = ""  # Son duyuru
        self.alerted_symbols = {}  # Spam önleme (aynı coin için 1 saatte 1 alarm)

    def send_telegram(self, message):
        """Telegram'a mesaj gönder"""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                print(f"✅ Telegram bildirimi gönderildi")
            else:
                print(f"❌ Telegram hatası: {response.status_code}")
                print(f"Cevap: {response.text}")
        except Exception as e:
            print(f"❌ Telegram gönderim hatası: {e}")

    def test_telegram(self):
        """Telegram bağlantısını test et"""
        print("\n🧪 Telegram bağlantısı test ediliyor...")
        test_msg = "🤖 <b>Kripto Alarm Sistemi Başlatıldı!</b>\n\n"
        test_msg += f"⏰ Kontrol sıklığı: {CHECK_INTERVAL} saniye\n"
        test_msg += f"📊 Hacim eşiği: x{VOLUME_MULTIPLIER}\n"
        test_msg += f"📈 Fiyat değişim eşiği: %{PRICE_CHANGE_PERCENT}\n"
        test_msg += f"💰 Minimum hacim: ${MIN_VOLUME_USDT:,}\n\n"
        test_msg += "Sistem şimdi tüm Binance coinlerini izliyor..."
        self.send_telegram(test_msg)

    def can_alert(self, symbol):
        """Spam önleme - Aynı coin için 1 saatte 1 alarm"""
        now = time.time()
        if symbol in self.alerted_symbols:
            last_alert_time = self.alerted_symbols[symbol]
            if now - last_alert_time < 3600:  # 1 saat = 3600 saniye
                return False
        self.alerted_symbols[symbol] = now
        return True

    def get_all_usdt_pairs(self):
        """Tüm Binance USDT paritelerini al (sadece aktif olanlar)"""
        try:
            markets = self.exchange.load_markets()
            usdt_pairs = []

            print(f"🔍 Binance'den coinler alınıyor...")

            for symbol in markets:
                if symbol.endswith('/USDT'):
                    base = symbol.split('/')[0]
                    market_info = markets[symbol]

                    # Filtreleme: Sadece aktif ve işlem gören coinler
                    if base not in BLACKLIST and \
                       market_info.get('active', True) and \
                       market_info.get('spot', False):
                        usdt_pairs.append(symbol)

            print(f"📊 Toplam {len(usdt_pairs)} adet aktif USDT paritesi bulundu")
            return usdt_pairs
        except Exception as e:
            print(f"❌ Market verisi alınamadı: {e}")
            return []

    def check_volume_spike(self, symbol, debug=False):
        """Hacim patlaması kontrolü (ERKEN YAKALAMA!)"""
        try:
            # Son 1.5 saatin 5 dakikalık mumları (daha uzun analiz)
            ohlcv = self.exchange.fetch_ohlcv(symbol, '5m', limit=18)

            if len(ohlcv) < 18:
                return None

            # Hacim listesi
            volumes = [candle[5] for candle in ohlcv]

            # ERKEN YAKALAMA: Son 15 dakikalık hacim vs önceki 1 saat
            recent_volumes = volumes[-3:]  # Son 3 mum (15 dakika)
            old_volumes = volumes[:15]  # Önceki 15 mum (1 saat 15 dk)

            avg_old_volume = sum(old_volumes) / len(old_volumes)
            avg_recent_volume = sum(recent_volumes) / len(recent_volumes)

            # TREND BAŞLANGICI kontrolü
            current_volume = volumes[-1]

            # Fiyat hareketi analizi (Son 15 dakika vs 1 saat önce)
            first_candle = ohlcv[0]  # 1.5 saat önce
            price_15min_ago = ohlcv[-4][4]  # 15 dakika önce kapanış
            current_candle = ohlcv[-1]
            current_price = current_candle[4]

            # TREND BAŞLANGICI: Son 15 dakikada yükseliş var mı?
            price_change_15min = ((current_price - price_15min_ago) / price_15min_ago) * 100

            # Genel trend (1.5 saat)
            price_change_total = ((current_price - first_candle[1]) / first_candle[1]) * 100

            # 24 saatlik ticker bilgisi
            ticker = self.exchange.fetch_ticker(symbol)
            volume_24h = ticker['quoteVolume']  # USDT cinsinden hacim

            # Debug modu - ilk 3 coinin detayını göster
            if debug:
                vol_mult = avg_recent_volume / avg_old_volume if avg_old_volume > 0 else 0
                print(f"      🔍 {symbol}: Hacim x{vol_mult:.1f}, 15dk fiyat {price_change_15min:+.1f}%, Total {price_change_total:+.1f}%, 24h ${volume_24h:,.0f}")

            # Filtreleme: Çok düşük hacimli coinleri atla
            if volume_24h < MIN_VOLUME_USDT:
                return None

            # ERKEN YAKALAMA KONTROLÜ
            # 1. Son 15 dakikada hacim artışı var mı?
            volume_increasing = avg_recent_volume > avg_old_volume * VOLUME_MULTIPLIER

            # 2. Son 15 dakikada fiyat yükseliyor mu? (TREND BAŞLANGICI)
            early_trend = price_change_15min > PRICE_CHANGE_PERCENT

            # 3. Henüz çok geç değil mi? (Toplam yükseliş %20'den az olmalı)
            not_too_late = price_change_total < 20.0

            # Fiyat kontrolü (ONLY_POSITIVE)
            price_check = early_trend if ONLY_POSITIVE else abs(price_change_15min) > PRICE_CHANGE_PERCENT

            # ERKEN YAKALAMA: Hacim artıyor + Trend başlıyor + Henüz geç değil
            if volume_increasing and price_check and not_too_late:
                # TAHMİNİ SÜRE HESAPLA (AI Tahmin)
                # Trend hızı: Dakikada ortalama ne kadar artıyor?
                trend_speed_per_min = price_change_15min / 15  # % / dakika

                # Ne zaman %10-15'e ulaşır? (Hedef bölge)
                target_10_percent = 10.0
                target_15_percent = 15.0

                if trend_speed_per_min > 0:
                    # Şu anki toplam yükselişten hedefe kalan yol
                    remaining_to_10 = target_10_percent - price_change_total
                    remaining_to_15 = target_15_percent - price_change_total

                    # Dakika cinsinden tahmini süre
                    est_minutes_to_10 = remaining_to_10 / trend_speed_per_min if remaining_to_10 > 0 else 0
                    est_minutes_to_15 = remaining_to_15 / trend_speed_per_min if remaining_to_15 > 0 else 0

                    # Tahmini fiyatlar
                    est_price_30min = current_price * (1 + (trend_speed_per_min * 30) / 100)
                    est_price_60min = current_price * (1 + (trend_speed_per_min * 60) / 100)
                else:
                    est_minutes_to_10 = 0
                    est_minutes_to_15 = 0
                    est_price_30min = current_price
                    est_price_60min = current_price

                return {
                    'symbol': symbol,
                    'avg_volume': avg_old_volume,
                    'current_volume': avg_recent_volume,
                    'volume_increase': (avg_recent_volume / avg_old_volume),
                    'price_change': price_change_15min,  # Son 15 dakika!
                    'price_change_total': price_change_total,  # Toplam
                    'current_price': current_price,
                    'volume_24h': volume_24h,
                    # TAHMİN verileri
                    'trend_speed': trend_speed_per_min,
                    'est_minutes_to_10': max(0, min(120, est_minutes_to_10)),  # Max 2 saat
                    'est_minutes_to_15': max(0, min(120, est_minutes_to_15)),
                    'est_price_30min': est_price_30min,
                    'est_price_60min': est_price_60min
                }

            return None

        except Exception as e:
            # Hata mesajını sadece önemli hatalar için göster
            if "does not have market symbol" not in str(e):
                print(f"⚠️ {symbol} kontrol hatası: {e}")
            return None

    def check_binance_announcements(self):
        """Binance yeni listeleme duyurularını kontrol et"""
        try:
            url = "https://www.binance.com/en/support/announcement/new-cryptocurrency-listing?c=48&navId=48"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            # İlk duyuruyu bul
            announcement = soup.find('div', class_='css-1ej4hfo')
            if announcement:
                title = announcement.get_text().strip()

                # Yeni duyuru varsa bildir
                if title != self.last_announcement and self.last_announcement != "":
                    alert = f"🔥 <b>BİNANCE YENİ LİSTELEME!</b>\n\n"
                    alert += f"📢 {title}\n\n"
                    alert += f"🔗 {url}"
                    self.send_telegram(alert)
                    print(f"🔥 Yeni duyuru: {title}")

                self.last_announcement = title

        except Exception as e:
            print(f"⚠️ Duyuru kontrolü hatası: {e}")

    def monitor_markets(self):
        """Ana izleme döngüsü"""
        print("\n" + "="*60)
        print("🚀 KRİPTO ALARM SİSTEMİ BAŞLATILDI")
        print("="*60)

        # Telegram test
        self.test_telegram()

        # Tüm USDT paritelerini al
        all_pairs = self.get_all_usdt_pairs()

        if not all_pairs:
            print("❌ Hiç parite bulunamadı. İnternet bağlantınızı kontrol edin.")
            return

        # Coinleri hacme göre sırala (önce popüler olanlar)
        print(f"📊 Coinler hacme göre sıralanıyor...")
        try:
            tickers = self.exchange.fetch_tickers()
            sorted_pairs = sorted(
                all_pairs,
                key=lambda x: tickers.get(x, {}).get('quoteVolume', 0),
                reverse=True
            )
            all_pairs = sorted_pairs
            print(f"✅ Sıralama tamamlandı")
        except:
            print(f"⚠️ Sıralama yapılamadı, normal devam ediliyor")

        print(f"\n✅ İzleme başladı: {len(all_pairs)} coin")
        print(f"⏰ Kontrol sıklığı: Her {CHECK_INTERVAL} saniye")
        print(f"📊 Hacim eşiği: x{VOLUME_MULTIPLIER}")
        print(f"📈 Fiyat değişim eşiği: %{PRICE_CHANGE_PERCENT}")
        print(f"💰 Minimum 24h hacim: ${MIN_VOLUME_USDT:,}")
        print(f"\n{'='*60}\n")

        last_announcement_check = 0
        scan_count = 0

        while True:
            try:
                scan_count += 1
                now = time.time()
                timestamp = datetime.now().strftime("%H:%M:%S")

                print(f"🔍 Tarama #{scan_count} - {timestamp} - {len(all_pairs)} coin kontrol ediliyor...")
                print(f"⚙️  TEST MODU: Hacim x{VOLUME_MULTIPLIER}, Fiyat %{PRICE_CHANGE_PERCENT}, Min ${MIN_VOLUME_USDT:,}")

                alerts_found = 0

                # Her coin için kontrol
                for i, symbol in enumerate(all_pairs):
                    try:
                        # İlk 3 coin için debug aktif
                        debug_mode = (i < 3)
                        if debug_mode and i == 0:
                            print(f"   🔬 DEBUG: İlk 3 coin detayı:")

                        # Hacim patlaması kontrolü
                        result = self.check_volume_spike(symbol, debug=debug_mode)

                        if result and self.can_alert(symbol):
                            alerts_found += 1

                            # Güvenilirlik skoru hesapla (1-10)
                            score = 0
                            if result['volume_increase'] >= 5: score += 3
                            elif result['volume_increase'] >= 3: score += 2
                            else: score += 1

                            if abs(result['price_change']) >= 15: score += 3
                            elif abs(result['price_change']) >= 10: score += 2
                            else: score += 1

                            if result['volume_24h'] >= 1000000: score += 3
                            elif result['volume_24h'] >= 500000: score += 2
                            elif result['volume_24h'] >= 200000: score += 1

                            # Sinyal gücü emoji ve ses
                            if score >= 8:
                                strength = "🟢 GÜÇLÜ SİNYAL"
                                recommendation = "✅ <b>Araştırıp girilebilir</b>"
                                sound_emoji = "🔥🔥🔥"  # En yüksek öncelik
                            elif score >= 5:
                                strength = "🟡 ORTA SİNYAL"
                                recommendation = "⚠️ <b>Önce araştır, dikkatli ol</b>"
                                sound_emoji = "⚠️⚠️"  # Orta öncelik
                            else:
                                strength = "🔴 ZAYIF SİNYAL"
                                recommendation = "❌ <b>Risk yüksek, bekleme önerilir</b>"
                                sound_emoji = "ℹ️"  # Düşük öncelik

                            # GİR/ÇIKIŞ fiyatları hesapla (Momentum Trading)
                            entry_price = result['current_price']
                            target_price = entry_price * 1.10  # +10% hedef
                            stop_loss_price = entry_price * 0.93  # -7% stop-loss

                            # Telegram mesajı hazırla (Keskin ses için başa emojiler)
                            alert = f"🔴🚨🔔 <b>HACİM VE FİYAT PATLADI!</b> 🔔🚨🔴\n\n"
                            alert += f"{sound_emoji} {strength} ({score}/9) {sound_emoji}\n"
                            alert += f"{recommendation}\n\n"
                            alert += f"💎 Coin: <b>{result['symbol']}</b>\n"
                            alert += f"💰 Şu Anki Fiyat: ${result['current_price']:.8f}\n"
                            alert += f"📈 Son 15dk Değişim: <b>{result['price_change']:+.2f}%</b>\n"
                            alert += f"📊 Toplam Yükseliş: <b>{result.get('price_change_total', 0):+.2f}%</b> (Henüz erken!)\n"
                            alert += f"🔊 Hacim Artışı: <b>x{result['volume_increase']:.1f}</b>\n"
                            alert += f"💵 24h Hacim: ${result['volume_24h']:,.0f}\n"
                            alert += f"⏰ Zaman: {timestamp}\n\n"
                            # TAHMİNİ SÜRE bilgileri
                            est_to_10 = result.get('est_minutes_to_10', 0)
                            est_to_15 = result.get('est_minutes_to_15', 0)
                            est_30min = result.get('est_price_30min', entry_price)
                            est_60min = result.get('est_price_60min', entry_price)
                            trend_speed = result.get('trend_speed', 0)

                            alert += f"🎯 <b>İŞLEM PLANI:</b>\n"
                            alert += f"🟢 GİR: ${entry_price:.8f}\n"
                            alert += f"✅ HEDEF: ${target_price:.8f} (+10%)\n"
                            alert += f"🛑 STOP: ${stop_loss_price:.8f} (-7%)\n\n"

                            # AI TAHMİN
                            if est_to_10 > 0 and est_to_10 < 120:
                                alert += f"🤖 <b>AI TAHMİN:</b>\n"
                                alert += f"📈 Trend Hızı: Dakikada %{trend_speed:.3f}\n"
                                if est_to_10 < 60:
                                    alert += f"⏱ %10 hedef: ~{int(est_to_10)} dakika sonra\n"
                                if est_to_15 < 90 and est_to_15 > 0:
                                    alert += f"⏱ %15 hedef: ~{int(est_to_15)} dakika sonra\n"
                                alert += f"💰 30dk tahmini: ${est_30min:.8f}\n"
                                alert += f"💰 60dk tahmini: ${est_60min:.8f}\n"
                            else:
                                alert += f"⏱ <b>TAHMİNİ SÜRE: 30-60 dakika</b>\n"
                            alert += "\n"

                            # Coin adını temizle
                            symbol_clean = result['symbol'].replace('/USDT', '')

                            # Linkler (çalışan formatlar)
                            alert += f"🔗 <a href='https://www.binance.com/en/trade/{symbol_clean}_USDT?type=spot'>Binance'de Aç</a>\n"
                            alert += f"📊 <a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol_clean}USDT'>TradingView'da Aç</a>\n"
                            alert += f"🔍 <a href='https://coinmarketcap.com/currencies/{symbol_clean.lower()}/'>CoinMarketCap</a>"

                            self.send_telegram(alert)
                            print(f"   🚨 ALARM: {symbol} - Hacim: x{result['volume_increase']:.1f}, Fiyat: {result['price_change']:+.2f}%")

                        # Her 50 coinde bir ilerleme göster
                        if (i + 1) % 50 == 0:
                            print(f"   ⏳ İlerleme: {i+1}/{len(all_pairs)} coin tarandı...")

                        # Rate limit için küçük bekleme
                        time.sleep(0.1)

                    except Exception as e:
                        # Sessizce devam et
                        pass

                if alerts_found == 0:
                    print(f"   ⚠️ Tarama tamamlandı - Alarm yok (Ayarlar çok mu sıkı?)")
                    print(f"      📊 Aranan: Hacim x{VOLUME_MULTIPLIER}+, Fiyat %{PRICE_CHANGE_PERCENT}+, Min hacim ${MIN_VOLUME_USDT:,}")
                else:
                    print(f"   🎯 Tarama tamamlandı - {alerts_found} alarm gönderildi!")

                # Binance duyuru kontrolü (5 dakikada bir)
                if now - last_announcement_check > ANNOUNCEMENT_CHECK_INTERVAL:
                    print(f"   📢 Binance duyuruları kontrol ediliyor...")
                    self.check_binance_announcements()
                    last_announcement_check = now

                # Bekleme
                print(f"   💤 {CHECK_INTERVAL} saniye bekleniyor...\n")
                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                print("\n\n⏹️  Sistem durduruldu. Görüşmek üzere!")
                break
            except Exception as e:
                print(f"❌ Beklenmeyen hata: {e}")
                print(f"   ⏳ 10 saniye sonra tekrar denenecek...")
                time.sleep(10)

# ═══════════════════════════════════════════════════════════════
# ANA PROGRAM
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Telegram ayarlarını kontrol et
    if TELEGRAM_TOKEN == "BURAYA_BOT_TOKEN_YAZ" or TELEGRAM_CHAT_ID == "BURAYA_CHAT_ID_YAZ":
        print("\n" + "="*60)
        print("⚠️  TELEGRAM AYARLARI EKSİK!")
        print("="*60)
        print("\n📱 Telegram Bot Kurulum Adımları:\n")
        print("1. Telegram'da @BotFather'ı aç")
        print("2. /newbot komutunu gönder")
        print("3. Bot için bir isim ver (örn: 'Kripto Alarm Botum')")
        print("4. Bot için bir kullanıcı adı ver (örn: 'benimkriptobot')")
        print("5. Aldığın TOKEN'ı kopyala (örn: 123456789:ABCdef...)")
        print("6. @userinfobot'u aç ve /start gönder")
        print("7. Aldığın CHAT ID'yi kopyala (örn: 987654321)")
        print("\n8. Bu dosyayı aç ve TELEGRAM_TOKEN ve TELEGRAM_CHAT_ID")
        print("   satırlarına yapıştır (satır 18-19)\n")
        print("="*60 + "\n")
        exit(1)

    # Sistemi başlat
    try:
        system = CryptoAlertSystem()
        system.monitor_markets()
    except Exception as e:
        print(f"\n❌ Sistem başlatma hatası: {e}")
        print("\nLütfen internet bağlantınızı kontrol edin.")
