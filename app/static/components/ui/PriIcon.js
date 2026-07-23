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
const LEVELS = [
  { icon: "▲▲", key: "highest", label: "Highest — 최우선" },
  { icon: "▲", key: "high", label: "High — 높음" },
  { icon: "▬", key: "medium", label: "Medium — 보통" },   // 방향 없음 = 중간
  { icon: "▼", key: "low", label: "Low — 낮음" },
  { icon: "▼▼", key: "lowest", label: "Lowest — 최하" },
];

export function priLevel(rank) {
  return LEVELS[rank === null || rank === undefined ? 2 : Math.max(0, Math.min(4, rank))];
}

export default {
  name: "PriIcon",
  props: { rank: { type: Number, default: 2 }, name: { type: String, default: "" } },
  computed: { lv() { return priLevel(this.rank); } },
  template: `<span class="pri-i" :class="lv.key"
                   :title="'우선순위: ' + (name || lv.label)">{{ lv.icon }}</span>`,
};
