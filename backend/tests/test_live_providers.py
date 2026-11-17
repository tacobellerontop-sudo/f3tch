"""Opt-in checks against live providers, using a file you uploaded yourself.

Skipped unless the environment names a test file, so the normal suite never
touches the network. Only the public file page is read: nothing is downloaded,
no timer is started and no CAPTCHA is requested.

    FETCH_LIVE_RAPIDGATOR_URL=https://rapidgator.net/file/<id>/<name>.html
"""

import os

import pytest

from fetch_app.engine.http import HttpClient
from fetch_app.providers.base import ProviderContext
from fetch_app.providers.rapidgator import RapidgatorProvider
from fetch_app.security.urlguard import UrlGuard

LIVE_URL = os.environ.get("FETCH_LIVE_RAPIDGATOR_URL", "")

pytestmark = pytest.mark.skipif(not LIVE_URL, reason="FETCH_LIVE_RAPIDGATOR_URL is not set")


async def test_live_rapidgator_anonymous_inspection():
    http = HttpClient(UrlGuard())
    try:
        adapter = RapidgatorProvider()
        assert adapter.can_handle(LIVE_URL)
        info = await adapter.inspect(LIVE_URL, ProviderContext(http=http))
        assert info.provider == "rapidgator" and info.filename
        print(f"name={info.filename!r} size={info.size} premium_only={info.premium_only}")
    finally:
        await http.close()
