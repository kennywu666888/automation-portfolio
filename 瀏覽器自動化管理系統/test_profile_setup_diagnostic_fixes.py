from pathlib import Path

from 個人資料設定 import (
    _facebook_document_language_matches,
    split_facebook_name_fields,
)


assert split_facebook_name_fields("Mavis Lewis") == ("Mavis", "", "Lewis")
assert split_facebook_name_fields("Melissa Rose Robles") == ("Melissa", "Rose", "Robles")
assert split_facebook_name_fields("Ana Maria De Cruz") == ("Ana", "Maria De", "Cruz")
assert _facebook_document_language_matches("Filipino", "fil")

profile_source = (Path(__file__).parent / "profile_setup.py").read_text(encoding="utf-8")
avatar_source = (Path(__file__).parent / "avatar_pin.py").read_text(encoding="utf-8")

# Banner 還原為已實測正常的直接 file-input 流程：不能先點 Add/Edit
# cover，否則這批 Facebook 版面會被導入一般 Create Post 視窗。
assert "candidates[0] if candidates else None" in profile_source
assert "chosen.send_keys(str(image_path))" in profile_source
assert "已直接寫入 Banner 圖片欄位" in profile_source
assert "cover_post_commit" not in profile_source
assert "新版 Banner 流程已點擊" not in profile_source

# 名字必須先驗證三欄實際值，再辨識 Meta 的帳號限制提示。
assert "姓名欄位未成功寫入" in profile_source
assert "Facebook 拒絕改名" in profile_source
assert "hindi mo mapapalitan ang iyong pangalan sa ngayon" in profile_source

# 語言切換以 HTML lang 為最終依據，且 row click 後要確認對話框真的出現。
assert "language_dialogs" in profile_source
assert profile_source.count("_facebook_document_language_matches(target, current_document_language())") >= 4

# 頭像 Save 改為對話框 + 最上層命中，不再依賴右下角座標。
assert "avatar_dialog_markers" in avatar_source
assert "window.innerWidth * 0.45" not in avatar_source
assert "window.innerHeight * 0.50" not in avatar_source

print("profile setup diagnostic regression tests passed")
