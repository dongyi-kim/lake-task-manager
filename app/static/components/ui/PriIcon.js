// PriIcon.js — 우선순위 아이콘.
//
// 세 가지가 겹쳐 읽힌다: **방향**(위=급함/아래=나중) · **개수**(겹치면 극단) · **색**(적→청).
// 우선순위는 5단계라 이름(Highest/High/…)을 다 적으면 카드 한 줄을 먹는데, 방향과 색은
// 글자를 안 읽어도 잡히고 5단계가 한 축 위에 놓여 서로 비교된다.
//
//    ▲▲ Highest   ▲ High   ▬ Medium   ▼ Low   ▼▼ Lowest
//
// 이모지(⏫🔺)를 쓰지 않은 이유: 폰트가 색을 고정해 버려 **색으로 단계를 표현할 수 없고**,
// 같은 카드의 긴급도 표정(😡😰)과 섞이면 둘 중 무엇이 무엇인지 구분되지 않는다.
// 여기 쓴 ▲▼▬ 는 기본 도형이라 어느 폰트에서도 그대로 나온다.
//
// Jira DC 의 우선순위 아이콘과 같은 은유(위/아래 화살표 + 적/청)라 따로 배울 게 없다.
// 사내 체계에는 **Unclassified(미분류)** 가 실제 값으로 쓰인다. 이걸 Medium 으로 떨어뜨리면
// "아직 분류 안 된 것" 과 "보통이라고 판단한 것" 이 화면에서 같아 보인다 — 둘은 전혀 다른 상태다
// (전자는 누군가 등급을 매겨야 하고, 후자는 이미 매겨졌다). 그래서 자기 표식을 준다.
const UNSET_NAMES = new Set(["unclassified", "none", "not set", "undefined", "-", ""]);
const UNSET = { icon: "·", key: "unset", label: "Unclassified — 미분류(등급 미지정)" };

const LEVELS = [
  { icon: "▲▲", key: "highest", label: "Highest — 최우선" },
  { icon: "▲", key: "high", label: "High — 높음" },
  { icon: "▬", key: "medium", label: "Medium — 보통" },   // 방향 없음 = 중간
  { icon: "▼", key: "low", label: "Low — 낮음" },
  { icon: "▼▼", key: "lowest", label: "Lowest — 최하" },
];

// 이름 → 등급. 서버(mytasks._PRI_RANK)와 **같은 표**다 — 선택지 목록처럼 서버 등급이 딸려
// 오지 않는 자리에서 쓴다. 둘이 어긋나면 고르기 전과 후의 아이콘이 달라진다.
const RANK_OF = {
  highest: 0, high: 1, medium: 2, normal: 2, low: 3, lowest: 4,
  blocker: 0, critical: 1, major: 2, minor: 3, trivial: 4,
  urgent: 0, p1: 0, p2: 1, p3: 2, p4: 3, p5: 4,
};
// ★ 사내 체계는 'P0-Blocker … P4-Trivial' 이라 **접두사 숫자가 곧 등급**이다. 이름 표를 먼저
//   보면 사내 이름이 바뀌는 순간 전부 '보통' 으로 떨어진다 — 숫자를 먼저 읽는다.
const P_PREFIX = /^\s*P\s*(\d+)/i;
export function priRankOf(name) {
  const n = (name || "").trim();
  const m = P_PREFIX.exec(n);
  if (m) return Math.min(parseInt(m[1], 10), 4);
  const k = n.toLowerCase();
  return k in RANK_OF ? RANK_OF[k] : 2;
}

export function priLevel(rank) {
  return LEVELS[rank === null || rank === undefined ? 2 : Math.max(0, Math.min(4, rank))];
}

export default {
  name: "PriIcon",
  props: { rank: { type: Number, default: 2 }, name: { type: String, default: "" } },
  computed: {
    // 이름이 미분류면 등급(숫자)보다 **이름이 우선**이다. 미분류는 등급이 없는 상태이지
    // '2등급' 이 아니다 — 정렬용 숫자를 그림에 그대로 옮기면 거짓말이 된다.
    lv() {
      const n = (this.name || "").trim().toLowerCase();
      return UNSET_NAMES.has(n) ? UNSET : priLevel(this.rank);
    },
  },
  template: `<span class="pri-i" :class="lv.key"
                   :title="'우선순위: ' + (name || lv.label)">{{ lv.icon }}</span>`,
};
