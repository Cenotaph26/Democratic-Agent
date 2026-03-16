# Railway Kurulum Kılavuzu — Demokratik Trading Bot

## Genel Bakış

Railway, GitHub repo'nuzdan otomatik deploy eden bir bulut platformudur.
Bot web sunucusu olmayan bir "worker" process olarak çalışır —
7/24 arka planda döner, log izleyebilirsiniz.

---

## ADIM 1 — GitHub Repo Hazırlığı

### 1.1 Yeni repo oluştur

GitHub'a gir → "New repository" → isim ver (örn: `trading-bot`) → Create

### 1.2 Projeyi GitHub'a yükle

Bilgisayarında terminalden:

```bash
cd demokratik-trading-bot/

git init
git add .
git commit -m "İlk kurulum"
git branch -M main
git remote add origin https://github.com/KULLANICI_ADIN/trading-bot.git
git push -u origin main
```

⚠️ `secrets.env` dosyası `.gitignore` sayesinde yüklenmez — API anahtarların güvende.

---

## ADIM 2 — Railway Hesabı ve Proje

### 2.1 Railway'e kaydol
→ https://railway.app → "Login with GitHub" ile gir

### 2.2 Yeni proje oluştur
Dashboard → "New Project" → "Deploy from GitHub repo"

### 2.3 Repo seç
Az önce oluşturduğun `trading-bot` repo'sunu seç.

Railway otomatik olarak:
- Python 3.11 ortamı kurar
- `requirements.txt` paketleri yükler
- `railway.json` ile başlatır

---

## ADIM 3 — Environment Variables (API Anahtarları)

Bu adım en kritik adım. Railway panelinde:

**Service → Variables** sekmesine git → aşağıdaki değişkenleri ekle:

| Değişken | Değer | Açıklama |
|----------|-------|----------|
| `BOT_MODE` | `paper` | `paper` ile başla! Canlı için `live` |
| `INITIAL_CAPITAL` | `1000` | Başlangıç sermayesi (USDT) |
| `SCAN_INTERVAL` | `60` | Yeni coin tarama sıklığı (saniye) |
| `TICK_INTERVAL` | `10` | TP/SL kontrol sıklığı (saniye) |
| `BINANCE_API_KEY` | `xxx` | Binance API Key |
| `BINANCE_API_SECRET` | `xxx` | Binance API Secret |
| `TELEGRAM_BOT_TOKEN` | `xxx` | (İsteğe bağlı) Bildirimler için |
| `TELEGRAM_CHAT_ID` | `xxx` | (İsteğe bağlı) |

### Binance API Key nasıl alınır?

1. https://binance.com → Hesap → API Yönetimi
2. "API Oluştur" → isim ver
3. İzinler:
   - ✅ Okuma
   - ✅ Futures İşlem (paper için gerekmez)
   - ❌ Para çekme — ASLA işaretleme
4. IP kısıtlaması → Railway IP'lerini eklemek isterseniz: Railway paneli → Settings → görünür IP

---

## ADIM 4 — Deploy

Variables eklendikten sonra:

**Deploy** butonuna bas → Railway build başlar (1-3 dakika)

Build logları:
```
✓ Python 3.11 kuruldu
✓ requirements.txt yüklendi
✓ python-binance, ccxt, aiohttp...
✓ Build tamamlandı
✓ Worker başlatıldı
```

---

## ADIM 5 — Logları İzle

Railway paneli → Service → **Logs** sekmesi

Başarılı başlangıç şöyle görünür:
```
═══════════════════════════════════════════════════════
  🏛️  Demokratik Trading Bot — PAPER
  Başlangıç sermayesi: $1000.00
  Birikim: %60 | Fırsat: %25 | Rezerv: %15
═══════════════════════════════════════════════════════
✅ Tüm bileşenler hazır. Ana döngü başlıyor...

[Scanner] 15 aday tarandı
[Parliament] BTCUSDT için oylama başlıyor...
[AccumEngine] ✅ BTCUSDT BİRİKİM AÇILDI | Tier:majör | 2x | ...
[OppEngine] 🎯 SOLUSDT FIRSAT LONG | 10x | $25.00 @ 148.30
```

---

## ADIM 6 — Paper'dan Live'a Geçiş

Paper modda en az **2 hafta** test ettikten sonra:

Railway → Variables → `BOT_MODE` değerini `paper` → `live` yap → Redeploy

---

## Maliyetler

Railway fiyatlandırması (2025):
- **Hobby Plan**: $5/ay — bot için yeterli
- **Pro Plan**: $20/ay — daha fazla kaynak

Bot 7/24 çalışır, RAM kullanımı ~150-300MB arası olur.

---

## Sorun Giderme

### Bot hemen kapanıyor
→ Logs'a bak, genellikle eksik environment variable'dır
→ `BOT_MODE`, `INITIAL_CAPITAL` mutlaka olmalı

### "Module not found" hatası
→ `requirements.txt` düzgün commit'lendi mi kontrol et

### Binance bağlantı hatası
→ Paper modda API key gerekmez, paper ile test et önce
→ Canlıda: API key izinleri kontrol et

### Bot yeniden başlıyor
→ Normal: `railway.json`'da `ON_FAILURE` restart ayarlı
→ Çok sık oluyorsa: Logs'ta hatayı bul

---

## Güncelleme

Kodu değiştirip push ettiğinde Railway otomatik yeniden deploy eder:

```bash
git add .
git commit -m "Güncelleme açıklaması"
git push
```

Railway bunu algılar → yeni build → otomatik restart.

---

## Güvenlik Notları

- API Secret asla GitHub'a gitmesin (.gitignore bunu önler)
- Binance'te "Para Çekme" iznini ASLA verme
- Paper modda test et, sonra live'a geç
- Günlük zarar limitini düşük tut (%3-5)
