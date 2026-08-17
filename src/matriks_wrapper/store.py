"""Tick persistence + live snapshot. v1 = local files; Redis-ready interface.

- Live snapshot: per-symbol merged dict (partial proto3 updates applied on top).
- Raw log: append each update as JSONL under data/<YYYY-MM-DD>/<symbol>.jsonl.
Swap `FileTickStore` for a `RedisTickStore` later without touching the service.
"""
import json
import os
import time

from . import config


class MemoryTickStore:
    """In-memory live snapshot only (no disk). Ideal when embedding for live calculations
    option IV) — read `snapshot(sym)` from your app. Same interface as FileTickStore."""
    def __init__(self):
        self.snap = {}
        self.counts = {}

    def update(self, symbol, root, data, ts=None):
        if symbol is None:
            symbol = root.replace("/", "_")
        s = self.snap.setdefault(symbol, {})
        s.update(data)
        s["_root"] = root
        self.counts[symbol] = self.counts.get(symbol, 0) + 1

    def flush(self):
        pass

    def snapshot(self, symbol):
        return self.snap.get(symbol)

    def stats(self):
        return {"symbols": len(self.snap), "updates": sum(self.counts.values())}

    def close(self):
        pass


class FileTickStore:
    def __init__(self, base=None):
        self.base = base or os.path.join(config.ROOT, "data")
        self.snap = {}            # symbol -> merged dict
        self.counts = {}          # symbol -> update count
        self._fh = {}             # symbol -> open file handle
        self._day = None

    def _file(self, symbol):
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            for fh in self._fh.values():
                try: fh.close()
                except Exception: pass
            self._fh = {}
            self._day = day
        if symbol not in self._fh:
            d = os.path.join(self.base, day)
            os.makedirs(d, exist_ok=True)
            self._fh[symbol] = open(os.path.join(d, f"{symbol}.jsonl"), "a", encoding="utf-8")
        return self._fh[symbol]

    def update(self, symbol, root, data, ts=None):
        if symbol is None:
            symbol = root.replace("/", "_")  # e.g. timestamp/session
        # merge into live snapshot (partial updates)
        s = self.snap.setdefault(symbol, {})
        s.update(data)
        s["_root"] = root
        self.counts[symbol] = self.counts.get(symbol, 0) + 1
        # append raw
        rec = {"t": ts or time.time(), "root": root, **data}
        fh = self._file(symbol)
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def flush(self):
        for fh in self._fh.values():
            try: fh.flush()
            except Exception: pass

    def snapshot(self, symbol):
        return self.snap.get(symbol)

    def stats(self):
        return {"symbols": len(self.snap), "updates": sum(self.counts.values())}

    def close(self):
        for fh in self._fh.values():
            try: fh.close()
            except Exception: pass
