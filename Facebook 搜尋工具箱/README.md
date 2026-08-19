# Facebook 搜尋工具箱

這是整理後的 GitHub 公開展示版本，用於展示 Python 自動化、GUI、API 串接與資料處理能力。

## 主要能力

- Python 桌面 GUI
- 瀏覽器工作流程自動化
- API 串接與錯誤重試
- 多執行緒／任務排程
- 資料讀取、整理與匯出
- 日誌與錯誤處理

## 技術

Python 3、Tkinter、Requests、Playwright

## 安全處理

公開版本已移除或排除：
- API Key、Token、Cookie、密碼與登入憑證
- 真實帳號、Email、電話與個人資料
- 執行紀錄、診斷檔、資料庫與快取
- `.env`、本機設定、虛擬環境與編譯快取

需要憑證的功能請自行建立本機設定，請勿將真實憑證提交到 GitHub。

## 安裝

```bash
pip install -r requirements.txt
```

若專案沒有 `requirements.txt`，請依程式實際使用的套件安裝。

## 使用方式

請先閱讀程式介面中的設定說明，填入自己的合法 API／本機環境資訊後執行主程式。

## 注意

本專案僅供自動化技術展示與合法用途。使用者應遵守相關網站、平台與 API 的服務條款及適用法律。
