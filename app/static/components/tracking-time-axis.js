import { layoutTimeAxis } from './trackingTimeAxisLayout.js';

export default {
  name:'TrackingTimeAxis',
  props:{items:{type:Array,default:()=>[]},range:{type:Object,required:true},ticks:{type:Array,default:()=>[]},sourceKey:{type:String,default:'combined'},selectedEventId:String},
  emits:['focus'],
  data(){return {width:320,measuredWidths:{},activeId:null,commentGroup:null};},
  computed:{
    layout(){return layoutTimeAxis(this.items,this.range,this.width,this.measuredWidths);},
    originals(){return this.layout.items.flatMap(item=>item.members||[item]).sort((a,b)=>a.at-b.at);},
    visibleTicks(){
      const step=Math.max(1,Math.ceil((this.ticks.length-1)/Math.max(2,Math.floor(this.width/90)))), result=[];
      for(let i=0;i<this.ticks.length;i++){
        if(i%step&&i!==this.ticks.length-1)continue;
        const tick=this.ticks[i], size=[...tick.label].reduce((sum,char)=>sum+(/[^\x00-\x7f]/.test(char)?12:7),0)+2;
        const left=i===0?0:i===this.ticks.length-1?this.width-size:tick.x/100*this.width-size/2;
        if(i===this.ticks.length-1)while(result.length>1&&result.at(-1).right+12>left)result.pop();
        if(!result.length||result.at(-1).right+12<=left)result.push({...tick,right:left+size});
      }
      return result;
    },
  },
  watch:{items(){this.closeComments(false);this.measureSoon();},range(){this.closeComments(false);this.measureSoon();}},
  mounted(){
    this._observer=new ResizeObserver(()=>this.measureSoon());this._observer.observe(this.$refs.plot);
    this.measureSoon();
  },
  beforeUnmount(){this._observer?.disconnect();cancelAnimationFrame(this._measureFrame);},
  methods:{
    measureSoon(){
      cancelAnimationFrame(this._measureFrame);
      this._measureFrame=requestAnimationFrame(()=>this.$nextTick(()=>{
        if(!this.$refs.plot)return;
        const width=this.$refs.plot.clientWidth;
        if(width>0&&width!==this.width)this.width=width;
        const measured={};
        for(const label of this.$refs.plot.querySelectorAll('.tt-callout'))measured[label.dataset.layoutKey]=label.getBoundingClientRect().width;
        if(JSON.stringify(measured)!==JSON.stringify(this.measuredWidths))this.measuredWidths=measured;
      }));
    },
    fullTime(item){return new Date(item.at+9*3600000).toISOString().slice(0,19).replace('T',' ')+' KST';},
    rangeText(item){return this.fullTime({at:item.startAt??item.at})+(item.endAt>item.startAt?' — '+this.fullTime({at:item.endAt}):'');},
    accessible(item){return item.isCluster?this.rangeText(item)+' '+item.short+' · '+item.sourceCount+'개 작업'+(item.hasOwn&&item.hasChildren?' · 자체·하위 포함':'')+' · 원본 기록 펼치기':this.fullTime(item)+' '+(item.own?'자체':'하위')+' '+item.title+' '+item.issueKey+' '+item.label+' '+(item.detail||item.short)+' 근거 보기';},
    selected(item){return item.members?.some(member=>member.id===this.selectedEventId)||this.selectedEventId===item.id;},
    timeX(at){return (at-this.range.start)/(this.range.end-this.range.start)*this.width;},
    markerClasses(item){return ['tt-'+item.tone, item.own?(item.comment?'tt-comment':'tt-transition'):'tt-child-event',
      {'tt-comment-group':item.comment&&item.isCluster,'is-cluster':item.isCluster,'is-child':!item.own,'is-related':this.activeId===item.uid,'is-selected':this.selected(item)}];},
    activate(item,event){
      if(!item.isCluster){this.$emit('focus',item.id);return;}
      this._trigger=event.currentTarget;this.commentGroup=item;
      this.$nextTick(()=>this.$refs.closeComments?.focus({preventScroll:true}));
    },
    openAll(event){
      const members=this.originals;if(!members.length)return;
      this.activate({uid:'all',isCluster:true,title:'이 구간의 원본 기록',short:'전체 '+members.length+'건',members,sourceCount:new Set(members.map(item=>item.issueKey)).size,startAt:members[0].at,endAt:members.at(-1).at},event);
    },
    closeComments(restore=true){
      this.commentGroup=null;
      if(restore&&this._trigger?.isConnected)this._trigger.focus({preventScroll:true});
      this._trigger=null;
    },
    evidence(item){this.$emit('focus',item.id);this.closeComments(false);},
    clearHover(){this.activeId=this.$refs.plot?.querySelector(':focus')?.dataset.layoutKey||null;},
    trapFocus(event){
      if(event.key==='Escape'){event.stopPropagation();event.preventDefault();this.closeComments();return;}
    },
  },
  template:`<div class="tt-axis" :data-source-key="sourceKey">
    <div class="tt-axis-ticks" aria-label="한국 시간 눈금"><time v-for="tick in visibleTicks" :key="tick.at" :datetime="new Date(tick.at).toISOString()" :title="tick.end?'선택 기간의 끝 · 다음 날 00시':tick.label" :style="{left:tick.x+'%'}">{{ tick.label }}</time></div>
    <div class="tt-axis-plot" ref="plot" :style="{height:layout.height+'px'}">
      <i v-for="tick in visibleTicks" :key="tick.at" class="tt-axis-guide" :style="{left:tick.x+'%'}" aria-hidden="true"></i>
      <div class="tt-axis-rail" :style="{top:layout.baseline+'px'}" aria-hidden="true"></div>
      <i v-for="tick in visibleTicks" :key="'tick:'+tick.at" class="tt-axis-notch" :style="{left:tick.x+'%',top:layout.baseline-5+'px'}" aria-hidden="true"></i>
      <svg class="tt-leaders" :width="width" :height="layout.height" aria-hidden="true">
        <template v-for="item in layout.items" :key="item.uid">
          <line v-if="item.level" class="tt-marker-stem" :x1="item.x" :y1="layout.baseline" :x2="item.x" :y2="item.y" />
          <line v-if="item.isCluster&&item.endAt>item.startAt" class="tt-cluster-span" :x1="timeX(item.startAt)" :x2="timeX(item.endAt)" :y1="item.y" :y2="item.y" />
          <path v-if="item.showCallout" class="tt-leader" :class="['tt-'+item.tone,{'is-related':activeId===item.uid||selected(item)}]" :data-event-id="item.id" :data-layout-key="item.uid" :d="'M '+item.x+' '+item.y+' L '+item.x+' '+item.bendY+' L '+(item.left+item.width/2)+' '+item.tipY" />
        </template>
      </svg>
      <template v-for="item in layout.items" :key="item.uid">
        <button type="button" class="tt-marker" :class="markerClasses(item)" :data-event-id="item.id" :data-member-ids="JSON.stringify(item.members.map(member=>member.id))" :data-count="item.members.length" :data-grouped="item.isCluster?'true':'false'" :data-source-count="item.sourceCount" :data-layout-key="item.uid" :data-at="item.at" :data-issue-key="item.issueKey" :style="{left:item.x+'px',top:item.y+'px'}" :aria-label="accessible(item)" :aria-expanded="item.isCluster?commentGroup?.uid===item.uid:null" :aria-current="selected(item)?'true':null" :title="accessible(item)" @click="activate(item,$event)" @pointerenter="activeId=item.uid" @pointerleave="clearHover" @focus="activeId=item.uid" @blur="activeId=null">
          <svg class="tt-marker-icon" :class="{'tt-comment-icon':item.comment}" viewBox="0 0 16 16" aria-hidden="true">
            <path v-if="item.comment" d="M2 2.5h12v8H7l-4 3v-3H2z" />
            <template v-else><circle cx="8" cy="8" r="7"/><path v-if="item.tone==='done'" d="m4 8 3 3 5-6"/><path v-else-if="item.tone==='reopen'" d="M4 7a4 4 0 1 1 1 5M4 3v4h4"/><path v-else-if="item.tone==='new'" d="M8 4v8M4 8h8"/><path v-else-if="item.tone==='mixed'||item.tone==='change'" d="M4 6h8M4 10h8"/><path v-else d="m6 4 5 4-5 4z"/></template>
          </svg><span v-if="item.isCluster" class="tt-marker-count">{{ item.members.length }}</span>
        </button>
        <button v-if="item.showCallout" type="button" class="tt-callout" :class="['tt-'+item.tone,{'is-related':activeId===item.uid,'is-selected':selected(item)}]" :data-event-id="item.id" :data-layout-key="item.uid" :style="{left:item.left+'px',top:item.labelY+'px',maxWidth:item.width+'px'}" :aria-label="accessible(item)" :title="accessible(item)" @click="activate(item,$event)" @pointerenter="activeId=item.uid" @pointerleave="clearHover" @focus="activeId=item.uid" @blur="activeId=null"><span class="tt-callout-source">{{ item.source }}</span><span class="tt-callout-key" v-if="item.issueKey&&!item.own">{{ item.issueKey }}</span><span aria-hidden="true">·</span><strong>{{ item.short }}</strong></button>
      </template>
      <span v-if="!items.length" class="tt-axis-empty" :style="{top:layout.baseline+12+'px'}">이 구간에 확인된 기록 없음</span>
    </div>
    <div class="tt-axis-footer"><span>{{ layout.totalCount }}건<template v-if="layout.groupedCount"> · 밀집 기록 묶음 표시</template><template v-else> · 실제 발생 시각</template></span><button v-if="layout.totalCount" type="button" @click="openAll">원본 기록 {{ layout.totalCount }}건</button></div>
    <div v-if="commentGroup" class="tt-event-dialog tt-comment-dialog" role="dialog" :aria-label="commentGroup.short+' 원본 기록'" @keydown="trapFocus">
      <div class="tt-comment-dialog-heading"><b>{{ commentGroup.title }} · {{ commentGroup.short }}</b><button ref="closeComments" type="button" aria-label="기록 목록 닫기" @click="closeComments()">×</button></div>
      <p>{{ rangeText(commentGroup) }} · {{ commentGroup.members.length }}건 · {{ commentGroup.sourceCount }}개 작업</p>
      <div class="tt-comment-list"><button v-for="member in commentGroup.members" :key="member.uid" type="button" :data-event-id="member.id" :data-issue-key="member.issueKey" @click="evidence(member)"><strong>{{ member.title }} <small>{{ member.issueKey }}</small></strong><time>{{ fullTime(member) }} · {{ member.label }}</time><span>{{ member.detail || member.label }}</span><small>근거 보기 ↗</small></button></div>
    </div>
  </div>`,
};
