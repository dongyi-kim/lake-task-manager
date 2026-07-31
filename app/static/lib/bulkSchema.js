// bulkSchema.js — Bulk 티켓 생성 JSON 의 **포맷 정의 · 1차 검증 · LLM 프롬프트**(단일 소스).
//
// 검증은 2단이다:
//   1단(여기)  — JSON 문법·필수키·타입·형식. 네트워크 없이 즉시. 통과해야 [다음] 이 열린다.
//   2단(서버)  — 실값 대조(부모 존재/타입, 만들 수 있는 타입, priority·component·담당자 실존).
// 규칙 문구는 여기 한 곳에만 두고 프롬프트도 이걸 재사용한다 — 두 벌로 나뉘면 반드시 어긋난다.
//
// 서버 검증기는 app/domain/bulk.py. **두 파일의 규칙은 같아야 한다**(여기서 통과하고 서버에서
// 막히는 건 괜찮지만, 그 반대는 사용자를 속이는 것이다).

import { stripJsonComments } from "./jsonlines.js";

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
    // 값 예시를 여기 적지 않는다 — 화면은 이 줄 안에 **실제 목록**을 붙이고, 프롬프트도 아래에
    // 실제 목록을 따로 싣는다. 두 곳에 값이 있으면 하나는 반드시 낡는다.
    { f: "type", req: "필수", d: "만들 이슈 타입" },
    sub ? { f: "parent", req: "필수", d: "상위 Task 키(예 \"DL-9012\"). 이미 존재하는 티켓만. null 불가" }
        : { f: "epic", req: "필수(키)", d: "소속 Epic 키(예 \"DL-5874\"). Epic 없이 만들면 null 을 명시" },
    { f: "priority", req: "선택", d: "없으면 Jira 기본값" },
    { f: "duedate", req: "선택", d: "\"YYYY-MM-DD\"" },
    { f: "assignee", req: "선택", d: "Jira 사용자명 = 이메일 @ 앞부분(예 hong.gildong). 표시이름 아님" },
    { f: "components", req: "선택", d: "모듈 이름의 배열" },
    { f: "labels", req: "선택", d: "문자열 배열. 공백은 _ 로 바뀜" },
    { f: "description", req: "선택", d: "본문. Markdown (체크박스·표·불릿 지원)" },
  ];
}

/**
 * 예제 JSON 의 **필드 정의** — 값·주석·순서를 여기 한 곳에 둔다.
 *
 * 예제는 두 가지 일을 한다: (1) 붙여넣어 바로 쓸 수 있는 뼈대 (2) **어떤 필드가 있고 무엇을
 * 적는 자리인지 알려 주는 안내.** 그래서 '티켓 만들기' 창에서 값을 물려받을 때도 **필드를
 * 지우지 않는다** — 안 고른 필드를 지워 버리면 labels·description 을 어떻게 쓰는지 알 길이
 * 사라진다. 고른 값이 있으면 그 자리의 **값만** 갈아 끼운다.
 *
 *   lead : 그 줄 **위**에 붙는 주석(길어서 옆에 못 붙이는 설명)
 *   tail : 그 줄 **옆**에 붙는 주석(짧은 형식 안내)
 */
function exampleFields(mode) {
  const sub = mode === "subtask";
  return [
    sub
      ? { k: "parent", v: "DL-9012",
          lead: "상위 Task 키 — 이미 있는 티켓이어야 합니다(이 JSON 안에서 방금 만든 건 못 씁니다)." }
      : { k: "epic", v: "DL-5874",
          lead: "소속 Epic 키. 키 자체는 반드시 있어야 합니다 — 없이 만들 땐 null 을 적습니다\n"
              + "(빠뜨린 것과 '일부러 없음' 을 구분하지 못하면 미아 티켓이 조용히 쌓입니다)." },
    { k: "type", v: sub ? "Sub-Task" : "Task", tail: "대소문자는 가리지 않습니다" },
    { k: "summary", v: sub ? "스키마 설계" : "실시간 수집 파이프라인 설계", tail: "필수. 제목",
      v2: sub ? "적재 파이프라인 구현" : "Epic 없이 만드는 단독 Task" },
    { k: "priority", v: "P2-Major", tail: "오른쪽 안내의 목록에서 고르세요" },
    { k: "duedate", v: "2026-08-20", tail: "YYYY-MM-DD" },
    { k: "assignee", v: "test.ui01", tail: "회사 이메일의 @ 앞부분. 모르면 이 줄을 지우세요" },
    { k: "components", v: ["ETL"], tail: "모듈. 목록에 없는 값도 쓸 수 있습니다(경고만)" },
    { k: "labels", v: ["backend"], tail: "공백은 _ 로 바뀝니다" },
    { k: "description",
      v: sub
        ? "## 범위\n- 테이블 3종\n\n### 체크리스트\n- [ ] 초안\n- [x] 리뷰 요청\n\n| 항목 | 값 |\n|------|-----|\n| 대상 | DW |"
        : "## 배경\n지연이 커져 재설계가 필요하다.\n\n- [ ] 요건 정리\n- [ ] 설계 리뷰\n\n참고: [설계 문서](https://example.com/doc)",
      lead: "본문은 Markdown — 체크박스 · 표 · 불릿을 씁니다. 첨부는 만들 수 없고,\n"
          + "링크는 웹(http/https)만 살아납니다." },
  ];
}

