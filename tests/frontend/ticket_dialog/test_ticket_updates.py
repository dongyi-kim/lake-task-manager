"""Unread metadata batching and monotonic read-watermark behavior, without upstream requests."""
import json
import shutil
import subprocess

import pytest

from support.paths import STATIC_ROOT


def test_unread_activity_window_edits_batching_scope_and_late_responses():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend state tests")
    script = r"""
      import assert from 'node:assert/strict';
      globalThis.window=new EventTarget(); globalThis.CustomEvent=class extends Event {};
      globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
      const {createTicketUpdatesStore,isRecentUnread,commentUpdateAt,isMajorHistoryUpdate,RECENT_UPDATE_MS}=await import(MODULE);
      const now=Date.parse('2026-11-05T12:00:00Z'), day=86400000, old=now-8*day, recent=now-day;
      assert.equal(isRecentUnread(recent,null,now),true);
      assert.equal(isRecentUnread(recent,recent,now),false);
      assert.equal(isRecentUnread(old,null,now),false);
      assert.equal(isRecentUnread(now+1,null,now),false);
      assert.equal(isRecentUnread(now-RECENT_UPDATE_MS,null,now),true);
      assert.equal(isRecentUnread(commentUpdateAt({date:new Date(old).toISOString(),updated:new Date(recent).toISOString()},now),null,now),true);
      assert.equal(commentUpdateAt({date:recent,updated:now+1},now),recent);
      assert.equal(isMajorHistoryUpdate({kind:'status'}),true);
      assert.equal(isMajorHistoryUpdate({kind:'epic-link'}),true);
      assert.equal(isMajorHistoryUpdate({kind:'worklog'}),false);
      let activityCalls=0, viewedCalls=0, fail=false, clock=now, finish;
      const events=[];
      const transport={
        ticketActivity:async keys=>{
          activityCalls++; assert.deepEqual(keys,['DL-1','DL-2']);
          return new Promise(resolve=>finish=resolve);
        },
        ticketViewed:async(key,through)=>{viewedCalls++;if(fail)throw Error('disk');return{scope:'mock',key,lastViewedAt:through};},
      };
      const store=createTicketUpdatesStore(transport,event=>events.push(event),()=>clock);
      assert.equal(store.isUnread({key:'DL-1',updated:new Date(recent).toISOString()}),false,'unknown baseline is not an absent baseline');
      const first=store.observe([{key:'dl-1'},'DL-2']), second=store.observe(['DL-1']);
      await new Promise(resolve=>setImmediate(resolve)); assert.equal(activityCalls,1);
      finish({scope:'mock',tickets:{'DL-1':{latestRelevantAt:recent,lastViewedAt:null},'DL-2':{latestRelevantAt:old}}});
      await Promise.all([first,second]); assert.equal(store.isUnread('DL-1'),true); assert.equal(store.isUnread('DL-2'),false);
      assert.equal(store.isUnread({key:'DL-1',updated:now+1}),true,'future issue time cannot mask valid recent activity');
      await store.observe(['DL-1']); assert.equal(activityCalls,1);
      assert.equal(await store.acknowledge('DL-1',recent),true); assert.equal(store.isUnread('DL-1'),false);
      store.ingest({scope:'mock',tickets:{'DL-1':{latestRelevantAt:recent,lastViewedAt:null}}});
      assert.equal(store.isUnread('DL-1'),false,'late old activity cannot revive already read content');
      store.ingest({scope:'mock',tickets:{'DL-1':{latestRelevantAt:now}}});
      assert.equal(store.isUnread('DL-1'),true,'new event after opening must remain unread');
      await store.acknowledge('DL-1',recent); assert.equal(viewedCalls,1);
      fail=true; assert.equal(await store.acknowledge('DL-1',now),false); assert.equal(store.isUnread('DL-1'),true);
      assert.equal(await store.acknowledge('DL-1',now+1),false); assert.equal(viewedCalls,2);
      store.ingest({scope:'prod',tickets:{'DL-1':{latestRelevantAt:recent,lastViewedAt:null}}});
      assert.equal(store.metadata('DL-1').lastViewedAt,null,'different Jira context does not reuse read state');
      assert.equal(store.isUnread('DL-1'),true); assert.ok(events.length>=4);
      const batches=[];
      const large=createTicketUpdatesStore({ticketActivity:async keys=>{batches.push(keys.length);return{tickets:{}};}},()=>{},()=>now);
      await large.observe(Array.from({length:1001},(_,i)=>'DL-'+i));
      assert.deepEqual(batches,[500,500,1]);
    """.replace("MODULE", json.dumps((STATIC_ROOT / "lib/ticketUpdates.js").as_uri()))
    result = subprocess.run([node, "--input-type=module"], input=script, capture_output=True,
                            text=True, encoding="utf-8", timeout=12)
    assert result.returncode == 0, result.stderr


