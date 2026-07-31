// bulkSchema.js — Bulk 티켓 생성 JSON 의 **포맷 정의 · 1차 검증 · LLM 프롬프트**(단일 소스).
//
// 검증은 2단이다:
//   1단(여기)  — JSON 문법·필수키·타입·형식. 네트워크 없이 즉시. 통과해야 [다음] 이 열린다.
//   2단(서버)  — 실값 대조(부모 존재/타입, 만들 수 있는 타입, priority·component·담당자 실존).
// 규칙 문구는 여기 한 곳에만 두고 프롬프트도 이걸 재사용한다 — 두 벌로 나뉘면 반드시 어긋난다.
//
// 서버 검증기는 app/domain/bulk.py. **두 파일의 규칙은 같아야 한다**(여기서 통과하고 서버에서
// 막히는 건 괜찮지만, 그 반대는 사용자를 속이는 것이다).

const KEY_RE = /^[A-Z][A-Z0-9]*-\d+$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_ITEMS = 100;

// 항목이 가질 수 있는 키(그 밖은 경고 후 무시) — app/domain/bulk.py ITEM_FIELDS 와 일치.
const ITEM_FIELDS = ["summary", "type", "epic", "parent", "priority", "duedate",
                     "assignee", "components", "labels", "description"];

/** 필드 설명표 — 화면(입력창 옆 안내)과 LLM 프롬프트가 같은 문구를 쓴다. */
export function fieldDocs(mode) {
  const sub = mode === "subtask";
  return [
    { f: "summary", req: "필수", d: "티켓 제목. 빈 문자열 불가" },
    { f: "type", req: "필수", d: sub ? "보통 \"Sub-Task\"" : "\"Task\" · \"Bug\" · \"Story\" 등" },
    sub ? { f: "parent", req: "필수", d: "상위 Task 키(예 \"DL-9012\"). 이미 존재하는 티켓만. null 불가" }
        : { f: "epic", req: "필수(키)", d: "소속 Epic 키(예 \"DL-5874\"). Epic 없이 만들면 null 을 명시" },
    { f: "priority", req: "선택", d: "없으면 Jira 기본값" },
    { f: "duedate", req: "선택", d: "\"YYYY-MM-DD\"" },
    { f: "assignee", req: "선택", d: "Jira 사용자명 = 이메일 @ 앞부분(예 hong.gildong). 표시이름 아님" },
    { f: "components", req: "선택", d: "모듈. 문자열 배열 예 [\"ETL\"]" },
    { f: "labels", req: "선택", d: "문자열 배열. 공백은 _ 로 바뀜" },
    { f: "description", req: "선택", d: "본문. Markdown (체크박스·표·불릿 지원)" },
  ];
}

/** 붙여넣어 바로 쓸 수 있는 예제 JSON. */
export function exampleJson(mode) {
  if (mode === "subtask") {
    return JSON.stringify({
      mode: "subtask",
      items: [
        { parent: "DL-9012", type: "Sub-Task", summary: "스키마 설계",
          assignee: "test.ui01", duedate: "2026-08-20", priority: "P2-Major",
          description: "## 범위\n- 테이블 3종\n\n### 체크리스트\n- [ ] 초안\n- [x] 리뷰 요청\n\n| 항목 | 값 |\n|------|-----|\n| 대상 | DW |" },
        { parent: "DL-9012", type: "Sub-Task", summary: "적재 파이프라인 구현" },
      ],
    }, null, 2);
  }
  return JSON.stringify({
    mode: "task",
    items: [
      { epic: "DL-5874", type: "Task", summary: "실시간 수집 파이프라인 설계",
        priority: "P2-Major", duedate: "2026-08-20", assignee: "test.ui01",
        components: ["ETL"], labels: ["backend"],
        description: "## 배경\n지연이 커져 재설계가 필요하다.\n\n- [ ] 요건 정리\n- [ ] 설계 리뷰\n\n참고: [설계 문서](https://example.com/doc)" },
      { epic: null, type: "Task", summary: "Epic 없이 만드는 단독 Task" },
    ],
  }, null, 2);
}

function err(index, field, message) { return { index, field, message }; }

/**
 * 1차 검증(스키마). `{ ok, data, errors[], warnings[] }`.
 * errors 는 { index(0-based|null), field, message } — 화면이 목록으로 그린다.
 */
