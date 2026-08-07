# Lake PMO Agent — 채점/데모용 컨테이너.
# run.py(데스크톱 앱 창 런처) 대신 uvicorn 을 직접 띄운다 — 컨테이너엔 창이 없다.
FROM python:3.11-slim

WORKDIR /app

# 의존성 레이어 분리(코드 수정 시 재설치 방지)
COPY requirements.txt requirements-agent.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-agent.txt

COPY . .

# mock: 내장 가상 Jira/Confluence(jira820, 12개월 히스토리) — 외부 Jira 불필요
ENV JIRA_ENV=mock \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
