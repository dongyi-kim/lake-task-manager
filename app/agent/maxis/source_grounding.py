"""One canonical stdlib-only lexical view for local and embedded Dify guards."""
import json
import math


def source_lexical_parts(value):
    """Return original keys/scalars separately; do not modify evidence or claims.

    Decode exactly one fully strict bounded JSON container. Unsupported input keeps
    its literal interpretation. Numeric spellings are retained; no Unicode/HTML
    normalization occurs here. The guard tokenizes each part once, independently.
    """
    class Number(str):
        pass

    def pairs(rows):
        out = {}
        for key, item in rows:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = item
        return out

    def invalid(_):
        raise ValueError("nonfinite number")

    def number(raw):
        if not math.isfinite(float(raw)):
            raise ValueError("nonfinite number")
        return Number(raw)

    try:
        if (not isinstance(value, str) or len(value) > 1500
                or len(value.encode("utf-8")) > 4500
                or not value.lstrip().startswith(("{", "["))):
            return (value,)
        parsed = json.loads(value, object_pairs_hook=pairs, parse_constant=invalid,
                            parse_int=Number, parse_float=number)
        if not isinstance(parsed, (dict, list)):
            return (value,)
        parts, remaining = [], [1024]

        def collect(item, depth=0):
            remaining[0] -= 1
            if depth > 8 or remaining[0] < 0:
                raise ValueError("shape limit")
            if isinstance(item, str):
                item.encode("utf-8")
                parts.append(item)
            elif isinstance(item, (dict, list)):
                if len(item) > 100:
                    raise ValueError("collection limit")
                for key, child in item.items() if isinstance(item, dict) else enumerate(item):
                    if isinstance(item, dict):
                        key.encode("utf-8")
                        parts.append(key)
                    collect(child, depth + 1)
            else:
                parts.append("null" if item is None else "true" if item else "false")

        collect(parsed)
        return tuple(parts)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return (value,)  # No prefix extraction, duplicate winner or partial decode.
