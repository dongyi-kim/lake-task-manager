// TaskCard.js — '내 Task' 의 **2줄 카드**. 하위(Sub-Task)가 없어 부모 카드에 담기지 않는 티켓용.
//
//   1행  [타입] [번호] [제목]
//   2행  [우선순위] | [D-day] | [담당자] [소속 Epic]   + 급하면 우측 상단에 🔥
//
// 왜 2줄인가: 한 줄에 다 넣으면 제목이 먼저 잘려 정작 무슨 일인지가 안 보인다. 제목에 한 줄을
// 통째로 주고, 판단 재료(급한가·언제까지·누가·어디 소속)는 아랫줄에 모은다.
//
// 완료된 티켓은 **D-day 대신 완료일**을 보인다 — 끝난 일에 '며칠 남음' 은 의미가 없다.
//
// 급한 티켓(7일 이내)은 카드 **우측 상단에 🔥 를 띄운다**. 2행 안에 두면 다른 정보와 같은
// 무게로 줄에 섞여 훑을 때 안 걸린다. 카드 밖으로 반쯤 나온 표식은 목록을 스크롤하는 중에도
// 눈에 먼저 들어온다 — 그게 이 표시의 목적이다.
import TypeBadge from "./TypeBadge.js";
import Avatar from "./Avatar.js";
import PriIcon from "./PriIcon.js";
import DueText from "./DueText.js";

// 긴급도 — 남은 일수 하나로 정한다. 숫자(D-3)는 정확하지만 훑을 땐 안 읽히고,
// 표정은 정확하지 않지만 **한눈에** 읽힌다. 둘을 같이 둬서 서로를 보완한다.
// 급함의 단계 — 표식은 🔥 하나지만 말풍선은 왜 급한지까지 알려 준다.
const URGENCY = [
  { max: 0, key: "over", label: "마감일이거나 지났습니다" },
  { max: 3, key: "soon", label: "마감까지 3일 이내" },
  { max: 7, key: "week", label: "마감까지 일주일 이내" },
];
const CALM = { key: "calm", label: "여유 있음(7일 초과)" };
const UNKNOWN = { key: "unknown", label: "마감이 정해져 있지 않습니다" };
const HOT_DAYS = 7;    // 이 안으로 들어오면 카드에 🔥
const RED_DAYS = 3;    // 이 안으로 들어오면 카드 자체가 붉은 기를 띈다

export function urgencyOf(dueDays) {
  if (dueDays === null || dueDays === undefined) return UNKNOWN;
  for (const u of URGENCY) if (dueDays <= u.max) return u;
  return CALM;
}
export function isHot(dueDays) {
  return dueDays !== null && dueDays !== undefined && dueDays <= HOT_DAYS;
}
// '지금 손대야 하는 것' — **최우선(Blocker/Highest)** 이거나 **마감 3일 이내**.
// 둘 중 하나만으로는 부족하다: 마감이 멀어도 Blocker 는 지금 봐야 하고, 우선순위가 보통이어도
// 내일이 마감이면 지금 봐야 한다. 카드 전체에 옅은 붉은 기를 줘 목록에서 먼저 잡히게 한다.
export function isUrgent(card) {
  const d = card.dueDays;
  return card.priRank === 0 || (d !== null && d !== undefined && d <= RED_DAYS);
}

export default {
  name: "TaskCard",
  components: { TypeBadge, Avatar, PriIcon, DueText },
  props: {
    card: { type: Object, required: true },
    showOwner: { type: Boolean, default: true },
    showEpic: { type: Boolean, default: true },
    epicTitle: { type: String, default: "" },
  },
  computed: {
    done() { return this.card.statusCategory === "done"; },
    urg() { return urgencyOf(this.card.dueDays); },
    // 완료된 일은 아무리 마감이 지났어도 급하지 않다 — 이미 끝났다.
    hot() { return !this.done && isHot(this.card.dueDays); },
    urgent() { return !this.done && isUrgent(this.card); },
    // 고정폭으로 세로로 나열되므로 길이가 들쭉날쭉하면 안 된다 — 가장 긴 게 'D-DAY'(5자).
    doneAt() { return this.card.resolved ? ymd(this.card.resolved) : ""; },
    /** Sub-Task 는 **부모 Task** 를 단다. Epic 은 부모를 통해 이미 정해져 있어, 하위마다 Epic 을
     *  또 붙이면 같은 이름이 카드마다 반복되면서 정작 '어느 Task 밑이냐' 는 안 보인다.
     *  (그룹화 모드에서는 부모가 곧 그룹 머리라 이 뱃지가 필요 없다 — showEpic 이 꺼져 있다.) */
    isSub() { return !!this.card.isSub && !this.card.isGroupSelf; },
    parentKey() { return (this.card.parent && this.card.parent.key) || this.card.parentKey || ""; },
    parentTitle() { return (this.card.parent && this.card.parent.title) || ""; },
  },
  template: `
  <div class="mt-card two tkt" :data-key="card.key"
       :class="{ mine: card.mine, rel: !card.mine, done: done, hot: hot, urgent: urgent }">
    <span v-if="hot" class="tc-hot" :title="urg.label">🔥</span>
    <div class="tc-l1">
      <TypeBadge :type="card.type" />
      <span class="mt-key">{{ card.key }}</span>
      <span class="mt-title">{{ card.title }}</span>
    </div>
    <div class="tc-l2">
      <PriIcon :rank="card.priRank" :name="card.pri" />
      <!-- 완료면 '언제 끝냈나' 만 남긴다. 끝난 일에 긴급도·남은 일수는 의미가 없다. -->
      <DueText :card="card" />
      <span v-if="showOwner" class="mt-owner" :class="{ me: card.mine }"
            :title="(card.assignee || '미할당') + ' 담당' + (card.mine ? ' (나)' : '')">
        <Avatar :user="card.assigneeId" :name="card.assignee" :size="15" />{{ card.assignee || '미할당' }}
      </span>
      <!-- 상위 Task — Epic 뱃지와 **다른 모양**이다(채운 면 vs 테두리). 같은 자리에 같은 모양이면
           어느 것이 Epic 이고 어느 것이 상위 Task 인지 색만으로는 갈리지 않는다. -->
      <span v-if="showEpic && isSub && parentKey" class="mt-parent sm"
            :title="'상위: ' + parentKey + ' ' + parentTitle">
        <b class="mp-k">{{ parentKey }}</b><span class="mp-t">{{ parentTitle }}</span>
      </span>
      <span v-else-if="showEpic && card.epicKey" class="mt-epic sm" :title="'Epic: ' + epicTitle">{{ epicTitle }}</span>
      <span v-else-if="showEpic && card.voc" class="mt-epic sm">사용자 VoC</span>
      <span v-else-if="showEpic" class="mt-epic sm none">Epic 없음</span>
    </div>
  </div>`,
};
