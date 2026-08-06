"""app/agent — Lake PMO Agent (업무 착수 어시스턴트).

이 패키지는 **선택 설치**다(`requirements-agent.txt`). 설치돼 있지 않으면 라우트도 설정 패널도
켜지지 않는다 — 대시보드만 쓰는 사용자에게 200MB+ 의존을 강요하지 않기 위해서다.
게이팅은 `app.agent.config.available()` 한 곳을 거친다(devtools 와 같은 방식).
"""
