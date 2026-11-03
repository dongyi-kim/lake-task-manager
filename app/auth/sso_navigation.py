"""Record identity-provider origins visited by LTM's own login pages, never a user profile."""
from urllib.parse import urlsplit


def observe_login_page(page):
    context = page.context
    if not hasattr(context, "_ltm_auth_origins"):
        context._ltm_auth_origins = set()

    def record(raw_url):
        url = urlsplit(raw_url)
        if url.scheme in ("http", "https") and url.hostname:
            context._ltm_auth_origins.add(f"{url.scheme}://{url.netloc}")

    def visited(frame):
        if frame == page.main_frame:
            record(frame.url)

    def requested(request):
        # HTTP redirects may set IdP cookies without ever committing a document/frame.
        if request.is_navigation_request() and request.frame == page.main_frame:
            record(request.url)

    page.on("framenavigated", visited)
    page.on("request", requested)


def login_state(context):
    state = context.storage_state()
    # Store-only metadata; SsoStore.merged returns only Playwright's standard fields.
    state["authOrigins"] = sorted(getattr(context, "_ltm_auth_origins", ()))
    return state
