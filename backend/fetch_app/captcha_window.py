"""The CAPTCHA window: a provider's own page, opened inside the FETCH app.

Some providers (Rapidgator's free downloads) protect a download with a CAPTCHA
widget that only works on their own website. The desktop app can open that
page in a window of its own. The person completes the provider's steps and
CAPTCHA there themselves, exactly as in a browser; FETCH only watches for the
download address the provider then offers and takes it over, so it does not
have to be copied and pasted by hand.

FETCH never solves, skips or automates the CAPTCHA: the window is an ordinary
browser window that the person uses.

:class:`Handover` holds the part that needs no window library, so it is tested
on any system; :class:`CaptchaPage` is the window's side (see ``desktop.py``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .security.urlguard import UnsafeUrlError

log = logging.getLogger("fetch.captcha_window")

EXPIRED = "This CAPTCHA is no longer waiting in FETCH. Close this window and retry the download in FETCH."
WINDOW_SIZE = (1100, 820)


class Handover:
    """Hands a download address found in the CAPTCHA window to the waiting download."""

    def __init__(self, ctx: Any, job_id: int, provider: str) -> None:
        self.ctx, self.job_id, self.provider_name = ctx, job_id, provider

    @property
    def provider(self) -> Any:
        return self.ctx.registry.get(self.provider_name)

    def waiting(self) -> bool:
        challenge = self.ctx.manager.captcha(self.job_id)
        return challenge is not None and challenge.accepts_download_url

    def find(self, page: str) -> str | None:
        """The provider's download address on ``page``, if it shows one."""
        provider = self.provider
        return provider.find_download_address(page) if provider is not None else None

    def problem(self, url: str) -> str | None:
        """Why ``url`` cannot be handed over, or ``None``."""
        if not self.waiting():
            return EXPIRED
        try:
            self.ctx.guard.check_url(url)
        except UnsafeUrlError as exc:
            return str(exc)
        provider = self.provider
        if provider is None:
            return "This download's provider is not available."
        return provider.check_download_address(url)

    def hand_over(self, url: str) -> str | None:
        """Continue the download from ``url``. Returns why it could not, or ``None``."""
        problem = self.problem(url)
        if problem:
            return problem
        manager = self.ctx.manager
        try:
            row = manager.run_from_thread(manager.submit_captcha(self.job_id, {"download_url": url}))
        except Exception:
            log.warning("The download address could not be handed over", exc_info=True)
            return "FETCH could not take over the download. Try again, or retry the download in FETCH."
        if row is None:
            return EXPIRED
        try:
            self.ctx.activity.record(
                "download", "download.captcha_answered",
                f"CAPTCHA completed by the user in FETCH's window: {row.filename or row.url}",
                user_id=row.user_id, actor_id=row.user_id, target=str(self.job_id),
            )
        except Exception:
            log.warning("The activity entry could not be written", exc_info=True)
        return None


#: Shown at the bottom of the provider's page, and how clicks on its download
#: button are caught before the window itself would download the file.
PAGE_SCRIPT = r"""
(function () {
  var message = %(message)s;
  var bar = document.getElementById('__fetch_bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = '__fetch_bar';
    bar.setAttribute('style', 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;' +
      'padding:10px 16px;background:#16181d;color:#e7e9ee;border-top:2px solid #7c93ff;' +
      'font:14px/1.45 "Segoe UI",system-ui,sans-serif;text-align:left');
    (document.body || document.documentElement).appendChild(bar);
  }
  bar.textContent = message;
  if (window.__fetchWatching) return;
  window.__fetchWatching = true;
  var steps = /\/download\/(captcha|ajax)/i;
  function address(link) {
    try {
      var url = new URL(link.href, location.href);
      if (!/^https?:$/.test(url.protocol)) return '';
      return /\/download\//i.test(url.pathname) && !steps.test(url.pathname) ? url.href : '';
    } catch (e) { return ''; }
  }
  document.addEventListener('click', function (event) {
    var link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    var url = link ? address(link) : '';
    if (!url || !window.pywebview || !window.pywebview.api) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    var bar = document.getElementById('__fetch_bar');
    if (bar) bar.textContent = 'Handing the download to FETCH…';
    window.pywebview.api.found(url).then(function (problem) {
      if (bar) bar.textContent = problem || 'FETCH has the download. This window closes now.';
    });
  }, true);
})();
"""

GUIDE = (
    "FETCH: complete the free download steps and the CAPTCHA on this page yourself. When the "
    "download button appears, FETCH takes the download over and closes this window."
)


def page_script(message: str = GUIDE) -> str:
    return PAGE_SCRIPT % {"message": json.dumps(message)}


class PageApi:
    """All that the provider's page may call: offer one download address.

    pywebview exposes the public methods of this object to the page. The
    address is checked like a pasted one before anything happens.
    """

    def __init__(self, page: CaptchaPage) -> None:
        self._page = page

    def found(self, url: Any) -> str:
        return self._page.found(str(url or ""))


class CaptchaPage:
    """One CAPTCHA window for one waiting download."""

    def __init__(self, handover: Handover, on_done: Any = None) -> None:
        self.handover = handover
        self.on_done = on_done or (lambda page: None)
        self.window: Any = None
        self.api = PageApi(self)
        self.done = threading.Event()
        self._lock = threading.Lock()

    def on_loaded(self) -> None:
        """Each page the window loads: show the guide, and look for the download address."""
        window = self.window
        if window is None or self.done.is_set():
            return
        try:
            window.evaluate_js(page_script())
        except Exception:
            log.debug("The guide could not be shown on the page", exc_info=True)
        try:
            current = window.get_current_url() or ""
        except Exception:
            current = ""
        # Only the steps after the CAPTCHA can carry the address; the file's own
        # page is never scanned.
        if "/download/" not in urlsplit(current).path:
            return
        try:
            page = window.evaluate_js("document.documentElement.outerHTML")
        except Exception:
            log.debug("The page could not be read", exc_info=True)
            return
        address = self.handover.find(page) if isinstance(page, str) else None
        if address:
            problem = self.found(address)
            if problem:
                try:
                    window.evaluate_js(page_script(problem))
                except Exception:
                    pass

    def found(self, url: str) -> str:
        """The page offered ``url``. Returns why it was not taken, or '' when it was."""
        with self._lock:
            if self.done.is_set():
                return ""
            problem = self.handover.hand_over(url)
            if problem:
                return problem
            self.done.set()
        threading.Thread(target=self._finish, name="fetch-captcha-done", daemon=True).start()
        return ""

    def _finish(self) -> None:
        time.sleep(1.5)  # long enough to read that FETCH has it
        window = self.window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                log.warning("The CAPTCHA window could not be closed", exc_info=True)
        self.on_done(self)
