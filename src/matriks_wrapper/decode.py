"""Decode MQTT PUBLISH payloads to dicts via the compiled protobuf schema."""
from .proto import matriks_pb2 as pb
from google.protobuf.json_format import MessageToDict

TOPIC_TYPE = {
    "mx/symbol": pb.SymbolMessage,
    "mx/derivative": pb.DerivativeMessage,
    "mx/depth": pb.DepthTableMessage,
    "mx/depthstats": pb.DepthStatsMessage,
    "mx/trade": pb.TradeMessage,
    "mx/timestamp": pb.TimeMessage,
    "mx/session": pb.SessionMessage,
    "mx/news": pb.NewsMessage,
    "mx/pgc": pb.PgcMessage,
    "mx/fundratio": pb.FundratioMessage,
    "mx/ratingscore": pb.RatingScoreMessage,
    "mx/event": pb.EventMessage,
}


def strip_user_prefix(topic):
    """`mx/user/<sub>/mx/symbol/AKBNK` -> `mx/symbol/AKBNK` (snapshot echo)."""
    if topic.startswith("mx/user/") and topic.count("/") >= 3:
        return topic.split("/", 3)[3]
    return topic


def topic_root(topic):
    return "/".join(topic.split("/")[:2])


def symbol_of(topic):
    """`mx/symbol/AKBNK@lvl2` -> `AKBNK`; suffix stripped, None for symbol-less topics."""
    parts = topic.split("/")
    if len(parts) < 3:
        return None
    return parts[2].split("@")[0]


def decode(topic, payload):
    """Return (real_topic, root, symbol, data_dict). data_dict is the decoded message."""
    rt = strip_user_prefix(topic)
    root = topic_root(rt)
    cls = TOPIC_TYPE.get(root)
    if cls is None:
        return rt, root, symbol_of(rt), {"_raw_len": len(payload)}
    msg = cls()
    msg.ParseFromString(payload)
    data = MessageToDict(msg, preserving_proto_field_name=True)
    return rt, root, symbol_of(rt), data
