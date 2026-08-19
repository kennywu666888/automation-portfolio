import tempfile
import json
from pathlib import Path

import 設定
import 社團留言核心 as v64
from 媒體來源 import MediaPool, media_kind


class FakePage:
    def wait_for_timeout(self, _milliseconds):
        pass


class EmptyRoleLocator:
    def count(self):
        return 0


class FakeFileInput:
    def __init__(self, article):
        self.article = article

    def get_attribute(self, name):
        if name == "accept":
            return "image/*,video/*,video/mp4"
        return None

    def set_input_files(self, value, **_kwargs):
        if value == []:
            self.article.attached = None
        else:
            self.article.attached = media_kind(value)


class FakeInputLocator:
    def __init__(self, article):
        self.item = FakeFileInput(article)

    def count(self):
        return 1

    def nth(self, _index):
        return self.item


class FakeArticle:
    def __init__(self):
        self.attached = None

    def locator(self, selector):
        assert selector == 'input[type="file"]'
        return FakeInputLocator(self)

    def get_by_role(self, *_args, **_kwargs):
        return EmptyRoleLocator()

    def evaluate(self, _script):
        return {
            "images": 3 + (1 if self.attached == "photo" else 0),
            "videos": 1 if self.attached == "video" else 0,
            "remove_buttons": 1 if self.attached else 0,
            "busy": 0,
            "file_count": 1 if self.attached else 0,
        }


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    media = root / "media"
    media.mkdir()
    for name in ("one.jpg", "two.PNG"):
        (media / name).write_bytes(b"photo")
    for name in ("one.mp4", "two.WEBM"):
        (media / name).write_bytes(b"video")
    (media / "ignored.txt").write_text("ignored", encoding="utf-8")

    pool = MediaPool.from_settings(
        {
            "group_comment_media_mode": "random",
            "group_comment_random_media_dir": str(media),
        }
    )
    assert pool.photo_count == 2
    assert pool.video_count == 2
    claimed = [pool.claim() for _ in range(4)]
    assert len(set(claimed)) == 4, "同一環境不得重複使用同一媒體"
    assert sorted(media_kind(path) for path in claimed) == [
        "photo",
        "photo",
        "video",
        "video",
    ]

    fixed_file = media / "one.mp4"
    fixed_pool = MediaPool.from_settings(
        {
            "group_comment_media_mode": "fixed",
            "group_comment_fixed_media_file": str(fixed_file),
        }
    )
    assert fixed_pool.photo_count == 0
    assert fixed_pool.video_count == 1
    assert fixed_pool.claim() == fixed_file.resolve()
    assert fixed_pool.claim() == fixed_file.resolve(), "固定模式每次必須使用同一檔案"

    photo = media / "one.jpg"
    article = FakeArticle()
    assert v64.attach_comment_media(FakePage(), article, photo) == "photo"
    assert v64.comment_media_draft_present(article)
    v64.clear_comment_media(article, FakePage())
    assert not v64.comment_media_draft_present(article)

    video = media / "one.mp4"
    article = FakeArticle()
    assert v64.attach_comment_media(FakePage(), article, video) == "video"
    assert v64.comment_media_draft_present(article)
    v64.clear_comment_media(article, FakePage())
    assert not v64.comment_media_draft_present(article)


original_settings_path = config.SETTINGS_PATH
try:
    with tempfile.TemporaryDirectory() as folder:
        legacy_path = Path(folder) / "settings.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "group_comment_media_mode": "video",
                    "group_comment_photo_dir": "C:/legacy/photos",
                    "group_comment_video_dir": "C:/legacy/videos",
                }
            ),
            encoding="utf-8",
        )
        config.SETTINGS_PATH = legacy_path
        migrated = config.load_settings()
        assert migrated["group_comment_media_mode"] == "random"
        assert migrated["group_comment_random_media_dir"] == "C:/legacy/videos"
        assert "group_comment_photo_dir" not in migrated
        assert "group_comment_video_dir" not in migrated
finally:
    config.SETTINGS_PATH = original_settings_path


source = (Path(__file__).parent / "group_comment_v64.py").read_text(encoding="utf-8")
assert "set_input_files" in source
assert "附加相片或影片" in source
assert "comment_media_draft_present" in source

task_source = (Path(__file__).parent / "group_comment_task.py").read_text(encoding="utf-8")
assert "media_path=media_path" in task_source
assert 'media_successes["photo"]' in task_source
assert 'media_successes["video"]' in task_source

gui_source = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
assert "相片／影片隨機資料夾" in gui_source
assert "固定相片／影片檔案" in gui_source
assert "group_comment_photo_dir" not in gui_source
assert "group_comment_video_dir" not in gui_source

print("RC19 media-comment tests passed")
