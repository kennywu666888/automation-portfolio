from __future__ import annotations

import random
import re
import os
import shutil
import threading
from collections import Counter
from pathlib import Path
from typing import Iterator, List, Optional, Set

import 社團留言核心 as v64
from 環境管理客戶端 import ProfileInfo
from 任務結果 import TaskResult
from text_sources import load_text_lines
from 媒體來源 import MediaPool, media_kind


DESKTOP = Path.home() / "Desktop"
GROUP_FILE = DESKTOP / "group.txt"


def configure_adspower_api(settings: dict) -> None:
    base = str(settings.get("adspower_base_url", "")).rstrip("/")
    if base:
        v64.ADSPOWER_API = base + "/api/v1"
    api_key = str(settings.get("adspower_api_key", "")).strip()
    v64.ADSPOWER_HEADERS = (
        {"Authorization": f"Bearer {api_key}"} if api_key else {}
    )


def load_groups(path: Path = GROUP_FILE) -> List[v64.GroupResult]:
    """Accept one URL per line or V6.4's exported TXT block format."""
    if not path.exists():
        raise FileNotFoundError(f"找不到群組網址檔：{path}")
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    matches = re.findall(
        r"https?://(?:www\.|m\.|web\.)?facebook\.com/groups/[A-Za-z0-9_.-]+[^\s]*",
        content,
        flags=re.I,
    )
    groups = []
    seen = set()
    for raw in matches:
        url = v64.normalize_group_url(raw.rstrip("),.;，。"))
        if not url or url in seen:
            continue
        seen.add(url)
        groups.append(
            v64.GroupResult(
                name=v64.group_id_from_url(url),
                url=url,
                today_posts=10,
                members=None,
                privacy="Unknown",
                activity_text="group.txt",
            )
        )
    if not groups:
        raise RuntimeError(f"{path} 沒有可用的 Facebook Group 網址")
    return groups


