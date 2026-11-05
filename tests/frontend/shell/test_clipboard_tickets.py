"""Clipboard parsing and lifecycle run without touching the OS clipboard or Jira."""
import json
import shutil
import subprocess

import pytest

from support.paths import STATIC_ROOT


def test_clipboard_reader_limits_origin_focus_permission_and_lifetime():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend event tests")
    source = (STATIC_ROOT / "lib/clipboardTickets.js").read_text(encoding="utf-8")
    script = "const source = " + json.dumps(source) + ";\n" + r"""
    const {clipboardTicketKeys: parse, watchClipboardTickets: watch} =
      await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    const assert = (ok, why) => { if (!ok) throw Error(why); };
    const equal = (actual, expected, why) => assert(JSON.stringify(actual) === JSON.stringify(expected), why);
    const base = 'https://jira.example/jira';
    equal(parse('See (https://jira.example/jira/browse/dl-1). https://jira.example/jira/browse/DL-1\n'
      + 'https://jira.example/jira/secure/RapidBoard.jspa?selectedIssue=DL-2', base), ['DL-1','DL-2'], 'dedup and board URL');
    equal(parse('https://jira.example/browse/DL-1 https://jira.example/jira2/browse/DL-2 '
      + 'https://jira.example.evil/jira/browse/DL-3 http://jira.example/jira/browse/DL-4 '
      + 'https://user:pass@jira.example/jira/browse/DL-5 https://jira.example:999/jira/browse/DL-6 '
      + 'https://jira.example/jira/%zz https://jira.example/jira/browse/DL-7/extra DL-8', base), [], 'strict server/path matching');
    equal(parse('https://jira.example/jira/browse/DL-1', ''), [], 'unknown Jira must not match');

    const win = new EventTarget(), doc = new EventTarget(), permission = new EventTarget();
    const timers = new Map(); let seq = 0, focused = true, reads = 0, text = base + '/browse/DL-1';
    let fail = false, pending = null, shown = [], open = new Set();
    doc.visibilityState = 'visible'; doc.hasFocus = () => focused;
    permission.state = 'granted';
    const clipboard = new EventTarget();
    clipboard.readText = () => {
      reads++;
      if (pending) return pending;
      if (fail) return Promise.reject(Object.assign(Error('denied'), {name:'NotAllowedError'}));
      return Promise.resolve(text);
    };
    win.navigator = {clipboard, permissions:{query:async () => permission}};
    win.setTimeout = (fn, ms) => { assert(ms === 2000, 'poll cadence'); timers.set(++seq, fn); return seq; };
    win.clearTimeout = (id) => timers.delete(id);
    const tick = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
    const poll = async () => { const f = Array.from(timers.values())[0]; assert(f, 'poll scheduled'); f(); await tick(); };
    const reader = watch({jiraBase:base, onKeys:keys=>shown=keys, isOpen:key=>open.has(key)}, {window:win,document:doc});
    await tick(); equal(shown,['DL-1'],'initial suggestion'); assert(reads===1 && timers.size===1,'initial single read');
    reader.dismiss(); await poll(); equal(shown,[],'dismissed same value');
    text = 'unrelated text'; await poll(); text = base+'/browse/DL-1 '+base+'/browse/DL-2'; await poll();
    equal(shown,['DL-1','DL-2'],'changed clipboard reappears');
    const before = reads; open.add('DL-1'); reader.refresh(); equal(shown,['DL-2'],'already open filtered');
    assert(reads===before,'open-key changes do not read clipboard');
    focused=false; win.dispatchEvent(new Event('blur')); equal(shown,[],'blur hides');
    assert(timers.size===0,'background polling stopped');
    win.dispatchEvent(new Event('focus')); await tick(); assert(reads===before,'unfocused event cannot read');
    focused=true; let finish; pending=new Promise(resolve=>finish=resolve);
    win.dispatchEvent(new Event('focus')); win.dispatchEvent(new Event('focus')); await tick();
    assert(reads===before+1,'single flight');
    focused=false; doc.visibilityState='hidden'; doc.dispatchEvent(new Event('visibilitychange'));
    pending=null; finish(base+'/browse/DL-99'); await tick(); equal(shown,[],'late hidden completion ignored');
    doc.visibilityState='visible'; focused=true; win.dispatchEvent(new Event('focus')); await tick();
    equal(shown,['DL-2'],'visible read recovers');
    fail=true; await poll(); const deniedReads=reads;
    win.dispatchEvent(new Event('focus')); await tick();
    assert(reads===deniedReads && timers.size===0,'denial does not repeat permission request');
    fail=false; permission.dispatchEvent(new Event('change')); await tick(); equal(shown,['DL-2'],'manual permission grant recovers');
    reader.stop(); const stoppedReads=reads; win.dispatchEvent(new Event('focus')); await tick();
    assert(reads===stoppedReads && timers.size===0,'stop removes listeners/timers'); equal(shown,[],'stop hides');

    clipboard.onclipboardchange=null; text=base+'/browse/DL-3'; permission.state='denied';
    const eventReader=watch({jiraBase:base,onKeys:keys=>shown=keys},{window:win,document:doc});
    await tick(); assert(reads===stoppedReads,'known denied permission never reads');
    permission.state='granted'; permission.dispatchEvent(new Event('change')); await tick();
    equal(shown,['DL-3'],'grant wakes'); assert(timers.size===0,'native change event avoids polling');
    text=base+'/browse/DL-4'; clipboard.dispatchEvent(new Event('clipboardchange')); await tick();
    equal(shown,['DL-4'],'clipboard event reads'); eventReader.stop();
    clipboard.dispatchEvent(new Event('clipboardchange')); await tick(); equal(shown,[],'event detached');

    // A delayed Permissions API result must not override a readText rejection.
    let resolvePermission; fail=true; permission.state='prompt';
    win.navigator.permissions.query=()=>new Promise(resolve=>resolvePermission=resolve);
    const raceReader=watch({jiraBase:base,onKeys:keys=>shown=keys},{window:win,document:doc});
    win.dispatchEvent(new Event('focus')); await tick(); const rejectedReads=reads;
    resolvePermission(permission); await tick();
    win.dispatchEvent(new Event('focus')); permission.dispatchEvent(new Event('change')); await tick();
    assert(reads===rejectedReads,'late prompt permission cannot retry a refused read');
    raceReader.stop();
    """
    result = subprocess.run([node, "--input-type=module"], input=script, text=True,
                            capture_output=True, timeout=15, encoding="utf-8")
    assert result.returncode == 0, result.stderr
