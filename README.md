# 🚨 Kripto Alarm Sistemi

**Binance'deki tüm coinleri 7/24 izler, hacim patlamaları ve fiyat hareketlerinde Telegram'dan bildirim gönderir.**

---

## 🎯 Ne Yapar?

- ✅ **Tüm Binance USDT paritelerini izler** (500+ coin)
- ✅ **Ani hacim artışlarını yakalar** (örn: hacim 3 katına çıktığında)
- ✅ **Fiyat patlamalarını bildirir** (örn: %8+ değişim)
- ✅ **Binance yeni listeleme duyurularını takip eder**
- ✅ **Telegram'a anlık bildirim gönderir**
- ✅ **Spam önleme** (aynı coin için 1 saatte 1 alarm)
- ✅ **Küçük hacimli coinleri filtreler** (scam coinleri görmezden gelir)

---

## ⚡ Hızlı Başlangıç

### 1️⃣ Telegram Bot Kur (2 dakika)

1. Telegram'da `@BotFather` aç → `/newbot` gönder
2. Bot ismi ve kullanıcı adı ver
3. TOKEN'ı al ve not et
4. `@userinfobot` aç → `/start` gönder
5. CHAT ID'ni al ve not et

### 2️⃣ Sistemi Çalıştır (1 dakika)

```bash
# 1. crypto_alert_system.py dosyasını aç
# 2. Satır 18-19'a TOKEN ve CHAT_ID yapıştır
# 3. Terminalde çalıştır:

./start.sh
```

**Hepsi bu!** 🎉

---

## 📊 Örnek Alarm

```
🚨 HACİM VE FİYAT PATLADI!

💎 Coin: QKC/USDT
💰 Fiyat: $0.00512340
📈 Fiyat Değişimi: +12.45%
📊 Hacim Artışı: x4.2
💵 24h Hacim: $2,450,000
⏰ Zaman: 14:35:22

🔗 Binance'de Aç
```

---

## ⚙️ Ayarlar (Opsiyonel)

[crypto_alert_system.py](crypto_alert_system.py) dosyasında (satır 21-28):

```python
VOLUME_MULTIPLIER = 3.0      # Hacim eşiği (2.0 = daha hassas, 5.0 = daha az alarm)
PRICE_CHANGE_PERCENT = 8.0   # Fiyat değişim eşiği (5.0 = daha hassas)
MIN_VOLUME_USDT = 100000     # Min. 24h hacim (küçük coinleri filtrele)
CHECK_INTERVAL = 60          # Kontrol sıklığı (saniye)
```

---

## 📁 Dosyalar

```
pinger/
├── crypto_alert_system.py   # Ana sistem (çalıştır bunu)
├── requirements.txt          # Python paketleri
├── start.sh                  # Başlatma scripti (Mac/Linux)
├── KURULUM.md               # Detaylı kurulum kılavuzu
└── README.md                # Bu dosya
```

---

## 🛠️ Sorun Giderme

### "Telegram gönderim hatası"
- TOKEN ve CHAT_ID doğru mu?
- Botu `/start` ile aktive ettin mi?

### "Module not found"
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### "Rate limit"
- `CHECK_INTERVAL = 120` yap (2 dakika)

---

## 💡 İpuçları

1. **7/24 çalışsın ister misin?**
   - Bulut: Replit, PythonAnywhere, AWS
   - Evde: Raspberry Pi, eski laptop

2. **Belirli coinleri görmezden gel:**
   ```python
   BLACKLIST = ['USDT', 'BTC', 'ETH']  # İstemediğin coinler
   ```

3. **Her alarm için işlem yapma!**
   - False alarm olabilir
   - Kendi araştırmanı yap
   - Stop-loss kullan

---

## ⚠️ Uyarı

- ❌ Finansal tavsiye değildir
- ❌ Para kaybetme riski vardır
- ❌ Garantili kazanç yoktur
- ✅ Sadece bilgi amaçlıdır

---

## 🚀 Gelişmiş Özellikler (İsteğe Bağlı)

Şunları da ekleyebiliriz:

- [ ] Whale Alert API (büyük transferler)
- [ ] Twitter sentiment analizi
- [ ] Kore borsaları (Upbit/Bithumb)
- [ ] Discord bildirimleri
- [ ] Web arayüzü
- [ ] Otomatik alım-satım (risky!)

İstersen söyle, eklerim! 🎯

---

## 📞 Destek

Bir sorun olursa bana sor! 🙌

---

**🔥 Başarılar! Şimdi git Telegram bot ayarlarını yap ve sistemi başlat!**
