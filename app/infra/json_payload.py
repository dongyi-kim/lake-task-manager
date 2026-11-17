"""JSON payload accounting without whole-payload scratch copies."""
import json


def encoded_size(value):
    """Count the exact stored JSON bytes with bounded scratch for enormous strings."""
    active = set()

    def string_size(text):
        # JSON escaping is independent for each character. Count the outer quotes
        # once, retaining at most one small escaped/UTF-8 fragment at a time.
        return 2 + sum(len(json.dumps(text[offset:offset + 16384], ensure_ascii=False)
                           .encode("utf-8")) - 2 for offset in range(0, len(text), 16384))

    def measure(item):
        if isinstance(item, str):
            return string_size(item)
        if not isinstance(item, (dict, list, tuple)):
            if item is None or isinstance(item, (bool, int, float)):
                return len(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
            return string_size(str(item))
        identity = id(item)
        if identity in active:
            raise ValueError("Circular reference detected")
        active.add(identity)
        try:
            size = 2 + max(0, len(item) - 1)
            if isinstance(item, dict):
                for key, child in item.items():
                    # Let stdlib validate/normalize non-string keys; remove only
                    # the compact wrapper's braces, colon and null value.
                    key_size = (string_size(key) if isinstance(key, str) else
                                len(json.dumps({key: None}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) - 7)
                    size += key_size + 1 + measure(child)
            else:
                size += sum(measure(child) for child in item)
            return size
        finally:
            active.remove(identity)

    return measure(value)
