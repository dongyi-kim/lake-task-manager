"""Browser controls plus deterministic memo-age/race tests; no injected latency."""
import json
import shutil
import subprocess

from support.paths import REPO_ROOT
from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def test_browser_memo_age_swr_and_mutation_fence():
    module = (REPO_ROOT / "app/static/lib/cacheMemo.js").as_uri()
    script = """
      import assert from 'node:assert/strict';
      import { CacheMemo } from MODULE;
      let now=1000, calls=0, resolve;
      const updates=[];
      const fetcher=async (path,opts) => {
        calls++;
        if (path.includes('revalidate')) {
          const value=await new Promise(r=>resolve=r);
          opts.onCacheMetadata({fetchedAt:now,freshUntil:now+600,displayUntil:now+86400});
          return value;
        }
        opts.onCacheMetadata({fetchedAt:1000,freshUntil:1600,displayUntil:87400});
        return {summary:'old'};
      };
      const memo=new CacheMemo(fetcher,e=>updates.push(e),()=>now);
      assert.equal((await memo.get('/ticket')).summary,'old');
      assert.equal((await memo.get('/ticket')).summary,'old'); assert.equal(calls,1);
      now=1601;
      assert.equal((await memo.get('/ticket')).summary,'old');
      assert.equal((await memo.get('/ticket')).summary,'old'); assert.equal(calls,2);
      resolve({summary:'new'});
      await new Promise(r=>setImmediate(r));
      assert.equal(memo.peek('/ticket').summary,'new'); assert.equal(updates.length,1);
      now=2300; await memo.get('/ticket');
      memo.delete('/ticket'); // a write invalidated this request while it was pending
      resolve({summary:'outdated'});
      await new Promise(r=>setImmediate(r));
      assert.equal(memo.peek('/ticket'),undefined); assert.equal(updates.length,1);
      now=100000;
      await memo.get('/ticket');
      assert.equal(memo.peek('/ticket'),undefined); // expired server source cannot become new
    """.replace("MODULE", json.dumps(module))
    subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                   check=True, capture_output=True, text=True)


def test_cache_settings_save_reload_and_restore(editor_browser):
    page, base, errors, _ = editor_browser
    page.add_init_script("localStorage.setItem('ltm.guide-seen.v3', '{}')")
    page.goto(base)
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="표시 보존 · 갱신 간격 설정 →", exact=True).click()
    dialog = page.get_by_role("dialog", name="캐시 설정")
    dialog.get_by_label("일반 데이터 갱신 간격", exact=True).fill("20")
    dialog.get_by_role("button", name="저장", exact=True).click()
    page.get_by_role("status").filter(has_text="저장했습니다").wait_for()
    assert page.request.get(base + "/api/prefs").json()["cachePolicy"]["freshnessSeconds"] == 1200
    dialog.get_by_role("button", name="닫기", exact=True).click()
    page.reload()
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="표시 보존 · 갱신 간격 설정 →", exact=True).click()
    dialog.get_by_label("일반 데이터 갱신 간격", exact=True).wait_for()
    assert dialog.get_by_label("일반 데이터 갱신 간격", exact=True).input_value() == "20"
    dialog.get_by_role("button", name="기본값 복원").click()
    assert dialog.get_by_label("일반 데이터 갱신 간격", exact=True).input_value() == "10"
    dialog.get_by_role("button", name="저장", exact=True).click()
    page.get_by_role("status").filter(has_text="저장했습니다").wait_for()
    assert not errors, errors
