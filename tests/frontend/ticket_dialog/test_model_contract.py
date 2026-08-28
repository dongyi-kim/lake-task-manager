"""Focused TicketDialog model contracts, runnable without the full frontend suite."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "app" / "static"
MODEL = STATIC / "components" / "ticket-dialog" / "model.js"
DIALOG = STATIC / "components" / "ui" / "TicketDialog.js"


def test_ticket_dialog_imports_presentation_policies_from_feature_folder():
    dialog = DIALOG.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")

    assert 'from "../ticket-dialog/model.js"' in dialog
    for symbol in (
        "sortChildren", "sortComments", "childCard", "timelineText", "saveSpineW",
    ):
        assert f"export function {symbol}" in model
        assert symbol in dialog
    assert "localStorage.setItem" not in dialog


def test_ticket_dialog_model_keeps_sort_and_timeline_behavior():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")

    script = f"""
      import {{ sortChildren, sortComments, timelineText, timelineBadgeClass, formatBytes }}
        from {json.dumps(MODEL.as_uri())};
      const children = [
        {{ key: 'DL-2', statusCategory: 'done', priRank: 0, assignee: '가' }},
        {{ key: 'DL-1', statusCategory: 'inprogress', priRank: 2, assignee: '나' }},
      ];
      const comments = [{{ id: 1, date: '2026-01-01T00:00:00Z' }},
                        {{ id: 2, date: '2026-01-02T00:00:00Z' }}];
      const result = {{
        children: sortChildren(children, 'pri', null).map((row) => row.key),
        newest: sortComments(comments, 'new').map((row) => row.id),
        timeline: timelineText({{ kind: 'child-status', from: 'Open', to: 'Done' }}),
        priorityClass: timelineBadgeClass({{ kind: 'priority', from: null }}, 'P1-High'),
        bytes: formatBytes(2048),
      }};
      process.stdout.write(JSON.stringify(result));
    """
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "children": ["DL-1", "DL-2"],
        "newest": [2, 1],
        "timeline": "상태 Open → Done",
        "priorityClass": "pr-1",
        "bytes": "2KB",
    }
