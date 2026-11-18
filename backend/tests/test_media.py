"""YouTube and media sites through yt-dlp (against a local stand-in)."""

import os

import pytest

from fetch_app.app import create_app
from fetch_app.providers.base import ErrorCode, ProviderContext, ProviderError
from fetch_app.providers.direct import DirectHttpProvider
from fetch_app.providers.media import MediaProvider, format_for, map_error

from .conftest import Api


class LocalMedia(MediaProvider):
    """Treats the test server's /video/ links as a media site.

    The guard proxy only tunnels HTTPS, and the stand-in speaks plain HTTP, so
    the proxy is left out here.
    """

    def can_handle(self, url):
        return "/video/" in url or super().can_handle(url)

    def _options(self, ctx, quality, workdir):
        ctx.proxy_url = None
        return super()._options(ctx, quality, workdir)


@pytest.fixture
def media_api(boot):
    app = create_app(boot, providers=[LocalMedia(), DirectHttpProvider()])
    with Api(app) as api:
        api.open()
        api.put("/settings", json={"downloads": {"retry_delay_seconds": 1}})
        yield api


def test_recognises_media_sites_and_spotify():
    provider = MediaProvider()
    for url in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=x",
        "https://vimeo.com/123",
        "https://soundcloud.com/a/b",
        "https://open.spotify.com/track/abc",
    ):
        assert provider.can_handle(url), url
    assert not provider.can_handle("https://example.com/video.mp4")
    assert not provider.can_handle("https://notyoutube.com/watch")


async def test_spotify_is_refused_because_of_drm():
    provider = MediaProvider()
    with pytest.raises(ProviderError) as caught:
        await provider.inspect("https://open.spotify.com/track/abc", ProviderContext(http=None))
    assert caught.value.code == ErrorCode.UNSUPPORTED
    assert "DRM" in caught.value.message


def test_format_selection():
    assert format_for("best", True) == "bv*+ba/b"
    assert format_for("best", False) == "b"
    assert format_for("1080", True) == "bv*[height<=1080]+ba/b[height<=1080]/b"
    assert format_for("720", False) == "b[height<=720]/b"
    assert format_for("audio", False).startswith("ba")


def test_error_mapping():
    assert map_error("ERROR: [youtube] x: Private video").code == ErrorCode.NOT_FOUND
    assert map_error("ERROR: Sign in to confirm your age").code == ErrorCode.AUTH_REQUIRED
    assert map_error("HTTP Error 429: Too Many Requests").retryable
    assert map_error("ERROR: Unsupported URL: https://x").code == ErrorCode.UNSUPPORTED


def test_media_link_downloads_through_yt_dlp(media_api, fileserver, boot):
    data = fileserver.add("clip.mp4", os.urandom(300_000))
    url = fileserver.url("video", "clip.mp4")
    item = media_api.post("/downloads/inspect", json={"urls": [url]}).json()["items"][0]
    assert item["provider"] == "media" and item["supported"], item
    job_id = media_api.submit(url)["created"][0]["id"]
    done = media_api.wait_state(job_id, "completed", "failed", timeout=60)
    assert done["state"] == "completed", done
    assert done["engine"] == "yt-dlp"
    assert done["filename"].endswith(".mp4")
    saved = boot.storage_root / "downloads" / str(media_api.user["id"]) / done["filename"]
    assert saved.read_bytes() == data
    assert done["downloaded_bytes"] == len(data)


def test_missing_media_fails_with_a_clear_reason(media_api, fileserver):
    url = fileserver.url("video", "nothing.mp4")
    item = media_api.post("/downloads/inspect", json={"urls": [url]}).json()["items"][0]
    assert item["supported"] is False and item["restriction"] == "not_found", item