const REQUIRED = { task: ["epic", "type", "summary"], subtask: ["parent", "type", "summary"] };

/**
 * 한 항목을 그린다.
 *   only  : 이 필드들만(두 번째 항목은 '필수만으로도 된다' 를 보인다)
 *   brief : 줄 위에 붙는 긴 설명은 생략 — 같은 설명을 두 번 읽게 하지 않는다
 *   alt   : 대체 예제값(v2)이 있으면 그걸 쓴다 — 두 항목의 제목이 같으면 '여럿을 만드는 것'
 *           이라는 게 눈에 안 들어온다
 */
function renderItem(mode, opts) {
  const o = opts || {};
  const q = (v) => JSON.stringify(v);
  const fields = exampleFields(mode).filter((f) => !o.only || o.only.indexOf(f.k) >= 0);
  const IND = "      ";

  const valueOf = (f) =>
    (o.alt && o.alt.indexOf(f.k) >= 0 && f.v2 !== undefined) ? f.v2 : f.v;

  const rows = fields.map((f) => ({ f, text: IND + q(f.k) + ": " + q(valueOf(f)) }));
  // 옆 주석은 열을 맞춘다 — 들쭉날쭉하면 오히려 읽기 나쁘다. 아주 긴 줄(본문)은 기준에서 뺀다.
  const pad = Math.max(0, ...rows.filter((r) => r.f.tail && r.text.length <= 60).map((r) => r.text.length));

  return "    {\n" + rows.map((r, i) => {
    const comma = i < rows.length - 1 ? "," : "";
    const lead = (r.f.lead && !o.brief)
      ? r.f.lead.split("\n").map((l) => IND + "// " + l).join("\n") + "\n" : "";
    const tail = r.f.tail ? " ".repeat(Math.max(1, pad - r.text.length + 1)) + "// " + r.f.tail : "";
    return lead + r.text + comma + tail;
  }).join("\n") + "\n    }";
}

/**
 * 붙여넣어 바로 쓸 수 있는 예제 — **주석으로 무엇을 적는 자리인지 설명한다.**
 * 표준 JSON 은 주석을 모르지만 이 창은 받아 준다(validateBulk 가 파싱 전에 걷어낸다).
 * 그러니 주석은 지워도 되고 남겨도 된다 — 남겨 두는 편이 다음에 열었을 때 도움이 된다.
 */
export function exampleJson(mode) {
  const sub = mode === "subtask";
  const kind = sub ? "Sub-Task" : "Task";
  const other = sub ? "Task" : "Sub-Task";

  return "{\n"
    + `  // 이 창은 ${kind} 전용입니다. ${other} 와 섞어 만들 수 없습니다.\n`
    + `  "mode": ${JSON.stringify(mode)},\n`
    + '  "items": [\n'
    + renderItem(mode) + ",\n"
    + "    // 필수는 " + REQUIRED[mode].join(" · ") + " 셋뿐입니다. 나머지는 없으면 Jira 기본값.\n"
    // 둘째 항목: 긴 설명은 빼고(같은 글을 두 번 읽게 하지 않는다), 제목도 다르게 —
    // 두 항목의 제목이 같으면 '여러 개를 만드는 것' 이라는 게 눈에 안 들어온다.
    + renderItem(mode, { only: REQUIRED[mode], brief: true, alt: ["summary"] }) + "\n"
    + "  ]\n}";
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
    // 주석(//, /* */)을 허용한다 — 예제에 설명을 달아 두는 게 이 창의 가장 큰 도움이다.
    // 지우는 게 아니라 공백으로 덮으므로 오류가 가리키는 줄번호는 원문과 그대로 맞는다.
    data = JSON.parse(stripJsonComments(raw));
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
