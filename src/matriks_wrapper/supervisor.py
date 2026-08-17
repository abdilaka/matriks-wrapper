"""Supervisor: keeps the live data service running with a valid token, and triggers a remote
re-login (captcha+OTP via Telegram) when the Ziraat session dies.

Detection is hybrid:
  • Proactive periodic: every tick, refresh the JWT before expiry (AuthManager). If the mint is
    REJECTED (SessionExpired) the session is dead -> relogin.
  • Reactive on broker health: a mid-life session kill (e.g. another login elsewhere) leaves the
    cached JWT TTL-valid but the brokers get rejected/drop. If no broker has been up for a while,
    force a fresh mint; if that is rejected -> relogin.

Re-login coordination (single active MQTT session per account): stop the broker pool, run the
remote login, mint a fresh token, then rebuild the pool.

Run this instead of service.py for unattended operation:
    python -u src/supervisor.py
"""
import asyncio
import os
import sys
import time
from collections import defaultdict


from . import config
from . import discovery
from .decode import decode as decode_msg  # NOT `from . import decode` — __init__ shadows it with the func
from .broker import BrokerPool
from .store import FileTickStore
from .auth import AuthManager, SessionExpired, load_session
from .login import remote_login
from .telegram_relay import TelegramRelay

CHECK_EVERY = 30          # health tick seconds
BROKER_DOWN_GRACE = 90    # seconds with no broker up before forcing a refresh
RELOGIN_DEBOUNCE = 120    # min seconds between relogin attempts


class Supervisor:
    def __init__(self, store=None, on_update=None, watchlist=None, headless=None):
        self.auth = AuthManager()
        self.store = store if store is not None else FileTickStore()
        self.on_update = on_update          # optional extra callback(symbol, root, data)
        self.watchlist = watchlist          # dict, yaml path, or None (-> config.load_watchlist)
        self.headless = headless
        self.pool = None
        self._stop_event = None
        self.last_healthy = time.time()
        self.last_relogin = 0.0
        try:
            self.relay = TelegramRelay()
        except Exception:
            self.relay = None

    # --- helpers ---
    def notify(self, msg):
        print(msg)
        if self.relay:
            try: self.relay.send_text(msg)
            except Exception: pass

    def token_provider(self):
        return self.auth.current_token()

    def _watchlist(self):
        wl = self.watchlist
        if wl is None or isinstance(wl, str):
            return config.load_watchlist(wl)
        return wl

    def on_message(self, topic, payload):
        _rt, root, sym, data = decode_msg(topic, payload)
        self.store.update(sym, root, data)
        if self.on_update:
            try: self.on_update(sym, root, data)
            except Exception as e: print(f"[on_update] {e!r}")

    async def build_pool(self):
        disco = discovery.fetch_disco()
        bmap = discovery.broker_map(disco, tier="rt")
        plan = config.topics_for(self._watchlist())
        by_broker = defaultdict(list)
        for topic, sym, kind in plan:
            url = discovery.resolve_topic_broker(bmap, topic)
            if url:
                by_broker[url].append(topic)
        market = bmap.get("mx/symbol")
        if market:
            by_broker[market].append("mx/timestamp")
        pool = BrokerPool(self.token_provider, self.on_message)
        for i, (url, topics) in enumerate(by_broker.items()):
            pool.add_broker(url, topics, name=f"b{i}")
        await pool.start()
        return pool

    async def relogin(self, reason):
        if time.time() - self.last_relogin < RELOGIN_DEBOUNCE:
            return
        self.last_relogin = time.time()
        self.notify(f"🔁 Session yenileniyor ({reason}). Telegram'dan captcha+OTP gelecek.")
        if self.pool:
            await self.pool.stop(); self.pool = None
        ok = await remote_login(self.relay, headless=self.headless)
        if not ok:
            self.notify("⛔ Re-login başarısız. Bir sonraki kontrolde tekrar denenecek.")
            return
        self.auth.refresh()                      # fresh token from the new session
        self.pool = await self.build_pool()
        self.last_healthy = time.time()
        self.notify(f"✅ Servis yeniden bağlandı. Lisanslar: "
                    f"{[l.get('LicenseCode') for l in self.auth.licences]}")

    async def resubscribe(self, new_watchlist):
        """Watchlist değişince yeniden abone ol (token korunur, re-login YOK).
        Broker pool'u durdurup yeni watchlist ile yeniden kurar."""
        self.watchlist = new_watchlist
        try:
            if self.pool:
                await self.pool.stop(); self.pool = None
            self.pool = await self.build_pool()
            self.last_healthy = time.time()
            self.notify("🔄 Watchlist güncellendi; yeniden abone olundu.")
        except Exception as e:  # noqa
            self.notify(f"⚠️ Resubscribe hatası: {e!r}")

    async def ensure_session(self):
        """At startup: log in if there is no session, or if minting is already rejected."""
        try:
            load_session()
            self.auth.current_token()
        except (FileNotFoundError, SessionExpired):
            await self.relogin("ilk kurulum / oturum yok")

    async def health_tick(self):
        # 1) proactive refresh before expiry; rejection => session dead
        try:
            self.auth.current_token()
        except SessionExpired:
            await self.relogin("token reddedildi"); return
        except Exception as e:
            print(f"[health] geçici token hatası: {e!r}")   # network; retry next tick
        # 2) broker health -> detect mid-life session kill
        if self.pool:
            if any(s == "up" for s in self.pool.status().values()):
                self.last_healthy = time.time()
            elif time.time() - self.last_healthy > BROKER_DOWN_GRACE:
                try:
                    self.auth.refresh()           # force a fresh mint
                    self.last_healthy = time.time()
                    print("[health] brokerlar düşmüştü; token tazelendi, reconnect bekleniyor")
                except SessionExpired:
                    await self.relogin("brokerlar düştü + token reddedildi")

    def request_stop(self):
        """Signal the run loop to stop (thread-safe via the loop's call_soon_threadsafe)."""
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()

    async def run(self):
        self._stop_event = asyncio.Event()
        await self.ensure_session()
        if self.pool is None:
            self.pool = await self.build_pool()
        self.notify("📡 Matriks listener çalışıyor (supervisor).")
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_EVERY)
                    break  # stop requested
                except asyncio.TimeoutError:
                    pass
                await self.health_tick()
                self.store.flush()
                st = self.store.stats()
                print(f"[{time.strftime('%H:%M:%S')}] brokers={self.pool.status() if self.pool else {}} "
                      f"token_ttl={self.auth.ttl()}s symbols={st['symbols']} updates={st['updates']}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            if self.pool:
                await self.pool.stop()
            self.store.close()
            self.notify("🛑 Supervisor durduruldu.")


def main():
    """Console entry point: `matriks-run` / `python -m matriks_wrapper.supervisor`."""
    try:
        asyncio.run(Supervisor().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
