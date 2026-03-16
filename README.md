# 🏛️ Demokratik Trading Bot v2

Binance Futures üzerinde çalışan, demokratik AI orkestrasyonuyla yönetilen,
**birikim odaklı** otomatik kripto ticaret sistemi.

---

## Ana Felsefe

> "Biriktir, büyüt, fırsatçı ol — ama asla ana pozisyonu satma."

Sistem iki paralel mantıkla çalışır:

1. **Birikim (Ana mantık)** — Seçilmiş kaliteli coinleri düzenli, düşük kaldıraçla
   biriktirir. Bu pozisyonlar asla tamamen kapatılmaz. Sadece kısmi kâr satışı yapılır,
   kalan çekirdek pozisyon sonsuza tutulur.

2. **Fırsat (Paralel mantık)** — Güçlü sinyal geldiğinde bağımsız kısa vadeli
   long/short açılır. Kârlar ana birikim bütçesini büyütür.

---

## Üç Katmanlı Pozisyon Motoru

```
KATMAN 1 — BİRİKİM  (%60 sermaye)
  Majör: BTC ETH BNB SOL → 2x-3x, haftalık DCA
  Proje: skor≥65 → 3x-5x, aylık DCA + dip alımları
  Kâr: +%30/%60/%100'de %15 sat, kalan %55 SONSUZA TUTULUR

KATMAN 2 — FIRSAT  (%25 sermaye)
  Konsensüs ≥ ±55 → 5x-15x, kısa vadeli long/short
  TP: +%5/+%10/+%20 kademeli | SL: -%3
  Kâr → birikim bütçesine aktarılır

KATMAN 3 — REZERV  (%15 sermaye)
  Birikim alımları için güvence, asla fırsat için kullanılmaz
```

---

## Demokratik Orkestrasyon (değişmedi)

5 uzman ajan → bağımsız analiz → ağırlıklı oylama → konsensüs
Her 12 işlemde lider seçimi: en başarılı ajan %35 oy ağırlığı kazanır.

| Ajan | Ağırlık |
|------|---------|
| Balina Takip | %20 |
| Teknik Analiz | %25 |
| Sentiment | %20 |
| Proje Kalite | %20 |
| Risk (veto yetkili) | %15 |

---

## Kurulum

```bash
pip install -r requirements.txt
cp config/secrets.env.example config/secrets.env

# Paper trading
python main.py --mode=paper --capital=10000

# Canlı
python main.py --mode=live --capital=1000
```

---

## Risk Uyarısı

Kripto ticareti yüksek risk içerir. Gerçek para kullanmadan önce
paper modda kapsamlı test yapın.
