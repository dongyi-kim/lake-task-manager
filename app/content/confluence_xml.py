"""Bounded, network-free parsing of Confluence storage XML fragments."""
import re
import xml.etree.ElementTree as ET

AC = "{http://atlassian.com/content}"
RI = "{http://atlassian.com/resource/identifier}"


def parse_storage(value):
    markup = re.sub(r'<!\[CDATA\[[\s\S]*?\]\]>', '', value)
    if len(value) > 2_000_000 or re.search(r"<!DOCTYPE|<!ENTITY", markup, re.I):
        raise ValueError("지원하지 않는 Confluence 원문입니다.")
    value = re.sub(r'<!\[CDATA\[[\s\S]*?\]\]>|&nbsp;',
                   lambda match: '&#160;' if match[0] == '&nbsp;' else match[0], value)
    root = ET.fromstring('<root xmlns:ac="http://atlassian.com/content" xmlns:ri="http://atlassian.com/resource/identifier">'
                         + value + '</root>')
    stack, count = [(root, 0)], 0
    while stack:
        node, depth = stack.pop(); count += 1
        if depth > 80 or count > 50000:
            raise ValueError("문서 구조가 너무 복잡합니다.")
        stack.extend((child, depth + 1) for child in node)
    return root
