"""Activity grouping changes presentation without losing evidence or mutating it."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_feed_grouping_order_kst_boundaries_and_saved_preference():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    module = (REPO_ROOT / 'app/static/components/tracking-feed.js').as_uri()
    script = r'''
      import assert from 'node:assert/strict';
      const {default: component, groupActivityEvents} = await import(MODULE);
      const events = [
        {id:'a',issueKey:'A-1',date:'2026-11-01T15:30:00Z'},
        {id:'b',issueKey:'B-1',date:'2026-11-02T01:00:00+09:00'},
        {id:'c',issueKey:'A-1',date:'2026-11-01T23:30:00+09:00'},
        {id:'d',issueKey:'A-1',date:'2026-11-02T01:00:00+09:00'},
        {id:'unknown',issueKey:'B-1',date:'unknown'},
      ];
      const before=structuredClone(events);
      const all=groupActivityEvents(events,'none');
      assert.equal(all.length,1);assert.equal(all[0].tickets.length,1);
      assert.deepEqual(all[0].tickets[0].events.map(e=>e.id),['b','d','a','c','unknown']);
      const tickets=groupActivityEvents(events,'ticket');
      assert.deepEqual(tickets[0].tickets.map(g=>g.issueKey),['B-1','A-1']);
      assert.deepEqual(tickets[0].tickets[1].events.map(e=>e.id),['d','a','c']);
      const days=groupActivityEvents(events,'date-ticket');
      assert.deepEqual(days.map(g=>g.date),['2026-11-02','2026-11-01','날짜 미확인']);
      assert.deepEqual(days[0].tickets.map(g=>g.issueKey),['B-1','A-1']);
      for (const mode of ['none','ticket','date-ticket','invalid']) {
        const grouped=groupActivityEvents(events,mode);
        const flat=grouped.flatMap(day=>day.tickets.flatMap(ticket=>ticket.events));
        assert.equal(flat.length,events.length);
        assert.deepEqual(flat.map(e=>e.id).sort(),events.map(e=>e.id).sort());
        assert.deepEqual(groupActivityEvents([],mode),[]);
      }
      assert.deepEqual(events,before);
      const memory=new Map();
      globalThis.localStorage={getItem:key=>memory.get(key),setItem:(key,value)=>memory.set(key,value)};
      const vm=component.data();assert.equal(vm.grouping,'none');
      component.methods.setGrouping.call(vm,'date-ticket');
      assert.equal(component.data().grouping,'date-ticket');
      component.methods.setGrouping.call(vm,'invalid');assert.equal(vm.grouping,'none');
      globalThis.localStorage={getItem(){throw Error('disabled')},setItem(){throw Error('disabled')}};
      assert.equal(component.data().grouping,'none');
      component.methods.setGrouping.call(vm,'ticket');assert.equal(vm.grouping,'ticket');
    '''.replace('MODULE', json.dumps(module))
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