class GroupUrlQueue:
    """Thread-safe, run-wide queue backed by group.txt.

    Claiming reserves a URL only in memory so two workers cannot use it in the
    same run.  The source file is changed only after a group has produced at
    least one confirmed comment.  Failed, skipped, interrupted, and merely
    prefetched groups therefore remain available for a later run.
    """

    def __init__(
        self,
        path: Path = GROUP_FILE,
        delete_after_claim: bool = True,
    ):
        self.path = Path(path)
        # Keep the old constructor argument for compatibility with existing
        # callers; RC19 applies it only when finalize(successful=True) runs.
        self.delete_after_success = bool(delete_after_claim)
        self.delete_after_claim = self.delete_after_success
        self._lock = threading.Lock()
        self._groups = load_groups(self.path)
        self._source_groups = list(self._groups)
        self._inflight: Set[str] = set()
        self._committed: Set[str] = set()
        self._backup_created = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._groups)

    def __iter__(self) -> Iterator[v64.GroupResult]:
        while True:
            group = self.claim()
            if group is None:
                return
            yield group

    def claim(self) -> Optional[v64.GroupResult]:
        with self._lock:
            if not self._groups:
                return None
            group = self._groups.pop(0)
            self._inflight.add(group.url)
            return group

    def finalize(self, group: v64.GroupResult, successful: bool) -> bool:
        """Release a claim and commit its removal only after confirmed success."""
        with self._lock:
            self._inflight.discard(group.url)
            if not successful or not self.delete_after_success:
                return False
            if group.url in self._committed:
                return True
            self._committed.add(group.url)
            try:
                self._persist_retained_locked()
            except Exception:
                self._committed.discard(group.url)
                raise
            return True

    def retained_count(self) -> int:
        with self._lock:
            return len(self._source_groups) - len(self._committed)

    def _persist_retained_locked(self) -> None:
        if not self._backup_created:
            backup = self.path.with_name(self.path.name + ".bak")
            backup_temporary = self.path.with_name(
                f".{backup.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                shutil.copy2(self.path, backup_temporary)
                os.replace(backup_temporary, backup)
            finally:
                if backup_temporary.exists():
                    backup_temporary.unlink()
            self._backup_created = True

        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        retained = [
            group for group in self._source_groups
            if group.url not in self._committed
        ]
        content = "".join(f"{group.url}\n" for group in retained)
        try:
            temporary.write_text(content, encoding="utf-8-sig")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


class GroupCommentTask:
    """Independent V6.4 group-comment workflow for one AdsPower profile."""

    def __init__(
        self,
        profile: ProfileInfo,
        settings: dict,
        logger,
        stop_event: threading.Event,
        group_queue: Optional[GroupUrlQueue] = None,
    ):
        self.profile = profile
        self.settings = settings
        self.log = logger
        self.stop_event = stop_event
        self.group_queue = group_queue

    def run(self) -> TaskResult:
        target = max(1, int(self.settings.get("comments_per_profile", 1)))
        max_scrolls = max(1, int(self.settings.get("group_comment_max_scrolls", 20)))
        groups = self.group_queue if self.group_queue is not None else load_groups()
        total_groups = len(groups)
        group_iterator = None if isinstance(groups, GroupUrlQueue) else iter(groups)
        dedupe_scope = str(
            self.settings.get("author_dedupe_scope", "group")
        ).strip().lower()
        if dedupe_scope not in {"group", "environment", "none"}:
            dedupe_scope = "group"
        comment_file = str(
            self.settings.get("group_comment_text_file")
            or (DESKTOP / "文一.txt")
        )
        comments = load_text_lines(comment_file)
        media_pool = MediaPool.from_settings(self.settings)
        configure_adspower_api(self.settings)

        self.log.info(
            "開始獨立群組留言任務：目標=%d｜group.txt=%d 群｜文案=%d 則｜"
            "作者去重=%s｜媒體=%s（相片=%d／影片=%d）｜檔案=%s",
            target,
            total_groups,
            len(comments),
            dedupe_scope,
            media_pool.mode,
            media_pool.photo_count,
            media_pool.video_count,
            comment_file,
        )

        playwright = None
        browser = None
        completed = 0
        failed = 0
        skipped = 0
        tested_posts: Set[str] = set()
        environment_authors: Set[str] = set()
        opened_groups = 0
        zero_success_groups = 0
        removed_groups = 0
        retained_groups = 0
        all_skip_reasons = Counter()
        media_successes = Counter()

        try:
            ws_url = v64.start_profile(self.profile.profile_id)
            playwright = v64.sync_playwright().start()
            browser = playwright.chromium.connect_over_cdp(
                ws_url, timeout=v64.PAGE_TIMEOUT_MS
            )
            if not browser.contexts:
                raise RuntimeError("找不到 AdsPower 瀏覽器 Context")
            page = v64.choose_facebook_page(browser.contexts[0])
            page.set_default_timeout(v64.PAGE_TIMEOUT_MS)

            group_index = 0
            while not self.stop_event.is_set() and completed < target:
                # Check completion before claiming.  A for-loop asks the queue
                # for its next item before entering the body and used to delete
                # one unopened URL after the target had already been reached.
                group = (
                    groups.claim()
                    if isinstance(groups, GroupUrlQueue)
                    else next(group_iterator, None)
                )
                if group is None:
                    break
                group_index += 1
                opened_groups += 1
                self.log.info(
                    "環境 %s 開始群組 %d/%d：%s｜累積 %d/%d",
                    self.profile.name,
                    group_index,
                    total_groups,
                    group.url,
                    completed,
                    target,
                )
                group_count = 0
                group_stats = Counter()
                group_authors: Set[str] = set()
                seen_tokens: Set[str] = set()
                terminal_tokens: Set[str] = set()
                counted_skips: Set[tuple[str, str]] = set()

                def record_skip(reason: str, token: str) -> None:
                    nonlocal skipped
                    key = (reason, token)
                    if key in counted_skips:
                        return
                    counted_skips.add(key)
                    group_stats[reason] += 1
                    all_skip_reasons[reason] += 1
                    skipped += 1

                try:
                    page.goto(
                        group.url,
                        wait_until="domcontentloaded",
                        timeout=v64.PAGE_TIMEOUT_MS,
                    )
                    page.wait_for_timeout(4000)
                except Exception as exc:
                    failed += 1
                    self.log.warning("開啟群組失敗，換下一群：%s", exc)
                    if isinstance(groups, GroupUrlQueue):
                        groups.finalize(group, successful=False)
                        retained_groups += 1
                    self.log.info("本群未成功，網址保留於 group.txt 供下次重試")
                    continue

                group_admin_count = 0
                no_new_rounds = 0
                failed_scrolls = 0
                switch_reason = "已達最大捲動次數"

                for _scroll_index in range(max_scrolls + 1):
                    if self.stop_event.is_set() or completed >= target:
                        break
                    snapshots = v64.collect_comment_box_snapshots(page)
                    round_stats = Counter(snapshots=len(snapshots))
                    for snapshot in snapshots:
                        if snapshot.token not in seen_tokens:
                            seen_tokens.add(snapshot.token)
                            round_stats["new_boxes"] += 1

                    for snapshot in snapshots:
                        if self.stop_event.is_set() or completed >= target:
                            break
                        if snapshot.token in terminal_tokens:
                            round_stats["already_handled"] += 1
                            continue
                        try:
                            box = v64.get_comment_box_by_token(page, snapshot.token)
                            if box is None:
                                record_skip("box_missing", snapshot.token)
                                round_stats["box_missing"] += 1
                                continue
                            author, article = v64.extract_author_from_comment_box(
                                page, box, group
                            )
                            if article is None or author is None:
                                record_skip("author_missing", snapshot.token)
                                round_stats["author_missing"] += 1
                                continue

                            author_key = author.url or author.name.casefold()
                            post_key, post_display = v64.build_post_key(
                                article, author, group.url
                            )
                            if post_key in tested_posts:
                                terminal_tokens.add(snapshot.token)
                                record_skip("duplicate_post", snapshot.token)
                                round_stats["duplicate_post"] += 1
                                continue

                            if v64.article_has_admin_badge(article):
                                tested_posts.add(post_key)
                                terminal_tokens.add(snapshot.token)
                                group_admin_count += 1
                                record_skip("admin", snapshot.token)
                                round_stats["admin"] += 1
                                self.log.info(
                                    "略過 Admin：%s｜貼文=%s｜本群 Admin=%d/4",
                                    author.name,
                                    post_display or "未取得",
                                    group_admin_count,
                                )
                                if group_admin_count > 3:
                                    switch_reason = "Admin 超過 3 個"
                                    break
                                continue

                            duplicate_author = bool(author_key) and (
                                (dedupe_scope == "group" and author_key in group_authors)
                                or (
                                    dedupe_scope == "environment"
                                    and author_key in environment_authors
                                )
                            )
                            if duplicate_author:
                                tested_posts.add(post_key)
                                terminal_tokens.add(snapshot.token)
                                record_skip("duplicate_author", snapshot.token)
                                round_stats["duplicate_author"] += 1
                                continue

                            round_stats["attempted"] += 1
                            group_stats["attempted"] += 1
                            media_path = media_pool.claim()
                            if media_path:
                                self.log.info(
                                    "留言媒體已選擇：%s｜類型=%s",
                                    media_path.name,
                                    media_kind(media_path),
                                )
                            result = v64.test_one_comment_box(
                                page,
                                box,
                                group,
                                random.choice(comments),
                                "正式留言",
                                media_path=media_path,
                            )
                            if result.submitted:
                                tested_posts.add(post_key)
                                terminal_tokens.add(snapshot.token)
                                completed += 1
                                group_count += 1
                                round_stats["success"] += 1
                                group_stats["success"] += 1
                                if media_path:
                                    selected_media_kind = media_kind(media_path)
                                    group_stats[f"media_{selected_media_kind}"] += 1
                                    media_successes[selected_media_kind] += 1
                                if author_key:
                                    group_authors.add(author_key)
                                    environment_authors.add(author_key)
                                self.log.info(
                                    "留言成功：%s｜群組=%d｜環境=%d/%d",
                                    author.name,
                                    group_count,
                                    completed,
                                    target,
                                )
                            else:
                                # An unconfirmed send can still have reached
                                # Facebook.  Do not retry it in the same run or
                                # a duplicate comment may be posted.
                                tested_posts.add(post_key)
                                terminal_tokens.add(snapshot.token)
                                failed += 1
                                round_stats["submit_unconfirmed"] += 1
                                group_stats["submit_unconfirmed"] += 1
                                self.log.warning(
                                    "留言未確認送出：%s｜%s",
                                    author.name,
                                    result.status,
                                )
                        except Exception as exc:
                            failed += 1
                            round_stats["exception"] += 1
                            group_stats["exception"] += 1
                            self.log.warning("單一留言框處理失敗：%s", exc)

                    self.log.info(
                        "留言框掃描：快照=%d｜新增框=%d｜已處理=%d｜嘗試=%d｜"
                        "成功=%d｜找不到作者=%d｜重複貼文=%d｜重複作者=%d｜"
                        "Admin=%d｜框失效=%d｜例外=%d｜群組累積=%d｜環境=%d/%d",
                        round_stats["snapshots"],
                        round_stats["new_boxes"],
                        round_stats["already_handled"],
                        round_stats["attempted"],
                        round_stats["success"],
                        round_stats["author_missing"],
                        round_stats["duplicate_post"],
                        round_stats["duplicate_author"],
                        round_stats["admin"],
                        round_stats["box_missing"],
                        round_stats["exception"],
                        group_count,
                        completed,
                        target,
                    )

                    if group_admin_count > 3:
                        break
                    if completed >= target:
                        switch_reason = "環境留言目標完成"
                        break

                    # "No new" now means no newly discovered comment boxes,
                    # not merely a round where no comment happened to succeed.
                    no_new_rounds = (
                        no_new_rounds + 1
                        if round_stats["new_boxes"] == 0
                        else 0
                    )
                    if no_new_rounds >= v64.AUTHOR_NO_GROWTH_LIMIT:
                        switch_reason = "連續無新留言框達上限"
                        break

                    moved = v64.verified_small_scroll(page, group.name)
                    failed_scrolls = 0 if moved else failed_scrolls + 1
                    if failed_scrolls >= 2:
                        switch_reason = "連續 2 次無法確實滑動"
                        break
                    page.wait_for_timeout(900)

                self.log.info(
                    "換群：%s｜原因=%s｜本群成功=%d｜唯一留言框=%d｜"
                    "嘗試=%d｜找不到作者=%d｜重複貼文=%d｜重複作者=%d｜"
                    "Admin=%d｜相片=%d｜影片=%d｜環境累積=%d/%d",
                    group.url,
                    switch_reason,
                    group_count,
                    len(seen_tokens),
                    group_stats["attempted"],
                    group_stats["author_missing"],
                    group_stats["duplicate_post"],
                    group_stats["duplicate_author"],
                    group_stats["admin"],
                    group_stats["media_photo"],
                    group_stats["media_video"],
                    completed,
                    target,
                )

                if isinstance(groups, GroupUrlQueue):
                    removed = groups.finalize(group, successful=group_count > 0)
                    if removed:
                        removed_groups += 1
                        self.log.info(
                            "本群有確認成功留言，已從 group.txt 移除｜剩餘=%d",
                            groups.retained_count(),
                        )
                    else:
                        retained_groups += 1
                        self.log.info(
                            "本群 0 成功或已停用成功後刪除；網址保留於 group.txt"
                        )
                if group_count == 0:
                    zero_success_groups += 1

            if self.stop_event.is_set():
                status = "STOPPED"
            elif completed >= target:
                status = "SUCCESS"
            elif completed:
                status = "PARTIAL"
            else:
                status = "FAILED"
            issues = []
            if completed < target and not self.stop_event.is_set():
                issues.append(
                    f"group.txt 已處理完，但環境只完成 {completed}/{target} 筆留言"
                )
            skip_summary = "、".join(
                f"{name}={count}" for name, count in sorted(all_skip_reasons.items())
            ) or "無"
            self.log.info(
                "環境群組留言完成：狀態=%s｜成功=%d/%d｜開啟群組=%d｜"
                "0成功群組=%d｜相片=%d｜影片=%d｜移除網址=%d｜保留網址=%d｜"
                "跳過原因=%s｜失敗=%d",
                status,
                completed,
                target,
                opened_groups,
                zero_success_groups,
                media_successes["photo"],
                media_successes["video"],
                removed_groups,
                retained_groups,
                skip_summary,
                failed,
            )
            return TaskResult(
                status,
                found=completed + skipped + failed,
                reported=completed,
                skipped=skipped,
                failed=failed,
                issues=issues,
            )
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    playwright.stop()
            except Exception:
                pass
