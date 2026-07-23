// TaskCard.js — '내 Task' 의 **2줄 카드**. 하위(Sub-Task)가 없어 부모 카드에 담기지 않는 티켓용.
//
//   1행  [타입] [번호] [제목]
//   2행  [우선순위] [긴급도] [D-day] | [담당자] [소속 Epic]
//
// 왜 2줄인가: 한 줄에 다 넣으면 제목이 먼저 잘려 정작 무슨 일인지가 안 보인다. 제목에 한 줄을
// 통째로 주고, 판단 재료(급한가·언제까지·누가·어디 소속)는 아랫줄에 모은다.
//
// 완료된 티켓은 **긴급도·D-day 대신 완료일**을 보인다 — 끝난 일에 '며칠 남음' 은 의미가 없다.
import TypeBadge from "./TypeBadge.js";
import Avatar from "./Avatar.js";
import PriIcon from "./PriIcon.js";
import { ymd } from "../../lib/fmt.js";

// 긴급도 — 남은 일수 하나로 정한다. 숫자(D-3)는 정확하지만 훑을 땐 안 읽히고,
// 표정은 정확하지 않지만 **한눈에** 읽힌다. 둘을 같이 둬서 서로를 보완한다.
const URGENCY = [
  { max: 0, icon: "😡", key: "over", label: "마감일이거나 지났습니다" },
  { max: 3, icon: "😰", key: "soon", label: "3일 이내" },
  { max: 7, icon: "😮", key: "week", label: "일주일 이내" },
];
const CALM = { icon: "😴", key: "calm", label: "여유 있음(7일 초과)" };
// 마감이 없으면 '여유' 가 아니라 **모른다** — 급한지 아닌지를 판단할 근거 자체가 없다.
// 잠자는 얼굴로 두면 "안 급함" 이라고 단정해 버리므로 물음표로 구분한다.
const UNKNOWN = { icon: "❓", key: "unknown", label: "마감이 정해져 있지 않습니다" };

export function urgencyOf(dueDays) {
  if (dueDays === null || dueDays === undefined) return UNKNOWN;
  for (const u of URGENCY) if (dueDays <= u.max) return u;
  return CALM;
}

export default {
  name: "TaskCard",
  components: { TypeBadge, Avatar, PriIcon },
  props: {
    card: { type: Object, required: true },
    showOwner: { type: Boolean, default: true },
    showEpic: { type: Boolean, default: true },
    epicTitle: { type: String, default: "" },
  },
  computed: {
    done() { return this.card.statusCategory === "done"; },
    urg() { return urgencyOf(this.card.dueDays); },
    // 고정폭으로 세로로 나열되므로 길이가 들쭉날쭉하면 안 된다 — 가장 긴 게 'D-DAY'(5자).
    dday() {
      const d = this.card.dueDays;
      if (d === null || d === undefined) return "미정";
      return d < 0 ? "D+" + -d : d === 0 ? "D-DAY" : "D-" + d;
    },
    dueCls() {
      const d = this.card.dueDays;
      if (d === null || d === undefined) return "none";
      return d < 0 ? "over" : d === 0 ? "today" : d <= 7 ? "soon" : "later";
    },
    doneAt() { return this.card.resolved ? ymd(this.card.resolved) : ""; },
  },
  template: `
  <div class="mt-card two tkt" :data-key="card.key"
       :class="{ mine: card.mine, rel: !card.mine, done: done }">
    <div class="tc-l1">
      <TypeBadge :type="card.type" />
      <span class="mt-key">{{ card.key }}</span>
      <span class="mt-title">{{ card.title }}</span>
    </div>
    <div class="tc-l2">
      <PriIcon :rank="card.priRank" :name="card.pri" />
      <!-- 완료면 '언제 끝냈나' 만 남긴다. 끝난 일에 긴급도·남은 일수는 의미가 없다. -->
      <template v-if="done">
        <span class="tc-fin" :title="'완료 ' + doneAt">✓ {{ doneAt || '완료' }}</span>
      </template>
      <template v-else>
        <span class="tc-when" :class="[dueCls, { inh: card.dueInherited }]"
              :title="card.dueInherited ? '상위 Task 의 마감(' + (card.due || '') + ')' : (card.due || '마감 없음')">
          <span class="tc-urg" :class="urg.key" :title="urg.label">{{ urg.icon }}</span>
          <b><i v-if="card.dueInherited" class="inh-m">↑</i>{{ dday }}</b>
        </span>
      </template>
      <span v-if="showOwner" class="mt-owner" :class="{ me: card.mine }"
            :title="(card.assignee || '미할당') + ' 담당' + (card.mine ? ' (나)' : '')">
        <Avatar :user="card.assigneeId" :name="card.assignee" :size="15" />{{ card.assignee || '미할당' }}
      </span>
      <span v-if="showEpic && card.epicKey" class="mt-epic sm" :title="'Epic: ' + epicTitle">◆ {{ epicTitle }}</span>
      <span v-else-if="showEpic && card.voc" class="mt-epic sm">◆ 사용자 VoC</span>
      <span v-else-if="showEpic" class="mt-epic sm none">Epic 없음</span>
    </div>
  </div>`,
};
