"""MCP/config revisions refresh dashboards without flushing unrelated ticket data."""
from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def test_config_revision_poll_refreshes_wbs_and_retains_ticket_memo(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base + '/#/guide')
    result = page.evaluate("""async () => {
      const {api} = await import('/lib/api.js');
      const Banner = (await import('/components/ui/StatusBanner.js')).default;
      const Wbs = (await import('/components/views/WbsView.js')).default;
      const originalFetch = window.fetch, originalRaw = api.raw, originalWbs = api.wbs;
      let badgeRequests = 0, calls = 0, revision = 'first', events = 0;
      window.fetch = (path, ...args) => {
        if (String(path).startsWith('/api/ticket/DL-9001/badge')) badgeRequests++;
        return originalFetch(path, ...args);
      };
      await api.ticketBadge('DL-9001');
      const before = badgeRequests;
      const view = {D:{version:1}, expanded:{keep:true}, partial:{}, scheduleRender(){calls++;}};
      api.wbs = async () => ({version:2});
      Wbs.created.call(view);
      const onUpdate = () => events++;
      window.addEventListener('workspace-config-updated', onUpdate);
      api.raw = async () => ({mode:'ok',workspaceRevision:revision});
      const banner = {};
      const tick = async () => { Banner.methods.tick.call(banner); await new Promise(r => setTimeout(r, 0)); };
      try {
        await tick(); // baseline is not a change
        const initial = events;
        revision = 'second'; await tick(); await tick();
        await api.ticketBadge('DL-9001');
        return {initial,events,version:view.D.version,expanded:view.expanded.keep,calls,
                ticketRefetched:badgeRequests !== before};
      } finally {
        Wbs.unmounted.call(view);
        window.removeEventListener('workspace-config-updated',onUpdate);
        window.fetch = originalFetch; api.raw = originalRaw; api.wbs = originalWbs;
      }
    }""")
    assert result == {"initial": 0, "events": 1, "version": 2, "expanded": True,
                      "calls": 1, "ticketRefetched": False}
    assert not errors, errors
