export default {
  props: { target: Object }, emits: ['create','close'],
  mounted() {
    const menu = this.$refs.menu;
    menu.showPopover();
    const box = menu.getBoundingClientRect();
    menu.style.left = Math.max(8,Math.min(this.target.x,innerWidth-box.width-8))+'px';
    menu.style.top = Math.max(8,Math.min(this.target.y,innerHeight-box.height-8))+'px';
    menu.querySelector('button').focus();
  },
  template: `<div ref="menu" popover="auto" role="menu" aria-label="문서 메뉴" class="conf-page-menu"
    @toggle="$event.newState==='closed' && $emit('close')" @keydown.esc.stop="$emit('close')">
    <p>{{target.row.title}}</p><button role="menuitem" @click="$emit('create',target.row)">＋ 하위 문서 만들기</button>
  </div>`,
};
