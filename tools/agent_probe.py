# tools/agent_probe.py — 지속 시나리오 루프용 실측 러너 (출력 **전문**을 사람이 읽는다).
#
# 실행: python -X utf8 tools/agent_probe.py <파일.json>
#   파일: [{"id": "...", "turns": ["...", ...]}, ...]
# 체커 없음 — 이 러너의 산출물은 pass/fail 이 아니라 **읽을거리**다(battery green ≠ 품질).
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT", "gpt-4o-mini")
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")

from app.agent.workflow import session  # noqa: E402

cases = json.load(open(sys.argv[1], encoding="utf-8"))
for c in cases:
    tid = ""
    print(f"\n{'=' * 70}\n### {c['id']}")
    for q in c["turns"]:
        t0 = time.time()
        o = session.ask(q, thread_id=tid)
        tid = o["thread_id"]
        print(f"\n--- Q: {q[:100]}  ({time.time()-t0:.0f}s, intent={o.get('intent')})")
        print(o.get("reply") or "(빈 응답)")
        for qq in o.get("questions") or []:
            print(f"  [질문:{qq.get('kind')}] {qq.get('question')} | {qq.get('options')}")
        p = o.get("pending")
        if p:
            if p.get("action") == "update_tickets":
                print(f"  [일괄변경카드] keys={p.get('keys')} changes={p.get('changes')}")
            elif p.get("action") == "update_ticket":
                print(f"  [변경카드] {p.get('key')} changes={p.get('changes')} "
                      f"comment={bool(p.get('comment'))}")
            else:
                print(f"  [승인카드] mode={p.get('mode')} items={len(p.get('items') or [])} "
                      f"children={len(p.get('children') or [])}")
        if o.get("error"):
            print(f"  [오류] {o['error']}")
