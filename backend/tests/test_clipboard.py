"""Link grabbing from the clipboard."""

import asyncio

from fetch_app.clipboard import ClipboardWatcher, extract_urls, looks_like_file
from fetch_app.providers.registry import builtin_providers


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, event, data=None, **kwargs):
        self.events.append((event, data, kwargs))


class FakeRegistry:
    def __init__(self):
        self.providers = sorted(builtin_providers(), key=lambda p: p.priority)

    def detect(self, url):
        return next((p for p in self.providers if p.can_handle(url)), None)


class FakeSettings:
    def __init__(self, on=True):
        self.on = on

    def get(self):
        class General:
            watch_clipboard = self.on

        class Settings:
            general = General

        return Settings


def test_extract_urls_from_any_text():
    text = (
        "Here you go: https://mega.nz/file/AbC#key, and (https://example.com/a.zip). "
        '<a href="https://www.youtube.com/watch?v=abc">x</a> https://en.wikipedia.org/wiki/Foo_(bar) '
        "https://mega.nz/file/AbC#key"
    )
    assert extract_urls(text) == [
        "https://mega.nz/file/AbC#key",
        "https://example.com/a.zip",
        "https://www.youtube.com/watch?v=abc",
        "https://en.wikipedia.org/wiki/Foo_(bar)",
    ]


def test_only_file_links_of_plain_websites_are_offered():
    assert looks_like_file("https://example.com/setup.exe")
    assert looks_like_file("https://example.com/movie.part1.rar")
    assert looks_like_file("https://example.com/archive.r01")
    assert not looks_like_file("https://example.com/news/article")
    assert not looks_like_file("https://example.com/index.html")


def make_watcher(on=True):
    bus = FakeBus()
    return ClipboardWatcher(bus=bus, registry=FakeRegistry(), settings=FakeSettings(on), reader=lambda: None), bus


def test_new_clipboard_text_with_links_is_announced_once():
    watcher, bus = make_watcher()
    assert watcher.check("https://mega.nz/file/old#k") == [], "what was there at start-up is ignored"
    text = "https://example.com/page https://example.com/x.zip https://youtu.be/abc"
    assert watcher.check(text) == ["https://example.com/x.zip", "https://youtu.be/abc"]
    assert watcher.check(text) == [], "the same text is not announced twice"
    assert watcher.check("just words") == []
    assert bus.events == [("clipboard.links", {"urls": ["https://example.com/x.zip", "https://youtu.be/abc"]}, {"audience": "all"})]


def test_watcher_polls_the_reader_and_respects_the_setting():
    texts = iter(["start", "https://pixeldrain.com/u/abc12345"] + ["https://pixeldrain.com/u/abc12345"] * 100)
    settings = FakeSettings(on=False)
    bus = FakeBus()
    watcher = ClipboardWatcher(bus=bus, registry=FakeRegistry(), settings=settings, reader=lambda: next(texts), interval=0.01)

    async def run():
        watcher.start()
        await asyncio.sleep(0.05)
        assert bus.events == [], "nothing is read while the setting is off"
        settings.on = True
        await asyncio.sleep(0.1)
        await watcher.stop()

    asyncio.run(run())
    assert [e[1]["urls"] for e in bus.events] == [["https://pixeldrain.com/u/abc12345"]]
