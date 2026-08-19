import tempfile
import threading
from pathlib import Path

import 社團留言任務 as task_module
import 社團留言核心 as v64
from 環境管理客戶端 import ProfileInfo
from 社團留言任務 import GroupCommentTask, GroupUrlQueue
from text_sources import load_text_lines


ROOT = Path(__file__).resolve().parent
assert (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip().endswith("RC19")


class FakeLogger:
    def __init__(self):
        self.lines = []

    def _add(self, level, message, args):
        self.lines.append((level, message % args if args else message))

    def info(self, message, *args):
        self._add("INFO", message, args)

    def warning(self, message, *args):
        self._add("WARNING", message, args)


class FakePage:
    def __init__(self):
        self.current_group = ""

    def set_default_timeout(self, _timeout):
        pass

    def goto(self, url, **_kwargs):
        self.current_group = url

    def wait_for_timeout(self, _milliseconds):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [object()]
        self.page = page
        self.closed = False

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    def connect_over_cdp(self, *_args, **_kwargs):
        return self.browser


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeStarter:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


def run_fake_task(path, urls, snapshots, target=1):
    path.write_text("\n".join(urls) + "\n", encoding="utf-8-sig")
    queue = GroupUrlQueue(path, delete_after_claim=True)
    logger = FakeLogger()
    page = FakePage()
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)

    originals = {
        "start_profile": v64.start_profile,
        "sync_playwright": v64.sync_playwright,
        "choose_facebook_page": v64.choose_facebook_page,
        "collect_comment_box_snapshots": v64.collect_comment_box_snapshots,
        "get_comment_box_by_token": v64.get_comment_box_by_token,
        "extract_author_from_comment_box": v64.extract_author_from_comment_box,
        "build_post_key": v64.build_post_key,
        "article_has_admin_badge": v64.article_has_admin_badge,
        "test_one_comment_box": v64.test_one_comment_box,
        "verified_small_scroll": v64.verified_small_scroll,
        "no_growth": v64.AUTHOR_NO_GROWTH_LIMIT,
        "load_text_lines": task_module.load_text_lines,
    }
    try:
        v64.start_profile = lambda _profile_id: "ws://offline-test"
        v64.sync_playwright = lambda: FakeStarter(playwright)
        v64.choose_facebook_page = lambda _context: page
        v64.collect_comment_box_snapshots = lambda _page: list(snapshots)
        v64.get_comment_box_by_token = lambda _page, token: {"token": token}
        v64.extract_author_from_comment_box = lambda _page, _box, group: (
            v64.AuthorResult("Author", "https://facebook.test/author", group.name, group.url),
            group.url,
        )
        v64.build_post_key = lambda article, _author, _group_url: (article, article)
        v64.article_has_admin_badge = lambda _article: False
        v64.test_one_comment_box = lambda _page, _box, group, text, _mode, **_kwargs: (
            v64.CommentTestResult(
                "Author",
                "https://facebook.test/author",
                group.name,
                group.url,
                group.url,
                True,
                True,
                True,
                text,
                "submitted",
                "offline",
            )
        )
        v64.verified_small_scroll = lambda _page, _name: True
        v64.AUTHOR_NO_GROWTH_LIMIT = 1
        task_module.load_text_lines = lambda _path: ["offline comment"]

        result = GroupCommentTask(
            ProfileInfo("profile-id", "56"),
            {
                "comments_per_profile": target,
                "group_comment_max_scrolls": 1,
                "author_dedupe_scope": "group",
            },
            logger,
            threading.Event(),
            queue,
        ).run()
        return result, logger, browser, playwright, queue
    finally:
        v64.start_profile = originals["start_profile"]
        v64.sync_playwright = originals["sync_playwright"]
        v64.choose_facebook_page = originals["choose_facebook_page"]
        v64.collect_comment_box_snapshots = originals["collect_comment_box_snapshots"]
        v64.get_comment_box_by_token = originals["get_comment_box_by_token"]
        v64.extract_author_from_comment_box = originals["extract_author_from_comment_box"]
        v64.build_post_key = originals["build_post_key"]
        v64.article_has_admin_badge = originals["article_has_admin_badge"]
        v64.test_one_comment_box = originals["test_one_comment_box"]
        v64.verified_small_scroll = originals["verified_small_scroll"]
        v64.AUTHOR_NO_GROWTH_LIMIT = originals["no_growth"]
        task_module.load_text_lines = originals["load_text_lines"]


