"""Callout identity shared by LTM HTML and Jira/JEditor serialization.

Native styles: JEditor 3.13.4 (Jira 8 compatible), includes/ckeditor2/defaults.js.
Jira's own stylesheet supplies backgrounds/icons. The six existing LTM types
remain the editing model; JEditor offers five native panels.
"""

import re


CALLOUT_TYPES = frozenset({"info", "note", "tip", "success", "warning", "error"})
VENDOR_CALLOUT_RE = re.compile(r"^jePanel[_-](\w+)$", re.I)
_JIRA_TO_LTM = {"info": "info", "warning": "warning", "idea": "tip",
                "grey": "note", "dashed": "note"}
# Success/error have no distinct native panel. Keep the LTM class alongside the
# closest native panel so editing in LTM never silently changes their identity.
_LTM_TO_JIRA = {"info": "info", "note": "grey", "tip": "idea",
                "success": "idea", "warning": "warning", "error": "warning"}
JIRA_PANEL_STYLES = {
    "info": "border:1px solid #9eb6d4;padding:.5em 1em .5em 2.5em",
    "warning": "border:1px solid #d4a19e;padding:.5em 1em .5em 2.5em",
    "idea": "border:1px solid #d4d39e;padding:.5em 1em .5em 2.5em",
    "grey": "border:1px solid #b4b4b4;padding:.5em 1em .5em 1em",
    "dashed": "border:1px dashed #b4b4b4;padding:.5em 1em .5em 1em",
}


def _native_panel(tokens):
    for token in tokens:
        match = VENDOR_CALLOUT_RE.fullmatch(token)
        if match and match[1].lower() in JIRA_PANEL_STYLES:
            return match[1].lower()
    return None


def callout_kind(classes):
    """Keep compatible legacy intent while honoring type changes made in Jira.

    Older synthetic jePanel_note/tip/success/error and unknown native panels
    retain their existing import behavior. Unknown panels remain notes.
    """
    tokens = classes.split() if isinstance(classes, str) else classes
    native_panel = _native_panel(tokens)
    for token in tokens:
        if token.startswith("callout-") and token[8:] in CALLOUT_TYPES:
            kind = token[8:]
            if native_panel and _JIRA_TO_LTM[native_panel] != _JIRA_TO_LTM[_LTM_TO_JIRA[kind]]:
                return _JIRA_TO_LTM[native_panel]
            return kind
    if native_panel:
        return _JIRA_TO_LTM[native_panel]
    for token in tokens:
        match = VENDOR_CALLOUT_RE.fullmatch(token)
        if match:
            native = match[1].lower()
            return _JIRA_TO_LTM.get(native, native if native in CALLOUT_TYPES else "note")
    return "info" if "callout" in tokens else None


def jira_panel_kind(kind, classes=(), preferred=None):
    """Preserve native dashed notes without retaining arbitrary HTML/CSS."""
    tokens = classes.split() if isinstance(classes, str) else classes
    # Jira may replace only its own class. That explicit choice overrides an old
    # dashed marker; the marker is needed only after LTM removes native classes.
    native = _native_panel(tokens)
    if native:
        return native
    return "dashed" if kind == "note" and preferred == "dashed" else _LTM_TO_JIRA[kind]
