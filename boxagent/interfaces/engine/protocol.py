"""Versioned JSON-lines protocol shared by the Engine client and server."""

import json


PROTOCOL_VERSION = 1


def encode(message):
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def decode(line):
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("Engine 消息必须是 JSON object")
    return message