with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "group.txt"
    first = "https://www.facebook.com/groups/first"
    unopened = "https://www.facebook.com/groups/unopened"
    result, logger, browser, playwright, queue = run_fake_task(
        path,
        [first, unopened],
        [v64.CommentBoxSnapshot("box-1", 10.0, "comment")],
    )
    retained = path.read_text(encoding="utf-8-sig")
    assert result.status == "SUCCESS"
    assert first not in retained
    assert unopened in retained, "達標後不得預領或刪除下一個未開啟網址"
    assert queue.retained_count() == 1
    assert browser.closed and playwright.stopped
    assert any("新增框=1" in line for _, line in logger.lines)


with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "group.txt"
    zero_success = "https://www.facebook.com/groups/zero-success"
    result, logger, _browser, _playwright, queue = run_fake_task(
        path,
        [zero_success],
        [],
    )
    assert result.status == "FAILED"
    assert zero_success in path.read_text(encoding="utf-8-sig")
    assert queue.retained_count() == 1
    assert any("本群 0 成功" in line for _, line in logger.lines)
    assert any("0成功群組=1" in line for _, line in logger.lines)


source = (ROOT / "group_comment_task.py").read_text(encoding="utf-8")
assert 'self.settings.get("author_dedupe_scope", "group")' in source
assert 'if round_stats["new_boxes"] == 0' in source

gui_source = (ROOT / "gui.py").read_text(encoding="utf-8")
assert "load_group_urls()" in gui_source
assert "threading.Thread(target=self._run_engine" in gui_source

logger_source = (ROOT / "logger.py").read_text(encoding="utf-8")
assert "class ImmediateUtf8FileHandler" in logger_source
assert 'logger.info("RC19 LOG 系統已啟動' in logger_source

v64_source = (ROOT / "group_comment_v64.py").read_text(encoding="utf-8")
assert "V5 公開留言結果" in v64_source

with tempfile.TemporaryDirectory() as folder:
    source = Path(folder) / "multiline.txt"
    source.write_text(
        "第一則第一行 😊\n"
        "第一則第二行\n"
        "\n"
        "保留空白行上方\n"
        "---\n"
        "第二則 👍🏽\n"
        "第二行 🚀\n",
        encoding="utf-8-sig",
    )
    messages = load_text_lines(source)
    assert messages == [
        "第一則第一行 😊\n第一則第二行\n\n保留空白行上方",
        "第二則 👍🏽\n第二行 🚀",
    ]

    legacy = Path(folder) / "legacy.txt"
    legacy.write_text("舊文案一 😊\n舊文案二\n", encoding="utf-8-sig")
    assert load_text_lines(legacy) == ["舊文案一 😊", "舊文案二"]


class FakeUnicodeEditor:
    def __init__(self):
        self.filled = None
        self.sequential_calls = 0

    def scroll_into_view_if_needed(self, **_kwargs):
        pass

    def click(self, **_kwargs):
        pass

    def focus(self, **_kwargs):
        pass

    def press(self, *_args, **_kwargs):
        pass

    def fill(self, text, **_kwargs):
        self.filled = text

    def insert_text(self, _text, **_kwargs):
        raise AssertionError("fill 成功時不應再走 insert_text")

    def press_sequentially(self, *_args, **_kwargs):
        self.sequential_calls += 1


editor = FakeUnicodeEditor()
multiline_emoji = "第一行 😊\n第二行 🚀"
v64.fill_contenteditable(editor, multiline_emoji)
assert editor.filled == multiline_emoji
assert editor.sequential_calls == 0, "多行或 Emoji 不得用逐鍵輸入"

print("RC19 public-comment regression tests passed")
