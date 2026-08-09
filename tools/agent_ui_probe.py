"""agent_ui_probe — **화면에서** 시나리오를 돌리고 렌더 결과까지 검수한다.

`tools/agent_probe.py` 는 텍스트만 본다. 그런데 실제 사고의 절반은 텍스트가 아니라
**렌더**에서 났다(속성 누출, 뱃지/링크 혼재, 깨진 프사, 빈 표, 미치환 마크다운).
그래서 브라우저(playwright)로 AI 탭을 직접 몰고, 답변 HTML 을 규칙으로 훑는다.

    python -X utf8 tools/agent_ui_probe.py cases.json [--out report.md]

케이스 파일: [{"id": "...", "turns": ["첫 질문", "두 번째 질문", ...],
               "answer": "auto"|"skip"}]   # 되묻기가 뜨면 자동 응답할지

판정은 사람(=읽는 쪽)이 한다. 이 도구는 **기계적으로 확실한 위반**만 잡고,
답변 원문을 그대로 옮겨 정성 판독에 쓸 수 있게 한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8000"


# ── 렌더 규칙 — 어긴 것만 보고한다 ────────────────────────────────────────
def render_findings(html: str, text: str) -> list[str]:
    bad: list[str] = []

    def hit(cond, msg):
        if cond:
            bad.append(msg)

    # ① HTML 속성이 본문으로 샜다(툴팁 안에서 치환이 또 일어난 사고)
    hit(re.search(r'"\s*>\s*\[\d', text), '속성 누출: `">[n]` 이 글자로 보인다')
    hit("title=" in text or "class=" in text, "태그 속성이 본문 텍스트에 노출됐다")
    # ② 이스케이프 잔여
    hit(re.search(r"&(?:lt|gt|amp|quot|#\d+);", text), "이스케이프 실체참조가 글자로 남았다")
    # ③ 미치환 마크다운
    hit("**" in text, "굵게(**)가 변환되지 않았다")
    hit(re.search(r"\]\(https?://", text), "마크다운 링크가 변환되지 않았다")
    # 열린 대괄호만 남은 링크 토막("[설정 가이드가 있습니다") — 모델이 쓰다 만 것
    hit(re.search(r"\[[^\]\n]{2,40}(?:\n|$)", text), "링크 대괄호가 닫히지 않았다")
    hit(re.search(r"^\s*\|.*\|\s*$", text, re.M), "표가 파이프 글자 그대로 남았다")
    # ④ 참조 규율 — 나열 자리에는 무거운 뱃지를 쓰지 않는다(사용자 지시)
    refs = html.split('class="agent-refs-list"')
    if len(refs) > 1:
        seg = refs[1]
        hit("jira-badge" in seg, "참조에 무거운 티켓 뱃지가 쓰였다(슬림 링크여야 한다)")
        hit("conf-link" in seg or "web-badge" in seg, "참조에 문서 뱃지가 쓰였다")
        hit(re.search(r'class="[^"]*md-person(?![-\w])', seg),
            "참조에 사람 칩이 쓰였다(이름 글자면 된다)")
    # ⑤ 빈 껍데기
    hit(re.search(r"확인된 기록 없음", text), "'확인된 기록 없음' 이 답변에 남았다")
    hit(re.search(r"(?:^|\n)#{2,4}[^\n]*\n\s*(?:\n|$)", text), "내용 없는 섹션 헤딩이 있다")
    # ⑥ 제어문자·깨진 참조 번호
    hit(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text), "제어문자가 출력에 섞였다")
    marks = {int(x) for x in re.findall(r"\[(\d{1,2})\]", text)}
    listed = {int(x) for x in re.findall(r'data-ref="(\d{1,2})"', html)}
    if marks and listed:
        miss = sorted(marks - listed - {0})
        hit(bool(miss), f"본문의 참조 번호 {miss} 가 참조 목록에 없다")
    return bad


# 판독용 텍스트 — 뱃지·칩은 **한 덩어리**로 접어서 뽑는다.
# innerText 를 그대로 쓰면 flex 자식마다 줄이 갈려("버그/DL-5876/제목/Resolved")
# 화면은 멀쩡한데 리포트만 깨져 보인다(자체 실측).
READ_TEXT_JS = """
(root) => {
  const c = root.cloneNode(true);
  c.querySelectorAll('.jira-badge, a.tkt, a.ref-tkt').forEach((a) => {
    const key = a.getAttribute('data-key') || (a.querySelector('.jb-key') || {}).textContent || '';
    const nm = (a.querySelector('.jb-name') || a.querySelector('.ref-ttl') || {}).textContent || '';
    a.replaceWith(document.createTextNode(nm ? `${key}(${nm.trim()})` : key));
  });
  c.querySelectorAll('.md-person, .md-person-plain').forEach((s) => {
    const nm = (s.querySelector('.md-person-nm') || {}).textContent || s.textContent || '';
    const uid = s.getAttribute('data-uid') || s.getAttribute('title') || '';
    s.replaceWith(document.createTextNode(nm.trim() + (uid ? '[' + uid + ']' : '')));
  });
  c.querySelectorAll('.conf-link, .web-badge, .ref-link').forEach((a) => {
    a.replaceWith(document.createTextNode((a.textContent || '').trim()));
  });
  document.body.appendChild(c);
  const t = c.innerText;
  c.remove();
  return t;
}
"""


# 초안 전문 추출기 — 별도 파일이다(파이썬 문자열에 JS 를 섞으면 개행이 깨진다).
DRAFT_JS = (Path(__file__).with_name("_draft_snapshot.js")).read_text(encoding="utf-8")


def run(cases: list[dict], out_path: Path | None) -> int:
    from playwright.sync_api import sync_playwright

    report: list[str] = []
    problems = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page()
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        for case in cases:
            cid = case.get("id") or "?"
            print(f"\n{'=' * 70}\n### {cid}", flush=True)
            report.append(f"\n## {cid}\n")
            pg.goto(BASE + "/#/ai", wait_until="networkidle")
            pg.wait_for_timeout(1200)
            pg.evaluate("() => localStorage.removeItem('agentConvos')")
            pg.reload(wait_until="networkidle")
            pg.wait_for_timeout(1200)
            errs.clear()

            for ti, turn in enumerate(case.get("turns") or []):
                ed = pg.locator(".agent-chatbox .ProseMirror").first
                ed.click()
                ed.type(turn)
                pg.locator(".agent-send-round:not(.is-stop)").first.click()
                # 응답 끝날 때까지 — 중단 버튼이 사라지면 끝이다
                for _ in range(150):
                    pg.wait_for_timeout(2000)
                    if pg.locator(".agent-send-round.is-stop").count() == 0:
                        break
                pg.wait_for_timeout(1200)

                bubble = pg.locator(".agent-bubble.agent .agent-md").last
                text = (pg.evaluate(READ_TEXT_JS, bubble.element_handle())
                        if bubble.count() else "(빈 답변)")
                html = bubble.inner_html() if bubble.count() else ""
                # 참조를 펼쳐 안까지 본다
                if pg.locator("details.agent-refs").count():
                    pg.evaluate("() => document.querySelectorAll('details.agent-refs')"
                                ".forEach(d => d.open = true)")
                    pg.wait_for_timeout(600)
                    html = bubble.inner_html()
                    text = pg.evaluate(READ_TEXT_JS, bubble.element_handle())

                # 승인 카드가 떴으면 **티켓 본문 전문**을 서버 스냅샷에서 읽는다.
                draft_txt = pg.evaluate(DRAFT_JS) or ""
                if draft_txt.strip():
                    print("\n  [초안 전문]")
                    print("\n".join("  " + ln for ln in draft_txt.split("\n")))
                    report.append("\n```\n" + draft_txt + "\n```\n")
                    if not re.search(r"완료\s*조건|DoD", draft_txt, re.I):
                        print("  [본문 품질] 완료 조건(DoD) 없음")
                        report.append("- 본문 품질: 완료 조건(DoD) 없음\n")

                qs = pg.locator(".agent-qform .aq-q")
                cards = {
                    "승인": pg.locator(".agent-card, .ag-approve").count(),
                    "질문": qs.count(),
                    "참조": pg.locator("details.agent-refs").count(),
                    "근거": pg.locator(".agent-ev").count(),
                }
                broken = pg.evaluate(
                    "() => [...document.querySelectorAll('.agent-md img')]"
                    ".filter(i => i.complete && !i.naturalWidth).length")

                print(f"\n--- Q{ti + 1}: {turn[:70]}")
                print(text[:1200])
                report.append(f"**Q{ti + 1}:** {turn}\n\n{text}\n")

                found = render_findings(html, text)
                if broken:
                    found.append(f"깨진 이미지 {broken}개")
                page_errs = [e for e in errs if "favicon" not in e and "avatar" not in e]
                if page_errs:
                    found.append(f"콘솔 오류: {page_errs[:2]}")
                print(f"  [카드] {cards}")
                if found:
                    problems += len(found)
                    print("  [렌더 위반] " + " / ".join(found))
                    report.append("- 렌더 위반: " + "; ".join(found) + "\n")
                else:
                    print("  [렌더] 위반 없음")

                # 되묻기가 떴으면 자동 응답(다음 턴이 있을 때만)
                if qs.count() and case.get("answer", "auto") == "auto" \
                        and ti + 1 < len(case.get("turns") or []):
                    skip = pg.locator("button.ag-cancel")
                    if skip.count():
                        skip.first.click()
                        for _ in range(150):
                            pg.wait_for_timeout(2000)
                            if pg.locator(".agent-send-round.is-stop").count() == 0:
                                break
        br.close()

    if out_path:
        out_path.write_text("\n".join(report), encoding="utf-8")
        print(f"\n리포트: {out_path}")
    print(f"\n렌더 위반 총 {problems}건")
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    cases = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    out = None
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
    return 0 if run(cases, out) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
