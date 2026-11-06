import { api } from "../../lib/api.js";
export default {
  name: "ConfluenceMacroTree", props: { descriptor: Object },
  data() { return { branches: {}, expanded: {}, pending: {}, errors: {}, query: "", files: [], expanding:false, searching:false, searchResults:[], searchNext:null, searchError:'' }; },
  computed: {
    rows() {
      const output = [];
      if (this.query.trim()) return this.searchResults.map(item=>({...item,id:String(item.id),level:0}));
      const walk = (parent, level, path) => {
        for (const item of this.branches[parent] || []) {
          const id = String(item.id); if (path.has(id)) continue;
          output.push({ ...item, id, level });
          if (this.expanded[id]) walk(id, level + 1, new Set([...path, id]));
        }
      };
      walk(this.descriptor.pageId || "root", 0, new Set()); return output;
    },
    loading() { return Object.values(this.pending).some(Boolean); },
  },
  async mounted() { this._alive = true; await this.load(this.descriptor.pageId || "root", 0); if (this.descriptor.name==='children' && this.descriptor.params.all==='true') this.expand(); },
  beforeUnmount() { this._alive = false; this._searchSeq=(this._searchSeq||0)+1; clearTimeout(this._searchTimer); },
  methods: {
    ordered(rows) {
      const params = this.descriptor.params, key = params.sort || params.sortBy || (this.descriptor.name==='attachments' ? 'date' : 'position');
      if (key === "position") return params.reverse === "true" ? rows.slice().reverse() : rows;
      const value = row => key === "modified" || key === "date" ? row.version?.when || "" : key === "creation" ? row.history?.createdDate || row.created || "" : key === "size" ? +(row.extensions?.fileSize || 0) : row.title;
      const direction = this.descriptor.name==='attachments' ? ((params.sortOrder==='descending' ? -1 : 1) * (['size','date'].includes(key) ? -1 : 1)) : params.reverse==='true' ? -1 : 1;
      return rows.slice().sort((a,b) => (key === "size" ? value(a)-value(b) : key==='bitwise' ? (value(a)<value(b)?-1:value(a)>value(b)?1:0) : String(value(a)).localeCompare(String(value(b)), undefined, {numeric:true})) * direction);
    },
    async load(parent, level = 0) {
      if (this.pending[parent] || !this._alive) return;
      this.pending[parent] = true; this.errors[parent] = "";
      let start = 0; const seen = new Set(), rows = [];
      try {
        do {
          if (seen.has(start)) throw new Error("목록 페이지가 반복되었습니다. 다시 시도하세요."); seen.add(start);
          const data = this.descriptor.name === "attachments" ? await api.confluenceAttachments(this.descriptor.pageId,start)
            : await api.confluencePages(this.descriptor.space,parent === "root" ? "" : parent,start);
          if (!this._alive) return;
          rows.push(...(data.results || []));
          const ordered = this.ordered([...new Map(rows.map(item => [String(item.id),item])).values()]);
          if (this.descriptor.name === "attachments") this.files = ordered; else this.branches[parent] = ordered;
          start = data._links?.next ? Number(data.start || 0) + Number(data.size ?? data.results?.length ?? 0) : null;
        } while (start !== null);
        const depth = Math.min(5,Math.max(1,Number(this.descriptor.params.startDepth || this.descriptor.params.depth || 1)));
        // Expansion is sequential to avoid a request burst when a page contains many macros.
        if (this.descriptor.name !== "attachments" && level + 1 < depth) for (const item of rows) {
          if (!this._alive) return; const id = String(item.id); if (id === parent || this.branches[id]) continue;
          this.expanded[id] = true; await this.load(id,level+1);
        }
      } catch (error) { if (this._alive) this.errors[parent] = error.message; }
      finally { if (this._alive) this.pending[parent] = false; }
    },
    async toggle(row) { this.expanded[row.id] = !this.expanded[row.id]; if (this.expanded[row.id] && !this.branches[row.id]) await this.load(row.id,5); },
    collapse() { this.expanding=false; this.expanded = {}; },
    async expand() {
      if (this.expanding) { this.expanding=false; return; }
      this.expanding=true;
      const queue=[...(this.branches[this.descriptor.pageId || 'root'] || [])], seen=new Set();
      while (this._alive && this.expanding && queue.length) {
        const row=queue.shift(), id=String(row.id); if (seen.has(id)) continue; seen.add(id);
        this.expanded[id]=true; if (!this.branches[id]) await this.load(id,5);
        queue.push(...(this.branches[id] || []));
      }
      this.expanding=false;
    },
    scheduleSearch() { clearTimeout(this._searchTimer); this._searchSeq=(this._searchSeq||0)+1; this.searchResults=[]; this.searchNext=null; this.searching=!!this.query.trim(); this._searchTimer=setTimeout(()=>this.search(),250); },
    async search(more=false) {
      const seq=this._searchSeq=(this._searchSeq||0)+1; this.searchError='';
      if (!this.query.trim()) { this.searching=false; return; } this.searching=true;
      try { const data=await api.confluenceSearch(this.query,this.descriptor.space,more?this.searchNext:0,this.descriptor.pageId);
        if (!this._alive || seq!==this._searchSeq) return;
        const rows=(data.results||[]).map(item=>item.content||item);
        this.searchResults=[...new Map([...(more?this.searchResults:[]),...rows].map(item=>[String(item.id),item])).values()];
        this.searchNext=data._links?.next ? Number(data.start||0)+Number(data.size??rows.length) : null;
      } catch(error) { if (seq===this._searchSeq && this._alive) this.searchError=error.message; }
      finally { if (seq===this._searchSeq && this._alive) this.searching=false; }
    },
    fileUrl(file) { const path = file._links?.download; return path ? `/api/confluence/pages/${encodeURIComponent(this.descriptor.pageId)}/file?path=${encodeURIComponent(path)}` : ""; },
  },
  template: `<div class="conf-macro-tree"><input v-if="descriptor.params.searchBox==='true'" v-model="query" @input="scheduleSearch" aria-label="트리 문서 검색" placeholder="이 트리의 문서 검색">
    <div v-if="descriptor.params.expandCollapseAll==='true'"><button class="btn" @click="expand">{{expanding?'펼치기 중단':'모두 펼치기'}}</button><button class="btn" @click="collapse">모두 접기</button></div>
    <p v-if="searching" role="status">문서 검색 중…</p><p v-if="searchError" role="alert">{{searchError}} <button class="btn" @click="search()">검색 다시 시도</button></p>
    <p v-if="loading" role="status">문서 목록을 불러오는 중…</p>
    <p v-for="(message,parent) in errors" v-show="message" role="alert">{{message}} <button class="btn" @click="load(parent)">다시 시도</button></p>
    <div v-for="row in rows" :key="row.id" class="conf-macro-tree-row" :style="{paddingLeft:(row.level*16)+'px'}"><button class="conf-fold" :aria-label="row.title+' 하위 문서'" :aria-expanded="!!expanded[row.id]" @click="toggle(row)">{{expanded[row.id]?'▾':'▸'}}</button><a :href="'#/confluence/'+row.id">{{row.title}}</a></div>
    <ul v-if="descriptor.name==='attachments'"><li v-for="file in files"><a v-if="fileUrl(file)" :href="fileUrl(file)" download>{{file.title}}</a><span v-else>{{file.title}}</span><small> v{{file.version?.number}}</small></li></ul>
    <button v-if="query.trim() && searchNext!==null" class="btn" :disabled="searching" @click="search(true)">검색 결과 더 보기</button>
    <p v-if="!loading && !Object.values(errors).some(Boolean) && !rows.length && !files.length" class="conf-muted">표시할 항목이 없습니다.</p>
  </div>`,
};
