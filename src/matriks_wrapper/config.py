"""Config & watchlist loading, and topic-building rules."""
import os
import yaml

# Runtime home: where .env, .secrets/, watchlist.yaml and data/ live. Defaults to the current
# working directory so the package is portable; override with MTX_HOME when embedding.
ROOT = os.path.abspath(os.environ.get("MTX_HOME", os.getcwd()))


def _load_dotenv(path=None):
    """Minimal .env loader (no dependency): KEY=VALUE lines into os.environ (without overriding)."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv()

ORIGIN = os.environ.get("MTX_ORIGIN", "https://ziraatyatirim.matrikswebtrader.com")
ISSUER = os.environ.get("MTX_ISSUER", "ZRTYAT")
DISCO_URL = os.environ.get("MTX_DISCO_URL", "https://disco.matriksdata.com/disco-v2.json")
MQTT_USERNAME = os.environ.get("MTX_MQTT_USERNAME", "JTW")


def load_token():
    """Current MQTT password (JWT). Until track-4 auto-refresh, from env or .secrets/jwt.txt."""
    tok = os.environ.get("MTX_JWT")
    if tok:
        return tok.strip()
    p = os.path.join(ROOT, ".secrets", "jwt.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return None


def load_watchlist(path=None):
    path = path or os.path.join(ROOT, "watchlist.yaml")
    with open(path, encoding="utf-8") as f:
        wl = yaml.safe_load(f)
    return wl


def topics_for(watchlist):
    """Map the watchlist into MQTT topics by instrument class.

    Tümü @lvl2 (Karma Düzey 1 lisansı): top-of-book bidSize/askSize + volume/lastQuantity gelir.
    indices/fx/equities -> mx/symbol/<C>@lvl2 ; futures/options (VIOP) -> mx/derivative/<C>@lvl2.
    Returns list[(topic, symbol, kind)].
    """
    out = []
    for c in watchlist.get("indices", []) or []:
        out.append((f"mx/symbol/{c}@lvl2", c, "index"))
    for c in watchlist.get("fx", []) or []:
        out.append((f"mx/symbol/{c}@lvl2", c, "fx"))
    for c in watchlist.get("equities", []) or []:
        out.append((f"mx/symbol/{c}@lvl2", c, "equity"))
    for c in watchlist.get("futures", []) or []:
        out.append((f"mx/derivative/{c}@lvl2", c, "future"))
    for c in watchlist.get("options", []) or []:
        out.append((f"mx/derivative/{c}@lvl2", c, "option"))
    # de-dup preserving order
    seen, uniq = set(), []
    for t in out:
        if t[0] not in seen:
            seen.add(t[0]); uniq.append(t)
    return uniq
