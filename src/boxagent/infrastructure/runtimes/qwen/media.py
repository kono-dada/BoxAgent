"""Keep realtime media out of local traces and context-ledger projections."""


def redact_media(value: object) -> object:
    if isinstance(value, dict):
        return {key: "[media omitted]" if key in {"image", "image_url", "audio", "video"}
                else redact_media(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_media(item) for item in value]
    if isinstance(value, str) and value.startswith(("data:image/", "data:audio/")):
        return "[media omitted]"
    return value