export function validateBulk(text, mode) {
  const errors = [], warnings = [];
  const raw = String(text == null ? "" : text).trim();
  if (!raw) return { ok: false, data: null, errors: [err(null, null, "JSON 을 입력하세요.")], warnings };

  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    // JSON.parse 의 메시지는 위치를 알려 준다 — 그대로 보여 주는 게 가장 도움이 된다.
    return { ok: false, data: null, errors: [err(null, null, "JSON 문법 오류 — " + (e && e.message))], warnings };
  }

  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { ok: false, data: null,
             errors: [err(null, null, "최상위는 { \"mode\": …, \"items\": [ … ] } 객체여야 합니다.")], warnings };
  }
  if (data.mode !== mode) {
    errors.push(err(null, "mode",
      `이 창은 '${mode === "subtask" ? "Sub Task" : "Task"}' 모드인데 JSON 의 mode 는 '${data.mode || "없음"}' 입니다.`));
  }
  if (!Array.isArray(data.items)) {
    errors.push(err(null, "items", "items 는 배열이어야 합니다."));
    return { ok: false, data, errors, warnings };
  }
  if (!data.items.length) errors.push(err(null, "items", "만들 항목이 없습니다."));
  if (data.items.length > MAX_ITEMS) {
    errors.push(err(null, "items", `한 번에 최대 ${MAX_ITEMS}건까지 만들 수 있습니다 (현재 ${data.items.length}건).`));
  }

  const sub = mode === "subtask";
  const isStrList = (v) => Array.isArray(v) && v.every((x) => typeof x === "string");

  data.items.forEach((it, i) => {
    if (!it || typeof it !== "object" || Array.isArray(it)) {
      errors.push(err(i, null, "각 항목은 객체(JSON object)여야 합니다."));
      return;
    }
    Object.keys(it).forEach((k) => {
      if (!ITEM_FIELDS.includes(k)) warnings.push(err(i, k, `알 수 없는 필드 '${k}' — 무시됩니다.`));
    });

    if (typeof it.summary !== "string" || !it.summary.trim()) {
      errors.push(err(i, "summary", "제목(summary)은 비어 있지 않은 문자열이어야 합니다."));
    }
    if (typeof it.type !== "string" || !it.type.trim()) {
      errors.push(err(i, "type", "이슈 타입(type)은 필수입니다."));
    }

    if (sub) {
      if (!("parent" in it)) {
        errors.push(err(i, "parent", "Sub-Task 는 상위 Task 키(parent)가 필수입니다."));
      } else if (typeof it.parent !== "string" || !KEY_RE.test(it.parent.trim())) {
        errors.push(err(i, "parent", "parent 는 'DL-123' 형태의 기존 티켓 키여야 합니다(null 불가)."));
      }
      if ("epic" in it) warnings.push(err(i, "epic", "subtask 모드에서 epic 은 무시됩니다."));
    } else {
      if (!("epic" in it)) {
        errors.push(err(i, "epic", "epic 키가 필요합니다. Epic 없이 만들려면 \"epic\": null 을 명시하세요."));
      } else if (it.epic !== null && (typeof it.epic !== "string" || !KEY_RE.test(it.epic.trim()))) {
        errors.push(err(i, "epic", "epic 은 'DL-123' 형태의 Epic 키 또는 null 이어야 합니다."));
      }
      if ("parent" in it) warnings.push(err(i, "parent", "task 모드에서 parent 는 무시됩니다."));
    }

    if (it.duedate != null && it.duedate !== "") {
      if (typeof it.duedate !== "string" || !DATE_RE.test(it.duedate.trim())) {
        errors.push(err(i, "duedate", "duedate 는 'YYYY-MM-DD' 형식이어야 합니다."));
      }
    }
    ["priority", "assignee", "description"].forEach((f) => {
      if (it[f] != null && typeof it[f] !== "string") errors.push(err(i, f, `${f} 는 문자열이어야 합니다.`));
    });
    ["components", "labels"].forEach((f) => {
      if (it[f] != null && !isStrList(it[f])) {
        errors.push(err(i, f, `${f} 는 문자열 배열이어야 합니다. 예: ["ETL"]`));
      }
    });

    // 본문에서 못 만드는 것 — 첨부/이미지는 Bulk 로 불가, 링크는 웹(http/https)만 살아남는다.
    if (typeof it.description === "string" && it.description) {
      if (/!\[[^\]]*\]\([^)]*\)/.test(it.description)) {
        warnings.push(err(i, "description",
          "이미지·파일 첨부는 Bulk 로 만들 수 없습니다. 웹 링크(http/https)만 살아납니다."));
      }
      const m = /(?<!!)\[[^\]]*\]\(([^)\s]+)\)/.exec(it.description);
      if (m && !/^https?:\/\//i.test(m[1])) {
        warnings.push(err(i, "description", `'${m[1]}' 는 웹 링크가 아니라 글자로 남습니다.`));
      }
    }
  });

  return { ok: !errors.length, data, errors, warnings };
}

