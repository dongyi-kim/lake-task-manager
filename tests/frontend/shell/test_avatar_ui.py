"""Profile photo failures expire and recover without reloading healthy images."""
import json
import shutil
import subprocess
from urllib.parse import unquote

from support.paths import REPO_ROOT
from frontend.editor.test_editor_roundtrip_ui import PNG_1X1, editor_browser  # noqa: F401


def test_negative_avatar_cache_expires_and_clears_on_recovery():
    module = (REPO_ROOT / "app/static/lib/avatarFailures.js").as_uri()
    script = """
      import assert from 'node:assert/strict';
      globalThis.window = new EventTarget();
      let now = 1000; Date.now = () => now;
      const {avatarFailed, markAvatarFailed} = await import(MODULE);
      markAvatarFailed('temporary');
      assert.equal(avatarFailed('temporary'), true);
      now += 59999; assert.equal(avatarFailed('temporary'), true);
      now += 1; assert.equal(avatarFailed('temporary'), false);
      for (const event of ['auth-ok', 'online']) {
        markAvatarFailed('temporary'); markAvatarFailed('other');
        window.dispatchEvent(new Event(event));
        assert.equal(avatarFailed('temporary'), false);
        assert.equal(avatarFailed('other'), false);
      }
    """.replace("MODULE", json.dumps(module))
    subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                   check=True, capture_output=True, text=True)


def test_failed_photos_recover_but_successful_avatars_and_mentions_stay(editor_browser):
    page, base, errors, _ = editor_browser
    available = {"good": True, "failed": False, "offline": False,
                 "mention-good": True, "mention-failed": False}
    calls = {}

    def photo(route):
        user = unquote(route.request.url.rsplit("/", 1)[-1])
        calls[user] = calls.get(user, 0) + 1
        if available[user]:
            route.fulfill(status=200, content_type="image/png", body=PNG_1X1)
        else:
            route.fulfill(status=503, content_type="text/plain", body="temporary failure")

    page.route("**/api/avatar/*", photo)
    page.route("**/?avatar-recovery-test", lambda route: route.fulfill(
        content_type="text/html", body="<div id='avatars'></div><div id='mentions'></div>"))
    page.goto(base + "/?avatar-recovery-test")
    page.evaluate("""async () => {
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const Avatar = (await import('/components/ui/Avatar.js')).default;
      const {createMentionAvatar} = await import('/lib/mentionBadge.js');
      window.avatarApp = createApp({components:{Avatar}, template:`<div>
        <div id="good"><Avatar user="good" name="Healthy" /></div>
        <div id="failed"><Avatar user="failed" name="Expired session" /></div>
        <div id="offline"><Avatar user="offline" name="Offline" /></div></div>`});
      avatarApp.mount('#avatars');
      for (const id of ['mention-good','mention-failed']) {
        const box=document.createElement('div'); box.id=id;
        box.append(createMentionAvatar(id,id)); document.querySelector('#mentions').append(box);
      }
    }""")
    page.locator("#failed .avt-ini").wait_for()
    page.locator("#offline .avt-ini").wait_for()
    page.locator("#mention-failed [data-avatar-missing]").wait_for()
    page.wait_for_function("document.querySelector('#good img')?.naturalWidth > 0 && document.querySelector('#mention-good img')?.naturalWidth > 0")
    page.evaluate("window.healthyPhoto = document.querySelector('#good img'); window.healthyMention = document.querySelector('#mention-good img')")
    available.update({"failed": True, "mention-failed": True})
    page.evaluate("window.dispatchEvent(new Event('auth-ok'))")
    page.wait_for_function("document.querySelector('#failed img')?.naturalWidth > 0 && document.querySelector('#mention-failed img')?.naturalWidth > 0")
    page.locator("#offline .avt-ini").wait_for()
    available["offline"] = True
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_function("document.querySelector('#offline img')?.naturalWidth > 0")
    assert page.evaluate("healthyPhoto === document.querySelector('#good img') && healthyMention === document.querySelector('#mention-good img')")
    assert calls == {"good": 1, "failed": 2, "offline": 3,
                     "mention-good": 1, "mention-failed": 2}
    page.evaluate("avatarApp.unmount(); document.querySelector('#mentions').replaceChildren(); window.dispatchEvent(new Event('auth-ok'))")
    assert not [error for error in errors if "HTTP 503 " not in error], errors
