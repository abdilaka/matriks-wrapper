"""03 — Canlı veri: abone ol ve dinle.

`MatriksFeed` arka planda bir thread'de çalışır: giriş, abonelik, protobuf çözümü, token
tazeleme ve oturum ölürse yeniden giriş — hepsi içeride. Sen sadece `snapshot()` okursun
ya da her tikte çağrılacak bir fonksiyon verirsin.

⚠️ Hesap başına TEK MQTT oturumu. Bu script çalışırken tarayıcıda Matriks açıksa biri
diğerini düşürür.

Çalıştır:
    python examples/03_canli_veri.py
"""
import time

from matriks_wrapper import MatriksFeed

# Watchlist: dict ya da "watchlist.yaml" yolu.
#   indices/fx/equities → mx/symbol/<KOD>@lvl2      futures/options → mx/derivative/<KOD>@lvl2
watchlist = {
    "indices": ["XU030", "XU100"],
    "equities": ["GARAN", "THYAO", "ASELS"],
    "futures": ["F_XU0300826"],
}

# ── Yöntem 1: her tikte haber al (push) ──
sayac = {"tik": 0}


def tik_geldi(sembol, kok, veri):
    """kok: 'mx/symbol' | 'mx/derivative' — veri: çözülmüş alanlar (last/bid/ask/...)"""
    sayac["tik"] += 1
    if sayac["tik"] <= 3:
        print(f"  tik → {sembol}: last={veri.get('last')} bid={veri.get('bid')} ask={veri.get('ask')}")


feed = MatriksFeed(watchlist=watchlist, on_update=tik_geldi)
feed.start_in_thread()          # bloklamaz; gerekiyorsa Telegram'dan giriş yapar
print("Bağlanılıyor…")

try:
    # ── Yöntem 2: istediğin an anlık görüntü oku (pull) ──
    for tur in range(6):
        time.sleep(5)
        print(f"\n[{tur * 5 + 5}. sn]")
        for sembol in ("XU030", "GARAN", "THYAO", "F_XU0300826"):
            s = feed.snapshot(sembol)
            if not s:
                print(f"  {sembol:14} (henüz tik yok)")
                continue
            print(f"  {sembol:14} last={s.get('last')} bid={s.get('bid')} ask={s.get('ask')} "
                  f"hacim={s.get('volume')}")
        print(f"  istatistik: {feed.stats()}")

        # Watchlist'i CANLI değiştir — token korunur, yeniden giriş olmaz
        if tur == 2:
            print("  → watchlist değiştiriliyor (EREGL ekleniyor)")
            feed.set_watchlist({**watchlist, "equities": watchlist["equities"] + ["EREGL"]})
finally:
    feed.stop()
    print("\nDurduruldu.")

# Tikleri diske de yazmak istersen:
#     from matriks_wrapper import FileTickStore
#     feed = MatriksFeed(watchlist=watchlist, store=FileTickStore())
#     # → data/<tarih>/<sembol>.jsonl
# Kendi deponu da verebilirsin (Redis vb.): update/snapshot/stats/flush/close metodları yeterli.
