// One shared status layout for Task, Epic, and WBS sections; evaluated in MyTasksView scope.
const TASK_SECTION_TEMPLATE = `
    <!-- Shared board header/background live outside these flat Epic/WBS sections. -->
    <template v-if="axis === 'h'">
      <div class="mt-hwrap">

      <div v-if="!section.panels.length" class="mt-axis-shell">
        <div v-for="st in states" :key="'shell-' + st.k" :class="'c-' + st.k">
          <span><i v-if="axisLoading(st.k)" aria-hidden="true"></i>{{ axisEmptyText(st.k) }}</span>
        </div>
      </div>

      <template v-for="p in section.panels" :key="p.key">
        <!-- 그룹화 없음 / 하위 없는 Task 묶음 — 묶을 게 없으니 카드 테두리도 없다 -->
        <div v-if="p.kind === 'none' || p.kind === 'solo'" class="mt-gbody plain">
          <div v-for="st in states" :key="'n-' + st.k" class="mt-cell"
               :class="['c-' + st.k, { empty: !pagedCards(p, st.k).length,
                                                closed: !bandOpen(st.k) }]">
            <template v-for="c in pagedCards(p, st.k)" :key="c.key">
              <TaskCard v-if="!c.compactPanel" :card="c"
                     :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                     :epic-pending="epicPending(c.epicKey)" />
              <!-- 1축 Task 는 단독 Task 와 같은 셀·배열·TaskCard 를 그대로 쓴다.
                   공용 하단바와 폴더블 Sub-Task 목록만 부모 카드 바로 아래에 잇는다. -->
              <div v-else class="mt-compact-flow" :style="sigStyle(c)"
                   :class="{ folded: isGroupClosed(c.compactPanel), open: !isGroupClosed(c.compactPanel) }">
                <div class="mt-compact-head">
                  <TaskCard :card="c" :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                            :epic-pending="epicPending(c.epicKey)" />
                </div>
                <SubtaskFoldBar :panel="c.compactPanel" :closed="isGroupClosed(c.compactPanel)"
                                @toggle="toggleGroup(c.compactPanel)" />
                <div v-if="!c.compactPanel.emptyFiltered && !isGroupClosed(c.compactPanel)"
                     class="mt-gbody one mt-compact-children"
                     :class="{ foldwrap: foldable(c.compactPanel), folded: peeking(c.compactPanel),
                               'fold-peek': peeking(c.compactPanel) }">
                  <div v-for="sub in cellCards(c.compactPanel, c.compactPanel.singleStatus)" :key="sub.key" class="mt-card tkt"
                       :class="{ mine: sub.mine, rel: !sub.mine, done: sub.statusCategory === 'done',
                               urgent: isUrgentC(sub) }" :style="sigStyle(sub)" :data-key="sub.key">
                    <span v-if="isHotC(sub)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                    <PriIcon :rank="sub.priRank" :name="sub.pri" />
                    <span class="mt-key">{{ sub.key }}</span>
                    <span class="mt-title">{{ sub.title }}</span>
                    <span class="mt-subdue-sep" aria-hidden="true"></span>
                    <span class="mt-owner mt-sub-owner" :class="{ me: sub.mine }"
                          :title="(sub.assignee || '미할당') + ' 담당' + (sub.mine ? ' (나)' : '')">
                      <Avatar :user="sub.assigneeId" :name="sub.assignee" :size="15" />
                      <span class="mt-owner-name">{{ sub.assignee || '미할당' }}</span>
                    </span>
                    <DueText :card="sub" />
                  </div>
                  <button v-if="overflowed(c.compactPanel, c.compactPanel.singleStatus)" class="fold-b"
                          @click.stop="toggleSub(c.compactPanel.key)">{{
                          subOpen[c.compactPanel.key] ? '접기' : '+' + cellHidden(c.compactPanel, c.compactPanel.singleStatus) + '개 더' }}</button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- Task 그룹 = 카드 하나 -->
        <div v-else-if="p.kind === 'task' && panelPageVisibleAny(p)" class="mt-gslot">
        <div class="mt-gcard2 k-task" :class="{ folded: isGroupClosed(p) }" :style="sigStyle(p.group)">
          <div class="mt-gh">
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.group.voc ? vocStrip(p.title) : p.title }}</span>
              <span v-if="p.epicKey" class="mt-epic" :class="{ pending: epicPending(p.epicKey) }"
                    :title="'Epic: ' + epicDisplayTitle(p.epicKey)">{{ epicDisplayTitle(p.epicKey) }}</span>
              <span v-else-if="p.group.voc" class="mt-voc" :title="'사용자 VoC' + (vocSegs(p.title).length > 1 ? ' — ' + vocSegs(p.title).slice(1).join(' · ') : '')">
                <span v-for="(s, i) in vocSegs(p.title)" :key="i" class="mt-voc-seg" :class="{ head: i === 0 }">{{ s }}</span>
              </span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
          </div>
          <SubtaskFoldBar :panel="p" :closed="isGroupClosed(p)" @toggle="toggleGroup(p)" />
          <div v-if="!isGroupClosed(p)" class="mt-gbody">
            <div v-for="st in states" :key="p.key + st.k" class="mt-cell"
                 :class="['c-' + st.k, { empty: !panelPageVisible(p, st.k) || !byState(p.cards)[st.k].length,
                                                closed: !bandOpen(st.k),
                                                foldwrap: foldable(p),
                                                folded: peeking(p), 'fold-peek': peeking(p) }]">
                <div v-for="c in (panelPageVisible(p, st.k) ? cellCards(p, st.k) : [])" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span class="mt-subdue-sep" aria-hidden="true"></span>
                  <span class="mt-owner mt-sub-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                    <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />
                    <span class="mt-owner-name">{{ c.assignee || '미할당' }}</span>
                  </span>
                  <DueText :card="c" />
                </div>
                <!-- 넘친 칸에만 더보기가 붙지만, 누르면 **이 Task 의 모든 칸**이 함께 열린다.
                     접혀 있을 때는 흐려진 카드 **위에** 앉는다(.foldwrap.folded > .fold-b). -->
                <button v-if="panelPageVisible(p, st.k) && overflowed(p, st.k)" class="fold-b" @click.stop="toggleSub(p.key)">{{
                        subOpen[p.key] ? '접기' : '+' + cellHidden(p, st.k) + '개 더' }}</button>
            </div>
          </div>
        </div>
        </div>

      </template>
      </div>
    </template>

    <!-- ══ 상태 = 세로축 : 상태 패널이 가로로 꽉 차서 쌓이고, 그 안에서 그룹이 좌우로 ══ -->
    <template v-else>
      <div v-for="st in states" :key="st.k" class="mt-band" :class="['c-' + st.k, { closed: !bandOpen(st.k) }]">
        <div class="mt-bandh-w">
          <div class="mt-bandh">
            {{ st.label }}
            <b>{{ sectionCount(section, st.k) }}</b>
            <small v-if="!bandOpen(st.k)">접힘</small>
          </div>
        </div>
        <template v-if="bandOpen(st.k)">
        <!-- 그룹화 없음 → 카드 그리드 하나 -->
        <div v-if="groupBy === 'none'" class="mt-grid2">
          <TaskCard v-for="c in pagedCards(section.panels[0], st.k)" :key="c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                   :epic-pending="epicPending(c.epicKey)" />
          <div v-if="!pagedCards(section.panels[0], st.k).length" class="mt-none">{{ axisEmptyText(st.k) }}</div>
        </div>
        <!-- 그룹화 있음 → 그룹이 좌우로 늘어서고 각 그룹 안이 그리드 -->
        <div v-else class="mt-grouprow">
          <div v-if="!sectionCount(section, st.k)" class="mt-none">{{ axisEmptyText(st.k) }}</div>
          <template v-for="p in section.panels" :key="p.key">
            <!-- 하위 없는 Task 묶음 — 카드로만 -->
            <template v-if="p.kind === 'solo'">
              <TaskCard v-for="c in pagedCards(p, st.k)" :key="'so-' + c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                   :epic-pending="epicPending(c.epicKey)" />
            </template>
            <div v-else v-show="panelPageVisible(p, st.k)" class="mt-gcard2 k-task"
                 :class="{ folded: isGroupClosed(p) }" :style="sigStyle(p.group)">
              <div class="mt-gh">
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.group.voc ? vocStrip(p.title) : p.title }}</span>
              <span v-if="p.epicKey" class="mt-epic" :class="{ pending: epicPending(p.epicKey) }"
                    :title="'Epic: ' + epicDisplayTitle(p.epicKey)">{{ epicDisplayTitle(p.epicKey) }}</span>
              <span v-else-if="p.group.voc" class="mt-voc" :title="'사용자 VoC' + (vocSegs(p.title).length > 1 ? ' — ' + vocSegs(p.title).slice(1).join(' · ') : '')">
                <span v-for="(s, i) in vocSegs(p.title)" :key="i" class="mt-voc-seg" :class="{ head: i === 0 }">{{ s }}</span>
              </span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
              </div>
              <SubtaskFoldBar :panel="p" :closed="isGroupClosed(p)" @toggle="toggleGroup(p)" />
              <div v-if="!p.emptyFiltered && !isGroupClosed(p)" class="mt-gbody one"
                   :class="{ foldwrap: foldable(p), folded: peeking(p), 'fold-peek': peeking(p) }">
                <div v-for="c in cellCards(p, st.k)" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span class="mt-subdue-sep" aria-hidden="true"></span>
                  <span class="mt-owner mt-sub-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                    <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />
                    <span class="mt-owner-name">{{ c.assignee || '미할당' }}</span>
                  </span>
                  <DueText :card="c" />
                </div>
                <!-- 넘친 칸에만 더보기가 붙지만, 누르면 **이 Task 의 모든 칸**이 함께 열린다.
                     접혀 있을 때는 흐려진 카드 **위에** 앉는다(.foldwrap.folded > .fold-b). -->
                <button v-if="overflowed(p, st.k)" class="fold-b" @click.stop="toggleSub(p.key)">{{
                        subOpen[p.key] ? '접기' : '+' + cellHidden(p, st.k) + '개 더' }}</button>
              </div>
            </div>
          </template>
        </div>
        </template>
      </div>
    </template>
`;
export default TASK_SECTION_TEMPLATE;
