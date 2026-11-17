import { layoutTimeAxis } from './trackingTimeAxisLayout.js';

export default {
  name:'TrackingTimeAxis',
  props:{items:{type:Array,default:()=>[]},range:{type:Object,required:true},ticks:{type:Array,default:()=>[]},sourceKey:{type:String,default:'combined'},selectedEventId:String},
  emits:['focus'],
  data(){return {width:320,measuredWidths:{},activeId:null,commentGroup:null};},
  computed:{layout(){return layoutTimeAxis(this.items,this.range,this.width,this.measuredWidths);}},
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
    fullTime(item){return new Date(item.at+9*3600000).toISOString().slice(0,16).replace('T',' ')+' KST';},
    accessible(item){return this.fullTime(item)+' '+(item.own?'자체':'하위')+' '+item.title+' '+item.issueKey+' '+item.label+' '+(item.detail||item.short)+' 근거 보기';},
    markerClasses(item){return ['tt-'+item.tone, item.own?(item.comment?'tt-comment':'tt-transition'):'tt-child-event',
      {'tt-comment-group':item.comment&&item.members.length>1,'is-child':!item.own,'is-related':this.activeId===item.uid,'is-selected':this.selectedEventId===item.id||item.members?.some(member=>member.id===this.selectedEventId)}];},
    activate(item,event){
      if(!item.comment||item.members.length===1){this.$emit('focus',item.id);return;}
      this._trigger=event.currentTarget;this.commentGroup=item;
      this.$nextTick(()=>this.$refs.closeComments?.focus({preventScroll:true}));
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
    <div class="tt-axis-plot" ref="plot" :style="{height:layout.height+'px'}">
      <i v-for="tick in ticks" :key="tick.at" class="tt-axis-guide" :style="{left:tick.x+'%'}" aria-hidden="true"></i>
      <div class="tt-axis-rail" :style="{top:layout.baseline+'px'}" aria-hidden="true"></div>
      <svg class="tt-leaders" :width="width" :height="layout.height" aria-hidden="true">
        <template v-for="item in layout.items" :key="item.uid">
          <line v-if="item.level" class="tt-marker-stem" :x1="item.x" :y1="layout.baseline" :x2="item.x" :y2="item.y" />
          <path v-if="!item.comment" class="tt-leader" :class="['tt-'+item.tone,{'is-related':activeId===item.uid||selectedEventId===item.id}]" :data-event-id="item.id" :data-layout-key="item.uid" :d="'M '+item.x+' '+item.y+' L '+item.x+' '+item.bendY+' L '+(item.left+item.width/2)+' '+item.tipY" />
        </template>
      </svg>
      <template v-for="item in layout.items" :key="item.uid">
        <button type="button" class="tt-marker" :class="markerClasses(item)" :data-event-id="item.id" :data-layout-key="item.uid" :data-at="item.at" :data-issue-key="item.issueKey" :style="{left:item.x+'px',top:item.y+'px'}" :aria-label="item.comment&&item.members.length>1?fullTime(item)+' '+item.title+' '+item.issueKey+' 댓글·수정 '+item.members.length+'건 펼치기':accessible(item)" :aria-expanded="item.comment&&item.members.length>1?commentGroup?.uid===item.uid:null" :aria-current="selectedEventId===item.id?'true':null" :title="accessible(item)" @click="activate(item,$event)" @pointerenter="activeId=item.uid" @pointerleave="clearHover" @focus="activeId=item.uid" @blur="activeId=null">
          <svg class="tt-marker-icon" :class="{'tt-comment-icon':item.comment}" viewBox="0 0 16 16" aria-hidden="true">
            <path v-if="item.comment" d="M2 2.5h12v8H7l-4 3v-3H2z" />
            <template v-else><circle cx="8" cy="8" r="7"/><path v-if="item.tone==='done'" d="m4 8 3 3 5-6"/><path v-else-if="item.tone==='reopen'" d="M4 7a4 4 0 1 1 1 5M4 3v4h4"/><path v-else-if="item.tone==='new'" d="M8 4v8M4 8h8"/><path v-else d="m6 4 5 4-5 4z"/></template>
          </svg><span v-if="item.comment&&item.members.length>1" class="tt-marker-count">{{ item.members.length }}</span>
        </button>
        <button v-if="!item.comment" type="button" class="tt-callout" :class="['tt-'+item.tone,{'is-related':activeId===item.uid,'is-selected':selectedEventId===item.id}]" :data-event-id="item.id" :data-layout-key="item.uid" :style="{left:item.left+'px',top:item.labelY+'px'}" :aria-label="accessible(item)" :title="item.issueKey+' · '+item.source+' · '+item.detail" @click="$emit('focus',item.id)" @pointerenter="activeId=item.uid" @pointerleave="clearHover" @focus="activeId=item.uid" @blur="activeId=null"><span class="tt-callout-source">{{ item.source }}</span><span class="tt-callout-key" v-if="!item.own">{{ item.issueKey }}</span><span aria-hidden="true">·</span><strong>{{ item.short }}</strong></button>
      </template>
      <span v-if="!items.length" class="tt-axis-empty" :style="{top:layout.baseline+12+'px'}">이 구간에 확인된 기록 없음</span>
    </div>
    <div v-if="commentGroup" class="tt-comment-dialog" role="dialog" :aria-label="commentGroup.issueKey+' 댓글·수정 '+commentGroup.members.length+'건'" @keydown="trapFocus">
      <div class="tt-comment-dialog-heading"><b>{{ commentGroup.title }} · {{ commentGroup.issueKey }}</b><button ref="closeComments" type="button" aria-label="댓글 목록 닫기" @click="closeComments()">×</button></div>
      <p>{{ fullTime(commentGroup.members[0]) }}<template v-if="commentGroup.members.length>1"> — {{ fullTime(commentGroup.members.at(-1)) }}</template> · {{ commentGroup.members.length }}건</p>
      <div class="tt-comment-list"><button v-for="member in commentGroup.members" :key="member.id" type="button" :data-event-id="member.id" @click="evidence(member)"><time>{{ fullTime(member) }}</time><span>{{ member.detail || member.label }}</span><small>근거 보기 ↗</small></button></div>
    </div>
  </div>`,
};
