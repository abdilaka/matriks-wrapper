"""Matriks live listener service: subscribe the watchlist, decode, persist.

Usage:
    set MTX_JWT=<token>   # or .secrets/jwt.txt   (until track-4 auto-refresh)
    python -u src/service.py
"""
import asyncio
import os
import sys
import time
from collections import defaultdict


from . import config
from . import discovery
from .decode import decode as decode_msg  # __init__ shadows the `decode` submodule with the func
from .broker import BrokerPool
from .store import FileTickStore

# Prefer auto-refreshing AuthManager (needs .secrets/session_store.json); else static token.
try:
    from .auth import AuthManager
    _AUTH = AuthManager()
except Exception:
    _AUTH = None


def token_provider():
    if _AUTH is not None:
        try:
            return _AUTH.current_token()
        except Exception as e:
            print(f"auth refresh failed ({e!r}); falling back to static token")
    return config.load_token()


async def run_listener():
    token = token_provider()
    if not token:
        print("ERROR: no token (need .secrets/session_store.json, or MTX_JWT / .secrets/jwt.txt)")
        return
    if _AUTH is not None and _AUTH.ttl() > 0:
        print(f"auth: AuthManager active, token ttl {_AUTH.ttl()}s, licences "
              f"{[l.get('LicenseCode') for l in _AUTH.licences]}")

    wl = config.load_watchlist()
    plan = config.topics_for(wl)              # [(topic, symbol, kind)]
    print(f"watchlist: {len(plan)} topics across "
          f"{sum(len(v or []) for v in wl.values())} symbols")

    disco = discovery.fetch_disco()
    bmap = discovery.broker_map(disco, tier="rt")

    # group topics by resolved broker
    by_broker = defaultdict(list)
    unresolved = []
    for topic, sym, kind in plan:
        url = discovery.resolve_topic_broker(bmap, topic)
        if url:
            by_broker[url].append(topic)
        else:
            unresolved.append(topic)
    # always include the clock on the market broker
    market = bmap.get("mx/symbol")
    if market:
        by_broker[market].append("mx/timestamp")
    if unresolved:
        print(f"  {len(unresolved)} topics had no broker (e.g. {unresolved[:3]})")

    store = FileTickStore()

    def on_message(topic, payload):
        rt, root, sym, data = decode_msg(topic, payload)
        store.update(sym, root, data)

    pool = BrokerPool(token_provider=token_provider, on_message=on_message)
    for i, (url, topics) in enumerate(by_broker.items()):
        print(f"  broker[{i}] {url} <- {len(topics)} topics")
        pool.add_broker(url, topics, name=f"b{i}")

    await pool.start()
    print("listening… (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(10)
            store.flush()
            st = store.stats()
            print(f"[{time.strftime('%H:%M:%S')}] brokers={pool.status()} "
                  f"symbols={st['symbols']} updates={st['updates']}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await pool.stop()
        store.close()
        print("stopped.")


def main():
    """Console entry point: `matriks-listen` / `python -m matriks_wrapper.listener` (no auto re-login)."""
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
