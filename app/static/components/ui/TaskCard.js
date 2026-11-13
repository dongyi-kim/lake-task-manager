// Shared two-line ticket card. Deadline urgency sits beside D-day; unseen updates use the corner.
import TypeBadge from "./TypeBadge.js";
import Avatar from "./Avatar.js";
import PriIcon from "./PriIcon.js";
import DueText from "./DueText.js";
import TicketUpdateMark from "./TicketUpdateMark.js";
import { ticketUpdateView } from "../../lib/ticketUpdateView.js";
import { observeTicketUpdates } from "../../lib/ticketUpdates.js";
import { vocBadgeSegs, vocStripTitle } from "../../lib/voc.js";

const RED_DAYS = 3;
// '지금 손대야 하는 것' — **최우선(Blocker/Highest)** 이거나 **마감 3일 이내**.
// 둘 중 하나만으로는 부족하다: 마감이 멀어도 Blocker 는 지금 봐야 하고, 우선순위가 보통이어도
// 내일이 마감이면 지금 봐야 한다. 카드 전체에 옅은 붉은 기를 줘 목록에서 먼저 잡히게 한다.
export function isUrgent(card) {
  const d = card.dueDays;
  return card.priRank === 0 || (d !== null && d !== undefined && d <= RED_DAYS);
}

export default {
  name: "TaskCard",
  components: { TypeBadge, Avatar, PriIcon, DueText, TicketUpdateMark },
  mixins: [ticketUpdateView],
  watch: {
    updateSource: { immediate: true, handler(value) { if (value) observeTicketUpdates([this.card]); } },
  },
  props: {
    card: { type: Object, required: true },
    showOwner: { type: Boolean, default: true },
    showEpic: { type: Boolean, default: true },
    epicTitle: { type: String, default: "" },
    epicPending: { type: Boolean, default: false },
    // '내 담당' 이 아닌 카드를 흐리게(.rel) 물러나게 할지. '내 Task' 화면에서는 그게 핵심이지만,
    // **아직 만들어지지도 않은 티켓**(Bulk 미리보기)에는 담당 관계가 없어 흐릴 이유가 없다.
    dim: { type: Boolean, default: true },
    // 눌러서 티켓 창을 열 수 있는 카드인가(.tkt[data-key] 를 전역 위임 처리가 잡는다).
    // 미리보기 카드는 **아직 Jira 에 없다** — 누르면 없는 키로 창을 열려다 실패한다.
    link: { type: Boolean, default: true },
  },
  computed: {
    updateSource() { return this.link ? this.card.key + ":" + (this.card.updated || "") : ""; },
    done() { return this.card.statusCategory === "done"; },
    // 완료된 일은 아무리 마감이 지났어도 급하지 않다 — 이미 끝났다.
    urgent() { return !this.done && isUrgent(this.card); },
    /** Sub-Task 는 **부모 Task** 를 단다. Epic 은 부모를 통해 이미 정해져 있어, 하위마다 Epic 을
     *  또 붙이면 같은 이름이 카드마다 반복되면서 정작 '어느 Task 밑이냐' 는 안 보인다.
     *  (그룹화 모드에서는 부모가 곧 그룹 머리라 이 뱃지가 필요 없다 — showEpic 이 꺼져 있다.) */
    isSub() { return !!this.card.isSub && !this.card.isGroupSelf; },
    parentKey() { return (this.card.parent && this.card.parent.key) || this.card.parentKey || ""; },
    parentTitle() { return (this.card.parent && this.card.parent.title) || ""; },
    // VoC 티켓은 제목 접두 [대분류 - 소분류] 를 떼고, 그 값을 뱃지로 쪼개 보인다(voc.js).
    dispTitle() { return this.card.voc ? vocStripTitle(this.card.title) : (this.card.title || ""); },
    vocSegs() { return vocBadgeSegs(this.card.title); },
  },
  template: `
  <!-- dim=false 면 담당 관계로 흐려지지 않는다 — 띠는 제 색(.mine)을 쓰되, 담당자 칸의 '나'
       강조(.me)는 그대로 card.mine 을 따른다(소속을 흐리지 않는 것과 '내 것' 이라고 말하는
       것은 다른 얘기다). -->
  <div class="mt-card two" :class="[{ tkt: link },
       { mine: card.mine || !dim, rel: dim && !card.mine, done: done, urgent: urgent,
         'update-unread': link && isUnread(card) }]"
       :data-key="link ? card.key : null" :role="link ? 'button' : null" :tabindex="link ? 0 : null">
    <TicketUpdateMark v-if="link && isUnread(card)" />
    <div class="tc-l1">
      <TypeBadge :type="card.type" />
      <span class="mt-key">{{ card.key }}</span>
      <span class="mt-title" :title="dispTitle">{{ dispTitle }}</span>
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
      <span v-else-if="showEpic && card.epicKey" class="mt-epic sm" :class="{ pending: epicPending }"
            :title="'Epic: ' + epicTitle">{{ epicTitle }}</span>
      <span v-else-if="showEpic && card.voc" class="mt-voc sm" :title="'사용자 VoC' + (vocSegs.length > 1 ? ' — ' + vocSegs.slice(1).join(' · ') : '')">
        <span v-for="(s, i) in vocSegs" :key="i" class="mt-voc-seg" :class="{ head: i === 0 }">{{ s }}</span>
      </span>
      <span v-else-if="showEpic" class="mt-epic sm none">Epic 없음</span>
    </div>
  </div>`,
};
