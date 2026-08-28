"""Pure global-hotkey parsing shared by launcher and settings UI contracts."""


def parse_hotkey(spec):
    """Parse ctrl/alt/shift/win plus one supported key into Win32 modifiers and key code."""
    if not spec:
        return None
    modifiers = {
        "ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4,
        "win": 0x8, "super": 0x8, "cmd": 0x8,
    }
    labels = {
        "control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift",
        "win": "Win", "super": "Win", "cmd": "Win",
    }
    mods, vk, parts = 0, None, []
    for token in [part for part in spec.strip().lower().replace(" ", "").split("+") if part]:
        if token in modifiers:
            mods |= modifiers[token]
            parts.append(labels[token])
        elif token == "space":
            vk = 0x20
            parts.append("Space")
        elif len(token) == 1 and ("a" <= token <= "z" or "0" <= token <= "9"):
            vk = ord(token.upper())
            parts.append(token.upper())
        elif token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24:
            vk = 0x70 + int(token[1:]) - 1
            parts.append("F" + token[1:])
        else:
            return None
    if vk is None or mods == 0:
        return None
    return mods, vk, "+".join(parts)
