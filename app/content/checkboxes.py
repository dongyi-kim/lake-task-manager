"""JEditor checkbox storage shared by HTML and Wiki serializers."""

from html import escape


def checkbox_id(attrs):
    return str(attrs.get("id") or attrs.get("data-cb-id") or "").strip()


def native_checkbox(attrs):
    identifier = checkbox_id(attrs)
    identity = f' id="{escape(identifier, quote=True)}"' if identifier else ""
    checked = ' checked="checked"' if "checked" in attrs else ""
    return f'<input{identity} type="checkbox"{checked} />'
