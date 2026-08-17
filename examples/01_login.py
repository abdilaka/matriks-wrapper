"""01 — Giriş (login) ve token.

Matriks'e giriş iki aşamalı:

  1. UZAKTAN GİRİŞ (ilk kurulumda bir kez):  Playwright tarayıcıyı açar, captcha görselini
     Telegram'a yollar, sen cevabı yazarsın, ardından SMS OTP'yi sorar. Başarılı olunca
     `.secrets/session_store.json` (oturum materyali) + `.secrets/jwt.txt` yazılır.

  2. TOKEN TAZELEME (sonrası, otomatik):  `mint_token()` C6 çağrısıyla captcha/OTP OLMADAN
     yeni bir MarketDataToken üretir. Ömrü 5 saat. Canlı MQTT oturumunu bozmaz.

Normalde bunları elle çağırmazsın — `MatriksFeed` / `matriks-run` zaten hallediyor. Bu örnek
kimlik zincirini görmek ve kurulumu doğrulamak için.

Çalıştır:
    python examples/01_login.py
"""
import asyncio
import os
import time

from matriks_wrapper import config, mint_token, remote_login
from matriks_wrapper.auth import jwt_claims, jwt_ttl, load_session

SECRETS = os.path.join(config.ROOT, ".secrets")


def durum():
    """Elimizde geçerli bir oturum var mı?"""
    try:
        load_session()
        return True
    except Exception:
        return False


def main():
    print(f"MTX_HOME     : {config.ROOT}")
    print(f"Müşteri no   : {'var' if os.environ.get('ZRY_CUSTOMER_NO') else 'YOK — .env doldur'}")
    print(f"Oturum       : {'var' if durum() else 'yok'}  ({SECRETS}/session_store.json)")

    # ── 1. Oturum yoksa uzaktan giriş: captcha + OTP Telegram'dan sorulur ──
    if not durum():
        print("\nOturum yok → uzaktan giriş başlıyor. Telegram'ı aç, captcha ve OTP gelecek.")
        ok = asyncio.run(remote_login())
        if not ok:
            print("Giriş başarısız. .env içindeki ZRY_* ve TG_* değerlerini kontrol et.")
            return
        print("Giriş tamam, oturum kaydedildi.")

    # ── 2. Taze token (captcha/OTP yok) ──
    token, lisanslar = mint_token()
    claims = jwt_claims(token)
    kalan = jwt_ttl(token)

    print(f"\nToken        : {token[:32]}… ({len(token)} karakter)")
    print(f"  issuer     : {claims.get('iss')}")
    print(f"  bitiş      : {time.strftime('%H:%M:%S', time.localtime(claims.get('exp', 0)))}"
          f"  (kalan {kalan // 60} dk)")
    print(f"  lisans     : {len(lisanslar)} kayıt")

    # Token'ı diğer modüllere elle geçirebilirsin; geçirmezsen kendileri üretir.
    print("\nBu token'ı doğrudan kullanmak istersen:")
    print("    get_bars('GARAN', '1day', token=token)")
    print("    fetch_symbols(token=token)")


if __name__ == "__main__":
    main()
