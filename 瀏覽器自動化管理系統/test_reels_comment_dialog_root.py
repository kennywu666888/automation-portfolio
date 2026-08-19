from pathlib import Path

from 短影音留言 import ReelsCommentTask


source = (Path(__file__).parent / "reels_comment.py").read_text(encoding="utf-8")

assert ReelsCommentTask._reel_id("https://www.facebook.com/reel/1100083702450891/?x=1") == "1100083702450891"
assert ReelsCommentTask._reel_id("https://www.facebook.com/profile.php?id=1") == ""

# Facebook 專業模式的 Reels 留言框位於 dialog 底部，不在留言 article 內。
assert "def _find_active_reel_dialog" in source
assert '"/reel/" in str(self.driver.current_url' in source
assert "for dialog in reversed(dialogs)" in source
assert "reel_dialog = self._find_active_reel_dialog()" in source
assert "reel_dialog = self._find_active_reel_dialog(wait_seconds=6.0)" in source
assert "comment_root = reel_dialog or article" in source
assert "self._safe_multiline_input(box, comment, comment_root)" in source
assert "self._like_own_comment(comment_root, comment, own_comment)" in source

# 第一篇控制列未完成渲染時不可退到第二篇：先鎖定第一個 /reel/ID，
# 彈窗開啟後再核對 ID，不一致時回到精確 URL。
assert "def _article_reel_url" in source
assert "def _open_exact_reel_dialog" in source
assert "已鎖定個人主頁第一篇 Reels" in source
assert "拒絕猜測第二篇" in source
assert "current_reel_id != target_reel_id" in source
assert "comment_root = self._open_exact_reel_dialog(target_reel_url)" in source

print("Reels comment dialog-root regression tests passed")
