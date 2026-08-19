import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from 社團留言任務 import GroupUrlQueue


with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "group.txt"
    urls = [f"https://www.facebook.com/groups/test{i}" for i in range(100)]
    path.write_text("\n".join(urls) + "\n", encoding="utf-8-sig")
    queue = GroupUrlQueue(path, delete_after_claim=True)
    claimed = []
    claimed_lock = threading.Lock()

    def consume():
        local = []
        while True:
            group = queue.claim()
            if group is None:
                break
            local.append(group.url)
            assert queue.finalize(group, successful=True)
        with claimed_lock:
            claimed.extend(local)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(consume) for _ in range(16)]
        for future in futures:
            future.result()

    assert len(claimed) == 100
    assert len(set(claimed)) == 100
    assert path.read_text(encoding="utf-8-sig") == ""
    assert path.with_name("group.txt.bak").exists()

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "group.txt"
    path.write_text(
        "https://www.facebook.com/groups/current\n",
        encoding="utf-8-sig",
    )
    backup = path.with_name("group.txt.bak")
    backup.write_text("stale backup\n", encoding="utf-8-sig")
    queue = GroupUrlQueue(path, delete_after_claim=True)
    group = queue.claim()
    assert queue.finalize(group, successful=True)
    assert "current" in backup.read_text(encoding="utf-8-sig")
    assert "stale backup" not in backup.read_text(encoding="utf-8-sig")

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "group.txt"
    path.write_text(
        "https://www.facebook.com/groups/success\n"
        "https://www.facebook.com/groups/failed\n"
        "https://www.facebook.com/groups/prefetched\n",
        encoding="utf-8-sig",
    )
    queue = GroupUrlQueue(path, delete_after_claim=True)
    success = queue.claim()
    failed = queue.claim()
    prefetched = queue.claim()
    assert queue.finalize(success, successful=True)
    assert not queue.finalize(failed, successful=False)
    # A claimed but unopened/unfinalized URL must also stay in the source.
    retained = path.read_text(encoding="utf-8-sig")
    assert "success" not in retained
    assert "failed" in retained
    assert "prefetched" in retained
    assert queue.retained_count() == 2

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "group.txt"
    path.write_text(
        "https://www.facebook.com/groups/keep1\n"
        "https://www.facebook.com/groups/keep2\n",
        encoding="utf-8-sig",
    )
    queue = GroupUrlQueue(path, delete_after_claim=False)
    first = queue.claim()
    second = queue.claim()
    assert first.url != second.url
    assert not queue.finalize(first, successful=True)
    assert not queue.finalize(second, successful=True)
    assert "keep1" in path.read_text(encoding="utf-8-sig")
    assert not path.with_name("group.txt.bak").exists()

print("Group URL queue success-only commit tests passed")
