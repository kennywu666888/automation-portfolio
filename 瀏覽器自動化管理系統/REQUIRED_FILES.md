# REQUIRED_FILES

## A. 程式執行必要
main.py, gui.py, config.py, logger.py, adspower.py, browser.py, facebook.py, behavior.py, task_result.py, task_diagnostics.py, utils.py, requirements.txt

## B. 十二項功能模組
professional_mode.py, avatar_pin.py, reels.py, reels_comment.py, fanpage_message_task.py, chat_query_task.py, chat_reply_task.py, chat_repository.py, messenger_selectors.py, text_library.py, telegram_reporter.py

## C. 功能啟用時需要的資料
- PO 文：可選 RC19 格式隨機多行 TXT；未選或無可用內容時使用文案.xlsx。若勾選媒體，另需隨機相片／影片資料夾或固定相片／影片檔案
- 粉專私訊：kolurl.txt、文二.txt
- 聊天室回覆：文一.txt
- 發布 Reels：影片資料夾、RC19 格式隨機多行描述 TXT、reels_settings.json
- Reels 留言自選模式：reels_comment.txt 或使用者選擇的 TXT

## D. 執行後可自動產生
gui_settings.json, chat_tasks.db, logs/, diagnostics/, reels_history.json, schedules.json

## E. 開發／測試
test_core.py, test_12_tasks_audit.py, AUDIT_12_TASKS_REPORT.md

## F. 不需要／未納入正式 ZIP
__pycache__/, *.pyc, 舊 logs/, 舊 diagnostics/, 測試截圖與 HTML, 舊版 release notes, 重複專案資料夾

## G. 敏感設定
正式 ZIP 不包含真實 .env；請由 .env.example 複製建立。