def test_comments_source_age_refresh_reuses_same_memo_and_preserves_old_data_on_failure():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend state tests")
    script = r"""
      import assert from 'node:assert/strict';
      const {CacheMemo}=await import(MODULE);
      const now=2000, calls=[], published=[];
      let fail=false;
      const memo=new CacheMemo(async (path,opts)=>{
        calls.push(path); assert.equal('freshAfter' in opts,false,'LTM hint is not a fetch option');
        if(path.includes('freshAfter=')) {
          if(fail) throw Error('refresh unavailable');
          opts.onCacheMetadata({fetchedAt:now,freshUntil:now+600,displayUntil:now+86400});
          return [{id:'edited',updated:'new'}];
        }
        opts.onCacheMetadata({fetchedAt:now-100,freshUntil:now+600,displayUntil:now+86400});
        return [{id:'edited',updated:'old'}];
      },event=>published.push(event),()=>now);
      const path='/api/issue/DL-1/comments';
      await memo.get(path); assert.equal(memo.metadata(path).fetchedAt,now-100);
      fail=true;
      await assert.rejects(memo.get(path,{freshAfter:(now-10)*1000}),/unavailable/);
      assert.equal(memo.peek(path)[0].updated,'old'); assert.equal(memo.metadata(path).fetchedAt,now-100);
      fail=false;
      assert.equal((await memo.get(path,{freshAfter:(now-10)*1000}))[0].updated,'new');
      assert.equal(memo.metadata(path).fetchedAt,now);
      await memo.get(path,{freshAfter:(now-10)*1000});
      assert.equal(calls.length,3); assert.equal(published.length,1);
      assert.equal(published[0].path,path); assert.equal(memo.rows.size,1);
      assert.match(calls[1],/revalidate=1&freshAfter=1990000/);
      const unknown=new CacheMemo(async()=>[],()=>{},()=>now);
      await unknown.get(path); assert.equal(unknown.metadata(path).fetchedAt,null,'browser load time is not upstream provenance');
      const previousRevision=memo.revision(path);
      memo.delete(path); assert.notEqual(memo.revision(path),previousRevision);
      await memo.get(path); assert.notEqual(memo.revision(path),previousRevision,'a mutation and refill cannot validate an old response');
      let complete=true;
      const full=new CacheMemo(async (_path,opts)=>{
        opts.onCacheMetadata({fetchedAt:now-100,freshUntil:now+600,displayUntil:now+86400});
        return complete ? {comments:[{id:'old'}],complete:true} : {comments:[],complete:false,partial:true};
      },()=>{},()=>now);
      await full.get(path); complete=false;
      assert.equal((await full.get(path,{freshAfter:now*1000})).complete,false);
      assert.equal(full.peek(path).comments[0].id,'old','incomplete refresh preserves prior complete display snapshot');
      assert.equal(full.metadata(path).fetchedAt,now-100);
    """.replace("MODULE", json.dumps((STATIC_ROOT / "lib/cacheMemo.js").as_uri()))
    result = subprocess.run([node, "--input-type=module"], input=script, capture_output=True,
                            text=True, encoding="utf-8", timeout=12)
    assert result.returncode == 0, result.stderr