/**
 * LLM 프롬프트 — 유저가 복사해 자기 LLM 에 붙인다.
 * opts 에 서버에서 받은 **실제 선택지**(types·priorities·components)를 넣으면 그대로 박아 준다 —
 * 그래야 LLM 이 존재하지 않는 값을 지어내지 않는다.
 */
export function buildLlmPrompt(mode, opts) {
  const o = opts || {};
  const sub = mode === "subtask";
  const list = (a) => (a && a.length ? a.map((x) => `"${x}"`).join(", ") : "(조회 실패 — 아는 값을 쓰지 말고 필드를 생략하세요)");
  const docs = fieldDocs(mode).map((d) => `- ${d.f} (${d.req}): ${d.d}`).join("\n");

  return `너는 Jira 티켓 생성용 JSON 을 만드는 도우미다. 아래 규격을 **정확히** 지켜 JSON 만 출력한다.

## 출력 형식
\`\`\`json
${exampleJson(mode)}
\`\`\`

## 필드
${docs}

## 반드시 지킬 규칙
1. 최상위는 { "mode": "${mode}", "items": [...] } 객체다. mode 는 반드시 "${mode}".
2. ${sub
    ? "각 항목에 parent(상위 Task 키)가 **필수**다. 그 Task 는 **이미 Jira 에 존재**해야 한다 — 이 JSON 안에서 만든 티켓을 부모로 쓸 수 없다. null 불가."
    : "각 항목에 epic 키가 **반드시 존재**해야 한다. 소속 Epic 이 있으면 그 키, 없으면 반드시 `\"epic\": null` 로 **명시**한다(키를 빼면 오류)."}
3. Task 와 Sub-Task 를 한 번에 섞어 만들 수 없다. 이 JSON 은 전부 ${sub ? "Sub-Task" : "Task"} 다.
4. 티켓 키는 "DL-1234" 형태. duedate 는 "YYYY-MM-DD".
5. 한 번에 최대 ${MAX_ITEMS} 건.
6. 위 필드 외의 키는 넣지 않는다. 모르는 값은 **필드를 생략**한다(지어내지 말 것).

## 값은 아래 목록에서만 고른다
- type: ${list(o.types)}
- priority: ${list(o.priorities)}
- components: ${list(o.components)}
- assignee: Jira 사용자명 — **회사 이메일의 @ 앞부분**이다(예: hong.gildong@company.com → "hong.gildong").
  한글 이름("홍길동")이나 사번을 넣으면 실패한다. 모르면 **필드를 생략**한다.

## description 작성법 (Markdown)
- 제목 \`##\`, 불릿 \`- \`, 번호 \`1. \`, 인용 \`> \`, 코드펜스 \`\`\`
- 체크박스: \`- [ ] 할 일\` / \`- [x] 완료\`
- 표:
  \`\`\`
  | 항목 | 값 |
  |------|-----|
  | 대상 | DW |
  \`\`\`
- **이미지·파일 첨부는 만들 수 없다.** 링크는 **웹 링크(http/https)만** 동작한다 —
  \`[문서](https://example.com/doc)\` 형태. 로컬 경로·file:// · 첨부 참조는 글자로만 남는다.

## 출력 전 스스로 검사할 것
- [ ] JSON 문법이 유효한가 (마지막 쉼표, 따옴표)
- [ ] 모든 항목에 summary 와 type 이 있는가
- [ ] ${sub ? "모든 항목에 parent 가 있고 'DL-숫자' 형태인가" : "모든 항목에 epic 키가 있는가(없으면 null 명시)"}
- [ ] type / priority / components 값이 위 목록에 있는가
- [ ] duedate 가 YYYY-MM-DD 인가
- [ ] description 에 이미지·비-웹 링크를 쓰지 않았는가

설명·인사말 없이 **JSON 코드블록 하나만** 출력한다.`;
}
