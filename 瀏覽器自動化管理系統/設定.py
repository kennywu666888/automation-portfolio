"""
config.py
=========
Facebook Auto Warm-up Lite — 全域設定模組
所有可調整的參數集中於此，方便維護與修改。
"""

import os
from dataclasses import dataclass, field
from typing import List


# ─────────────────────────────────────────────
# AdsPower 設定
# ─────────────────────────────────────────────
@dataclass
class AdsPowerConfig:
    """AdsPower Local API 連線設定"""

    # AdsPower 本機 API 基底網址（預設 port 50325）
    base_url: str = "http://local.adspower.net:50325"

    # AdsPower API Key（刪除環境時固定使用）
    api_key: str = os.environ.get("ADSPOWER_API_KEY", "")

    # 目標 AdsPower 群組名稱與 ID
    target_group: str = os.environ.get("ADSPOWER_TARGET_GROUP", "養號專用")
    target_group_id: str = os.environ.get("ADSPOWER_TARGET_GROUP_ID", "9777935")

    # AdsPower 翻頁讀取間隔，避免 Too many request per second
    list_page_wait: float = 3.0

    # 開啟 Browser 後等待 Selenium 就緒的秒數
    # API 回傳 Selenium 位址後只保留短緩衝；後續由 Selenium 實際連線確認。
    browser_launch_wait: float = 0.8

    # API 請求逾時秒數
    request_timeout: int = 30

    # 開啟 / 關閉 Browser 最多重試次數
    launch_retries: int = 2

    # 每次重試前等待秒數
    retry_wait: float = 5.0


# ─────────────────────────────────────────────
# Selenium / 瀏覽器設定
# ─────────────────────────────────────────────
@dataclass
class BrowserConfig:
    """Selenium WebDriver 相關設定"""

    # 等待元素出現的預設逾時秒數
    # Facebook DOM 很大；全域隱式等待會讓每一條備援定位各自卡住。
    # 所有需要等待的流程均使用短週期顯式掃描，因此只保留極短容錯。
    implicit_wait: float = 0.3

    # WebDriverWait 最長等待秒數
    explicit_wait: int = 8

    # 頁面載入逾時秒數
    page_load_timeout: int = 15

    # 建立 Selenium 接管連線的 HTTP 逾時。Selenium 預設為 120 秒，
    # AdsPower 偵錯端口異常時會讓單一環境看似整個程式卡死。
    connect_timeout: int = 20

    # 操作失敗最多重試次數
    action_retries: int = 2

    # 重試前等待秒數
    action_retry_wait: float = 3.0


# ─────────────────────────────────────────────
# Facebook 瀏覽行為設定
# ─────────────────────────────────────────────
@dataclass
class BrowseConfig:
    """瀏覽動態牆行為設定"""

    # 瀏覽總時間範圍（秒），隨機取值
    browse_duration_min: int = 60    # 1 分鐘
    browse_duration_max: int = 180   # 3 分鐘

    # 從首頁頂端開始計算的最大滑動位置；到達後立即結束瀏覽
    max_scroll_position: int = 3000

    # 每次滑動的像素範圍（隨機取值）
    scroll_distance_min: int = 200
    scroll_distance_max: int = 600

    # 滑動後停留閱讀時間（秒）
    read_pause_min: float = 1.5
    read_pause_max: float = 4.5

    # 滑動速度（每像素耗時 ms，值越大越慢）
    scroll_speed_min: int = 1
    scroll_speed_max: int = 4

    # 點開留言區的機率（0.0 ~ 1.0）
    open_comments_prob: float = 0.25

    # 點開圖片的機率
    open_photo_prob: float = 0.20

    # 點開留言或圖片後停留秒數
    detail_stay_min: float = 2.0
    detail_stay_max: float = 6.0

    # 滑鼠移動時的隨機偏移像素範圍
    mouse_jitter_min: int = -8
    mouse_jitter_max: int = 8


# ─────────────────────────────────────────────
# 互動行為設定
# ─────────────────────────────────────────────
@dataclass
class InteractionConfig:
    """按讚 / 留言 / 分享行為設定"""

    # 對每篇貼文按讚的機率
    like_prob: float = 0.30

    # 對每篇貼文留言的機率（只對有留言的貼文）
    comment_prob: float = 0.15

    # 對每篇貼文分享的機率
    share_prob: float = 0.08

    # 留言送出後停留秒數
    after_comment_pause_min: float = 1.5
    after_comment_pause_max: float = 3.5

    # 按讚後停留秒數
    after_like_pause_min: float = 0.5
    after_like_pause_max: float = 2.0

    # 分享後停留秒數
    after_share_pause_min: float = 2.0
    after_share_pause_max: float = 5.0


