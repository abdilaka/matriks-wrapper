"""Resolve broker URLs per topic prefix from the disco-v2 document."""
import gzip
import json
import urllib.request

from . import config


def fetch_disco(issuer=None, origin=None):
    issuer = issuer or config.ISSUER
    origin = origin or config.ORIGIN
    url = f"{config.DISCO_URL}?issuer={issuer}"
    req = urllib.request.Request(url, headers={
        "Origin": origin,
        "Accept": "application/json",
        "User-Agent": "matriks-wrapper",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def _broker_for(qos, prefer="wss", tier="rt"):
    """Pick a broker URL from a qos block. tier: 'rt' (realtime) or 'dl' (delayed)."""
    if not isinstance(qos, dict):
        return None
    # transport-specific block first (wss/ws), then flat rt/dl
    blk = qos.get(prefer)
    if isinstance(blk, dict) and blk.get(tier):
        return blk[tier]
    if isinstance(qos.get(tier), str) and qos[tier]:
        return qos[tier]
    # fallback to wss flat
    for k in ("wss", "ws"):
        b = qos.get(k)
        if isinstance(b, dict) and b.get(tier):
            return b[tier]
    return None


def broker_map(disco, tier="rt"):
    """topic-prefix -> broker wss URL."""
    out = {}
    for prefix, cfg in (disco.get("mqtt") or {}).items():
        url = _broker_for(cfg.get("qos", {}), tier=tier)
        if url:
            out[prefix] = url
    return out


def resolve_topic_broker(bmap, topic, tier="rt"):
    """Find the broker for a concrete topic by longest-prefix match on the topic root.

    `mx/symbol/AKBNK@lvl2` -> prefix `mx/symbol`; `mx/derivative/X` -> `mx/derivative`.
    """
    root = "/".join(topic.split("/")[:2])
    # exact root, then any prefix the topic starts with
    if root in bmap:
        return bmap[root]
    cands = [p for p in bmap if topic.startswith(p)]
    if cands:
        return bmap[max(cands, key=len)]
    return None
