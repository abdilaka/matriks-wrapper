"""02 — Sembol listesi (enstrüman ana verisi).

Tek çağrıda tüm evren: hisse, vadeli, opsiyon, endeks, döviz, varant, fon — dayanak varlık,
kullanım fiyatı, vade, call/put ve **sözleşme büyüklüğü** ile birlikte. Watchlist'ini bu
listeden üretebilirsin.

Çalıştır:
    python examples/02_semboller.py
"""
from collections import Counter

from matriks_wrapper import fetch_symbols, normalize_symbols

ham = fetch_symbols()                 # satıcı alan adlarıyla ham kayıtlar
kayitlar = normalize_symbols(ham)     # okunur anahtarlar
canli = [k for k in kayitlar if not k["deleted"]]

print(f"{len(kayitlar)} sembol ({len(canli)} aktif)\n")

print("Tip dağılımı")
for tip, adet in Counter(k["type"] for k in canli).most_common():
    print(f"  {tip:14} {adet}")

# ── Bir hissenin opsiyon zinciri ──
print("\nGARAN opsiyonları — en yakın vade")
opsiyonlar = [k for k in canli if k["type"] == "option" and k["underlying"] == "GARAN"]
if opsiyonlar:
    vade = min(k["expiry"] for k in opsiyonlar if k["expiry"])
    zincir = sorted((k for k in opsiyonlar if k["expiry"] == vade),
                    key=lambda k: (k["strike"] or 0, k["call_put"] or ""))
    print(f"  vade {vade} · {len(zincir)} kontrat · sözleşme büyüklüğü {zincir[0]['contract_size']:.0f}")
    for k in zincir[:6]:
        print(f"    {k['symbol']:26} {k['call_put']:4} strike {k['strike']:>9,.2f}")

# ── Endeks üyeliği: BIST30 hisseleri ──
bist30 = [k["symbol"] for k in canli if k["type"] == "equity" and "BIST30" in (k["indices"] or [])]
print(f"\nBIST30 ({len(bist30)}): {', '.join(sorted(bist30)[:12])} …")

# ── Vadeli kontratlar ──
vadeliler = [k for k in canli if k["type"] == "future" and k["underlying"] == "XU030"]
print(f"\nXU030 vadelileri: {', '.join(sorted(k['symbol'] for k in vadeliler))}")

# ── Buradan watchlist üret ──
watchlist = {
    "indices": ["XU030", "XU100"],
    "equities": sorted(bist30)[:5],
    "futures": sorted({k["feed_code"] for k in vadeliler})[:2],   # feed_code tekrar edebilir → set
}
print(f"\nÜretilen watchlist: {watchlist}")
print("(03_canli_veri.py bunu doğrudan MatriksFeed'e verebilir)")