# ─────────────────────────────────────────────
# 加好友設定
# ─────────────────────────────────────────────
@dataclass
class FriendConfig:
    """搜尋與加好友設定"""

    # 固定加好友人數（不得超過此值）
    add_friend_count: int = 1

    # 每次最多確認幾個收到的好友邀請
    confirm_friend_count: int = 2

    # 搜尋用菲律賓文彩票關鍵字列表（隨機挑選一個）
    search_keywords: List[str] = field(default_factory=lambda: [
        "swertres lotto Pilipinas",
        "STL lotto result ngayon",
        "PCSO lotto winner",
        "EZ2 lotto result",
        "6/45 lotto Pilipinas",
        "lotto jackpot Pilipinas 2026",
        "PCSO result ngayon",
    ])

    # 搜尋結果頁載入後等待秒數
    search_wait_min: float = 2.0
    search_wait_max: float = 4.0

    # 送出好友邀請後停留秒數
    after_add_pause_min: float = 3.0
    after_add_pause_max: float = 7.0

    # 確認好友邀請後停留秒數
    after_confirm_pause_min: float = 2.0
    after_confirm_pause_max: float = 5.0

    # 整個確認好友流程完成後等待秒數
    after_confirm_finish_min: float = 3.0
    after_confirm_finish_max: float = 8.0


# ─────────────────────────────────────────────
# OpenAI 設定
# ─────────────────────────────────────────────
@dataclass
class OpenAIConfig:
    """OpenAI API 設定"""

    # API Key（建議從環境變數注入，此處留空由外部設定）
    api_key: str = os.environ.get("OPENAI_API_KEY", "")

    # 使用的模型
    model: str = "gpt-4o-mini"

    # 呼叫逾時秒數
    request_timeout: int = 30

    # 產生留言的 max_tokens
    max_tokens: int = 80

    # temperature（越高越有創意）
    temperature: float = 0.85

    # 系統提示（指引留言風格）
    system_prompt: str = (
        "You are a friendly Facebook user. "
        "Read the post content and write ONE short, natural comment in the SAME language as the post. "
        "Keep it under 20 words. Do not use hashtags. Do not mention you are an AI."
    )


# ─────────────────────────────────────────────
# Logger 設定
# ─────────────────────────────────────────────
@dataclass
class LoggerConfig:
    """日誌記錄設定"""

    # 日誌檔案存放目錄
    log_dir: str = "logs"

    # 日誌檔案名稱前綴
    log_prefix: str = "warmup"

    # 是否同時輸出到 Console
    console_output: bool = True

    # 日誌等級（DEBUG / INFO / WARNING / ERROR）
    log_level: str = "INFO"

    # 單一日誌檔最大 MB（超過則 rotate）
    max_mb: int = 10

    # 保留最多幾個 rotate 備份
    backup_count: int = 5


# ─────────────────────────────────────────────
# 主設定組合（統一入口）
# ─────────────────────────────────────────────
@dataclass
class AppConfig:
    """
    應用程式總設定
    所有子設定透過此類別存取，避免散落各處。
    """
    adspower: AdsPowerConfig = field(default_factory=AdsPowerConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    browse: BrowseConfig = field(default_factory=BrowseConfig)
    interaction: InteractionConfig = field(default_factory=InteractionConfig)
    friend: FriendConfig = field(default_factory=FriendConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    logger: LoggerConfig = field(default_factory=LoggerConfig)

    # 每個 Profile 完成後等待秒數（降低帳號操作節奏的相似性）
    profile_gap_min: float = 5.0
    profile_gap_max: float = 15.0

    # 是否啟用加好友功能（主動送出邀請）
    enable_add_friend: bool = True

    # 是否啟用確認好友功能（接受收到的邀請）
    enable_confirm_friend: bool = True


# ─────────────────────────────────────────────
# 模組層級單例（直接 import 使用）
# ─────────────────────────────────────────────
# 使用方式：
#   from config import CONFIG
#   CONFIG.openai.api_keyYOUR_SECRET
CONFIG = AppConfig()
