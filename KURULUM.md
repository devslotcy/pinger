# 🚀 Kripto Alarm Sistemi - Kurulum Kılavuzu

## 📱 1. TELEGRAM BOT KURULUMU (5 Dakika)

### Adım 1: Bot Oluştur
1. Telegram'ı aç
2. Arama çubuğuna `@BotFather` yaz ve aç
3. `/newbot` komutunu gönder
4. Bot için bir isim ver (örn: "/newbot")
5. Bot için bir kullanıcı adı ver (örn: "benimkriptoalarmbot")
6. BotFather sana bir **TOKEN** verecek
   - Örnek: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
   - Bu TOKEN'ı kopyala, bir yere not et

### Adım 2: Chat ID Öğren
1. Telegram'da arama çubuğuna `@userinfobot` yaz ve aç
2. `/start` komutunu gönder
3. Bot sana **Chat ID**'ni verecek
   - Örnek: `987654321`
   - Bu numarayı kopyala, bir yere not et

### Adım 3: Botu Aktive Et
1. BotFather'dan aldığın bot kullanıcı adını ara (örn: @benimkriptoalarmbot)
2. `/start` komutu gönder
3. Artık bot hazır!

---

## 💻 2. SİSTEM KURULUMU

### Mac/Linux:
```bash
# 1. Terminali aç

# 2. Proje klasörüne git
cd /Users/dev/development/pinger

# 3. Sanal ortam oluştur (opsiyonel ama önerilir)
python3 -m venv venv
source venv/bin/activate

# 4. Gerekli paketleri yükle
pip install -r requirements.txt
```

### Windows:
```bash
# 1. CMD veya PowerShell aç

# 2. Proje klasörüne git
cd C:\Users\...\pinger

# 3. Sanal ortam oluştur
python -m venv venv
venv\Scripts\activate

# 4. Gerekli paketleri yükle
pip install -r requirements.txt
```

---

## ⚙️ 3. AYARLARI YAP

1. `crypto_alert_system.py` dosyasını bir metin editörü ile aç
2. **18. satıra** git:
   ```python
   TELEGRAM_TOKEN = "BURAYA_BOT_TOKEN_YAZ"
   ```
   - Buraya BotFather'dan aldığın TOKEN'ı yapıştır
   - Örnek: `TELEGRAM_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"`

3. **19. satıra** git:
   ```python
   TELEGRAM_CHAT_ID = "BURAYA_CHAT_ID_YAZ"
   ```
   - Buraya userinfobot'tan aldığın CHAT ID'yi yapıştır
   - Örnek: `TELEGRAM_CHAT_ID = "987654321"`

4. Dosyayı kaydet (Ctrl+S veya Cmd+S)

---

## 🚀 4. SİSTEMİ ÇALIŞTIR

```bash
python crypto_alert_system.py
```

### İlk Çalıştırmada Ne Olur?
1. ✅ Telegram'a "Sistem başlatıldı" mesajı gelir
2. 📊 Tüm Binance USDT paritelerini tarar (500+ coin)
3. 🔍 Her 60 saniyede bir tüm coinleri kontrol eder
4. 🚨 Hacim patlaması yakalayınca sana Telegram'dan haber verir

---

## ⚙️ 5. AYARLARI ÖZELLEŞTİR

`crypto_alert_system.py` dosyasının başında (satır 21-28) ayarlar var:

```python
# Hacim kaç kat artarsa alarm çalsın
VOLUME_MULTIPLIER = 3.0  # 3 = %300 artış. Daha hassas olsun istersen 2.0 yap

# Fiyat % kaç değişirse alarm
PRICE_CHANGE_PERCENT = 8.0  # 8 = %8 değişim. Daha hassas: 5.0

# Minimum 24 saat hacim (küçük coinleri görmezden gel)
MIN_VOLUME_USDT = 100000  # 100k$. Daha küçükler: 50000

# Kaç saniyede bir kontrol
CHECK_INTERVAL = 60  # 60 saniye = 1 dakika. Daha hızlı: 30
```

---

## 📊 ÖRNEK ALARM MESAJI

Telegram'a şöyle bir mesaj gelir:

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

## 🛠️ SORUN GİDERME

### "Module not found" Hatası
```bash
# Paketleri tekrar yükle
pip install --upgrade ccxt requests beautifulsoup4
```

### "Telegram gönderim hatası"
- TOKEN ve CHAT_ID doğru mu kontrol et
- Botu `/start` ile aktive ettin mi?
- İnternete bağlı mısın?

### "Rate limit" Hatası
- `CHECK_INTERVAL` değerini 120'ye çıkar (2 dakika)

### "Too many requests" Hatası
- Binance bazen rate limit koyar, 5 dakika bekle

---

## 💡 İPUÇLARI

### 1. 7/24 Çalışsın İstiyorsan (Bulut)
- **Replit**: Ücretsiz, kodunu buraya yükle
- **PythonAnywhere**: 3 aya kadar ücretsiz
- **AWS EC2**: 12 ay ücretsiz trial
- **Raspberry Pi**: Evinde çalışsın

### 2. Spam Önleme
- Sistem aynı coin için 1 saatte 1 alarm verir
- Yanlış alarmları azaltmak için `VOLUME_MULTIPLIER` ve `PRICE_CHANGE_PERCENT` ayarla

### 3. Belirli Coinleri Görmezden Gel
Dosyada **BLACKLIST** var (satır 27):
```python
BLACKLIST = ['USDT', 'BUSD', 'BTC', 'ETH']  # İstemediğin coinler
```

### 4. Sadece Belirli Coinleri İzle
Bu özelliği eklememizi istersen söyle, onu da eklerim.

---

## 📞 DESTEK

Bir sorun olursa bana sor, birlikte çözeriz! 🚀

---

## ⚠️ UYARILAR

1. **Finansal Tavsiye Değil**: Bu sistem sadece bildirim gönderir
2. **Kendi Riskin**: Alım-satım kararları tamamen sana ait
3. **False Alarm**: Bazen yanlış alarm verebilir, her sinyale körü körüne inanma
4. **Stop-Loss Kullan**: Her işlemde %5-7 zarar limiti koy

---

## 🎯 Sonraki Adımlar

Sistem çalışıyorsa, şunları da ekleyebilirim:

1. ✅ Whale Alert API (büyük transferler)
2. ✅ Twitter sızıntı takibi
3. ✅ Kore borsaları (Upbit/Bithumb)
4. ✅ Discord bildirimleri
5. ✅ Web arayüzü (tarayıcıdan izle)

Hangisini istersen söyle! 🚀
