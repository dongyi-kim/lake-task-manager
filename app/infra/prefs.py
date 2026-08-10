"""app_prefs.py — 런타임에 사람이 켜고 끄는 설정(파일에 저장, 프로세스 재시작에도 유지).

config/jira.yml 은 **배포 시 손으로 채우는 템플릿**이라, 앱을 쓰다가 화면에서 바꾸는 값은
거기 두면 안 된다(커밋 대상이고, 실행 중 다시 읽지도 않는다). 그런 값은 이 작은 JSON 에 둔다.

지금 담는 것:
  bitbucketEnabled  Bitbucket 연동 사용 여부(기본 False). True 일 때만 SSO 인증 순회·통합
                    검색에 Bitbucket 이 낀다. base 가 config 에 있어도 이게 False 면 안 쓴다.

**커밋 금지** — 사용자별 상태다(.gitignore 에 넣는다). 비밀은 없지만 개인 설정이다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.Lock()
# ★ 이 표에 없는 키는 **저장도 조회도 조용히 버려진다**(load/save 가 _DEFAULTS 로 거른다).
#   새 설정을 추가할 때 여기 먼저 넣어야 한다 — 안 넣으면 화면은 저장됐다고 하고 값은 사라진다.
_DEFAULTS = {"bitbucketEnabled": False,
             # 빠른 열기 전역 단축키(데스크톱 앱). 설정/트레이에서 바꾼다. run.py 가 이 값으로 등록.
             "quickOpenHotkey": "ctrl+alt+space",

             # ── AI 에이전트 (app/agent) ──
             # 비밀이 아닌 것만 여기 둔다. API 키는 agent_secrets.json 이 따로 갖는다.
             # 빈 문자열 = "정하지 않음" → config 가 환경변수·기본값으로 넘어간다.
             "agentProvider": "",        # aoai | openai | openai_compat | fake
             "agentApiVersion": "",      # AOAI 전용
             "agentAoaiChat": "", "agentAoaiEmbed": "",        # ★ 모델명이 아니라 배포명
             "agentOpenaiChat": "", "agentOpenaiEmbed": "",
             "agentCompatChat": "", "agentCompatEmbed": "",
             # 간단한 역할(의도 분류·결정적 실행) 전용 모델 — 비우면 기본 모델 하나로 돈다.
             "agentAoaiChatSimple": "", "agentOpenaiChatSimple": "", "agentCompatChatSimple": "",
             # 이중 확인 게이트(사용자 지시) — **확인에 통과한 설정 조합의 지문.**
             # 지금 설정의 지문과 같을 때만 AI 기능이 켜진다. 모델이나 키를 바꾸면 지문이
             # 달라지고, 그 조합은 확인된 적이 없으므로 다시 확인해야 한다.
             # (여기 등록하지 않으면 prefs.save 가 **조용히 버린다** — 화이트리스트 방식이다.)
             "agentVerifiedSig": "",
             # 사용자별 시스템 프롬프트 추가분 — 모든 역할의 페르소나 뒤에 붙는다.
             # 프로젝트 공용 추가분은 여기가 아니라 config/agent-prompt.md (repo 커밋 대상).
             "agentUserPrompt": ""}


def _path():
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "app_prefs.json"


def load() -> dict:
    """저장된 설정 + 기본값. 파일이 없거나 깨졌으면 기본값(앱이 죽지 않게)."""
    out = dict(_DEFAULTS)
    try:
        with _path().open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in _DEFAULTS:
                if k in data:
                    out[k] = data[k]
    except Exception:
        pass
    return out


def save(patch: dict) -> dict:
    """일부 키만 바꿔 저장하고 병합 결과를 돌려준다. 원자적 교체(중간에 깨진 파일이 안 남게)."""
    with _LOCK:
        cur = load()
        for k, v in (patch or {}).items():
            if k in _DEFAULTS:
                cur[k] = v
        p = _path()
        tmp = p.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            tmp.replace(p)
        except Exception:
            pass
        return cur
