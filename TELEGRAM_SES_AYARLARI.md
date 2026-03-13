# 🔔 Telegram Keskin Ses Ayarları

## 🎯 Sistem Şimdi Nasıl Çalışıyor?

Artık her alarm **ses şiddetine göre farklı emoji** ile geliyor:

```
🔥🔥🔥 GÜÇLÜ SİNYAL (8-9 puan)
→ En yüksek ses! Hemen gir!

⚠️⚠️ ORTA SİNYAL (5-7 puan)
→ Orta ses, araştır sonra gir

ℹ️ ZAYIF SİNYAL (1-4 puan)
→ Düşük ses, genelde girme
```

---

## 📱 Telegram'da Sesli Bildirimi Aktif Et

### **Adım 1: Bot Sesini Ayarla**

#### **iPhone:**
```
1. Telegram'ı aç
2. Botunla konuşmaya gir
3. Üstte bot adına tıkla
4. "Notifications" → "Sound"
5. En keskin sesi seç:
   ✅ "Aurora" (EN KESKİN!)
   ✅ "Bamboo" (Keskin)
   ✅ "Glass" (Uyarıcı)
```

#### **Android:**
```
1. Telegram'ı aç
2. Botunla konuşmaya gir (uzun bas)
3. ⋮ (3 nokta) → "Notifications"
4. "Sound" → En yüksek ses
5. "Importance" → "High" veya "Urgent"
```

---

### **Adım 2: Telegram Genel Ayarları**

#### **Tüm Bildirimler Açık Olsun:**
```
1. Telegram → Settings → Notifications
2. "Private Chats" → ON
3. "Sound" → En yüksek
4. "Vibrate" → ON
5. "In-App Sounds" → ON
```

#### **Sessiz Saatleri Kapat:**
```
1. Settings → Notifications
2. "Notification Exceptions" kontrol et
3. Botunu "Always Notify" listesine ekle
```

---

### **Adım 3: Telefon Ayarları**

#### **iPhone:**
```
1. Settings → Notifications → Telegram
2. "Allow Notifications" → ON
3. "Sounds" → ON
4. "Badges" → ON
5. "Banner Style" → Persistent
6. "Show Previews" → Always
```

#### **Android:**
```
1. Settings → Apps → Telegram
2. "Notifications" → ON
3. Channel: "Messages" → Importance: High
4. Sound: En yüksek
5. Vibration: ON
6. "Override Do Not Disturb" → ON (Önemli!)
```

---

## 🔥 Pro İpucu: Özel Ses Dosyası

Daha da keskin ses istiyorsan:

### **iPhone:**
```
1. iTunes'dan keskin bir alarm sesi indir
2. Telegram Settings → Notifications → Sound
3. "Add Custom Sound" seç
4. Ses dosyasını yükle
```

### **Android:**
```
1. /storage/emulated/0/Notifications/ klasörüne
   keskin bir .mp3 dosyası koy
2. Telegram Notifications → Sound
3. Yeni sesin görünecek
```

**Tavsiye Edilen Sesler:**
- "Air Raid Siren" (Hava saldırı sesi - EN KESKİN!)
- "Emergency Alert"
- "Fire Alarm"
- "Tritone" (iPhone alarm sesi)

---

## 🎯 Test Et!

Ses ayarlarını yaptıktan sonra test et:

```bash
# Terminal'den test mesajı gönder
curl -X POST "https://api.telegram.org/bot[TOKEN]/sendMessage" \
  -d "chat_id=[CHAT_ID]" \
  -d "text=🔴🚨🔔 TEST! Sesi duydun mu? 🔔🚨🔴"
```

---

## 💡 Ekstra İpuçları

### **1. Gece Modunu Kapat**
```
Önemli alarmları kaçırmamak için:
- iPhone: Do Not Disturb'ü kapat veya
  Telegram'ı istisna listesine ekle
- Android: "Override DND" ON
```

### **2. Ekran Kilitlendeyken Görünsün**
```
iPhone:
Settings → Notifications → Show Previews → Always

Android:
Settings → Lock Screen → Show all content
```

### **3. Titreşim Şiddeti**
```
Android'de:
Settings → Sound → Vibration intensity → Max
```

---

## 🚨 Sorun Giderme

### **"Ses gelmiyor!"**
```
✅ Telefon sessiz modda mı? (Kapatmalısın)
✅ Telegram bildirimleri açık mı?
✅ Bottan bildirim izni var mı?
✅ "Notification Exceptions" kontrol et
```

### **"Sadece sessiz bildirim geliyor"**
```
✅ Bot ayarlarından "Sound" kontrol et
✅ Telegram → Settings → Notifications → Private Chats → Sound
✅ Telefon ayarlarından Telegram seslerini aç
```

### **"Ekranda görünmüyor"**
```
✅ Banner Style: "Persistent" (iPhone)
✅ Show on Lock Screen: ON
✅ Badge: ON
```

---

## 🎯 Mesaj Formatı

Artık alarmlar böyle gelecek:

```
🔴🚨🔔 HACİM VE FİYAT PATLADI! 🔔🚨🔔

🔥🔥🔥 GÜÇLÜ SİNYAL (8/9) 🔥🔥🔥
✅ Araştırıp girilebilir

💎 Coin: DOLO/USDT
💰 Şu Anki Fiyat: $0.03664000
📈 Fiyat Değişimi: +3.5%
📊 Hacim Artışı: x8.3
💵 24h Hacim: $1,312,517
⏰ Zaman: 15:30:00

🎯 İŞLEM PLANI:
🟢 GİR: $0.03664000
✅ HEDEF: $0.04030400 (+10%)
🛑 STOP: $0.03407520 (-7%)
⏱ SÜRE: 30-60 dakika

🔗 Binance'de Aç
📊 TradingView'da Aç
🔍 CoinMarketCap
```

**Emojiler:**
- 🔴🚨🔔 = Telegram'a "uyarı sesi çal" sinyali
- 🔥🔥🔥 = Güçlü sinyal (yüksek ses)
- ⚠️⚠️ = Orta sinyal (orta ses)
- ℹ️ = Zayıf sinyal (düşük ses)

---

**🎧 Şimdi sistemi yeniden başlat ve test et!**
