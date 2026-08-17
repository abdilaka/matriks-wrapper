"""04 — Geçmiş veri (OHLC mumları).

Canlı feed ile aynı token; bar (OHLC) için ek lisans gerekmez. Tick seviyesi geçmiş
(tick/trade, tick/depth) ayrı lisans ister ve EACCES döner.

Periyotlar: 1min, 5min, 1hour, 1day.
Zaman aralığı iki şekilde verilir:
    count + timestamp   → son N bar
    start + end         → tarih aralığı (geniş aralık otomatik parçalanır, tekilleştirilir)

Çalıştır:
    python examples/04_gecmis_veri.py
"""
from datetime import date, timedelta

from matriks_wrapper.history import get_bars

# ── Son N günlük bar ──
gunluk = get_bars("GARAN", "1day", count=10)
print(f"GARAN günlük — {len(gunluk)} bar")
for b in gunluk[-3:]:
    print(f"  {b['date']}  A:{b['open']:>8} Y:{b['high']:>8} D:{b['low']:>8} K:{b['close']:>8} "
          f"vwap:{b['vwap']:>9,.2f}  adet:{b['quantity']:>12,}")

# ── Tarih aralığı — dakikalık (API'nin ~17k bar/istek limiti otomatik aşılır) ──
bitis = date.today()
baslangic = bitis - timedelta(days=5)
dakikalik = get_bars("THYAO", "1min", start=str(baslangic), end=str(bitis))
print(f"\nTHYAO dakikalık {baslangic} → {bitis}: {len(dakikalik)} bar")
if dakikalik:
    print(f"  ilk: {dakikalik[0]['date']}  son: {dakikalik[-1]['date']}")

# ── Basit kullanım: getiri ve gerçekleşen oynaklık ──
if len(gunluk) > 2:
    import statistics
    kapanis = [b["close"] for b in gunluk]
    getiriler = [(kapanis[i] / kapanis[i - 1] - 1) for i in range(1, len(kapanis))]
    print(f"\nGARAN son {len(getiriler)} günlük getiri:")
    print(f"  toplam      %{(kapanis[-1] / kapanis[0] - 1) * 100:+.2f}")
    if len(getiriler) > 1:
        yillik = statistics.stdev(getiriler) * (252 ** 0.5) * 100
        print(f"  yıllık vol  %{yillik:.1f}")

# ── Vadeli ve opsiyon da aynı şekilde ──
vadeli = get_bars("F_XU0300826", "1hour", count=5)
print(f"\nF_XU0300826 saatlik — {len(vadeli)} bar; son kapanış: "
      f"{vadeli[-1]['close'] if vadeli else '—'}")

# CSV lazımsa (Excel/grafik):
#     from matriks_wrapper.history import get_bars_csv
#     open("garan.csv", "w").write(get_bars_csv("GARAN", "1day", count=300))
