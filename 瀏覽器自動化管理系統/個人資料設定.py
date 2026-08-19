"""Facebook 個人資料設定：Banner、名字與介面語言。"""
from __future__ import annotations

import re
import time
from pathlib import Path

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from 日誌 import get_logger

_log = get_logger(__name__)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def find_matching_banner(profile_name: str, banner_dir: str) -> Path | None:
    match = re.search(r"(\d+)\s*$", profile_name.strip())
    if not match:
        return None
    raw = match.group(1)
    number = int(raw)
    folder = Path(banner_dir).expanduser()
    if not folder.is_dir():
        _log.warning("[%s] Banner 資料夾不存在：%s", profile_name, folder)
        return None
    stems = list(dict.fromkeys((raw, str(number), f"{number:02d}", f"{number:03d}")))
    for stem in stems:
        for ext in IMAGE_EXTENSIONS:
            for candidate in (folder / f"{stem}{ext}", folder / f"{stem}{ext.upper()}"):
                if candidate.is_file():
                    return candidate.resolve()
    return None


def _visible(element) -> bool:
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def change_facebook_banner(driver, profile_name: str, image_path: Path, timeout: int = 45) -> bool:
    """以個人主頁封面區的 input[type=file] 直接上傳 Banner。"""
    image_path = Path(image_path).resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"Banner 圖片不存在：{image_path}")

    # 診斷 HTML 確認封面按鈕附近直接存在 accept=image 的 file input。
    candidates = driver.find_elements(By.CSS_SELECTOR, "input[type='file'][accept*='image']")
    chosen = None
    for element in candidates:
        try:
            is_cover = driver.execute_script(
                """
                const input = arguments[0];
                let node = input.parentElement;
                for (let i=0; node && i<10; i++, node=node.parentElement) {
                    const text = ((node.innerText || '') + ' ' +
                        Array.from(node.querySelectorAll('[aria-label]')).map(x=>x.getAttribute('aria-label')).join(' '))
                        .toLowerCase();
                    if (text.includes('cover photo') || text.includes('larawan sa cover') ||
                        text.includes('photo de couverture') || text.includes('封面相片') ||
                        text.includes('封面照片')) return true;
                }
                return false;
                """,
                element,
            )
            if is_cover:
                chosen = element
                break
        except StaleElementReferenceException:
            continue
    if chosen is None:
        # 沒有現有 Banner 時，封面 input 通常是頁面最上方第一個 image input。
        chosen = candidates[0] if candidates else None
    if chosen is None:
        raise RuntimeError("個人主頁找不到 Banner 對應的圖片上傳欄位")

    chosen.send_keys(str(image_path))
    _log.info("[%s] 已直接寫入 Banner 圖片欄位：%s", profile_name, image_path)

    end = time.monotonic() + timeout
    save_labels = {
        "save changes", "save", "i-save ang mga pagbabago", "i-save",
        "儲存變更", "保存更改", "enregistrer les modifications", "保存",
    }
    while time.monotonic() < end:
        buttons = driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
        for button in buttons:
            try:
                text = " ".join((button.text or button.get_attribute("aria-label") or "").split()).casefold()
                if text in save_labels and _visible(button) and button.is_enabled() and str(button.get_attribute('aria-disabled')).lower() != 'true':
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
                    button.click()
                    _log.info("[%s] 已點擊 Banner 儲存按鈕。", profile_name)
                    close_end = time.monotonic() + 20
                    while time.monotonic() < close_end:
                        if not _visible(button):
                            _log.info("[%s] Facebook Banner 更換成功。", profile_name)
                            return True
                        time.sleep(0.3)
                    return True
            except StaleElementReferenceException:
                continue
        time.sleep(0.3)
    raise RuntimeError("Banner 圖片已上傳，但找不到可用的儲存按鈕")


def set_facebook_language_filipino(driver, profile_name: str, timeout: int = 45) -> bool:
    """透過 Facebook Language 設定頁將介面語言改為 Filipino。"""
    try:
        driver.get("https://www.facebook.com/settings/?tab=language")
    except (TimeoutException, WebDriverException) as exc:
        # Facebook renderer 偶爾逾時，但設定頁 DOM 已可用；停止繼續載入後
        # 直接依實際頁面判斷，避免把 renderer timeout 當成語言失敗。
        _log.warning("[%s] 語言設定頁載入較慢，停止等待後檢查 DOM：%s", profile_name, exc)
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    end = time.monotonic() + timeout

    def body_text() -> str:
        try:
            return str(driver.execute_script("return document.body ? document.body.innerText : ''") or "")
        except Exception:
            return ""

    # 若目前已是 Filipino，直接完成。
    while time.monotonic() < end:
        text = body_text().casefold()
        if "facebook language" in text or "wika ng facebook" in text:
            if re.search(r"(?:facebook language|wika ng facebook)[\s\S]{0,160}\bfilipino\b", text):
                _log.info("[%s] Facebook 語言已是 Filipino，略過修改。", profile_name)
                return True
            break
        time.sleep(0.3)

    # 點擊 Facebook Language 設定列（整列是 role=button）。
    clicked = driver.execute_script(
        """
        const labels = ['facebook language','wika ng facebook'];
        const nodes = [...document.querySelectorAll('span,div')];
        const target = nodes.find(n => labels.includes((n.innerText || '').trim().toLowerCase()));
        if (!target) return false;
        const row = target.closest('[role="button"]') || target.parentElement;
        if (!row) return false;
        row.click(); return true;
        """
    )
    if not clicked:
        raise RuntimeError("Language and region 頁面找不到 Facebook Language 設定列")

    # 在開啟的選擇視窗中選 Filipino。
    select_end = time.monotonic() + 20
    selected = False
    while time.monotonic() < select_end:
        selected = bool(driver.execute_script(
            """
            const visible = e => { const r=e.getBoundingClientRect(); const s=getComputedStyle(e); return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'; };
            const nodes=[...document.querySelectorAll('[role="radio"], [role="option"], label, span, div')]
              .filter(visible).filter(e => (e.innerText || '').trim().toLowerCase()==='filipino');
            for (const n of nodes) {
                const row=n.closest('[role="radio"],label,[role="option"],[role="button"]') || n;
                row.click(); return true;
            }
            return false;
            """
        ))
        if selected:
            break
        time.sleep(0.25)
    if not selected:
        raise RuntimeError("Facebook Language 選單找不到 Filipino")

    # 點 Save changes／Save；某些版型選取後會自動套用，故也接受頁面已切換。
    save_end = time.monotonic() + 20
    while time.monotonic() < save_end:
        text = body_text().casefold()
        if "wika ng facebook" in text and "filipino" in text:
            _log.info("[%s] Facebook 介面語言已切換為 Filipino。", profile_name)
            return True
        clicked_save = driver.execute_script(
            """
            const wanted=new Set(['save changes','save','i-save ang mga pagbabago','i-save','儲存變更','保存更改']);
            const nodes=[...document.querySelectorAll('button,[role="button"]')];
            const b=nodes.find(e=>wanted.has(((e.innerText||e.getAttribute('aria-label')||'').trim().toLowerCase())) && e.getAttribute('aria-disabled')!=='true');
            if (!b) return false; b.click(); return true;
            """
        )
        if clicked_save:
            time.sleep(1.5)
        else:
            time.sleep(0.3)
    # 最後以頁面內容驗證。
    final_text = body_text().casefold()
    if "filipino" in final_text:
        _log.info("[%s] Facebook 語言設定已選擇 Filipino。", profile_name)
        return True
    raise RuntimeError("已選擇 Filipino，但無法確認語言設定完成")


LANGUAGE_OPTIONS = {
    "Filipino": ("Filipino",),
    "English (US)": ("English (US)", "English"),
    "العربية": ("العربية", "Arabic"),
    "繁體中文": ("中文(台灣)", "繁體中文", "Chinese (Traditional)"),
    "简体中文": ("中文(简体)", "简体中文", "Chinese (Simplified)"),
    "Français": ("Français (France)", "Français"),
    "Deutsch": ("Deutsch",),
    "Español": ("Español", "Español (España)"),
}

LANGUAGE_HTML_CODES = {
    "Filipino": ("fil", "tl"),
    "English (US)": ("en",),
    "العربية": ("ar",),
    "繁體中文": ("zh-hant", "zh-tw"),
    "简体中文": ("zh-hans", "zh-cn"),
    "Français": ("fr",),
    "Deutsch": ("de",),
    "Español": ("es",),
}


def _facebook_document_language_matches(target: str, document_language: str) -> bool:
    """以 Facebook HTML lang 驗證介面語言是否真的完成切換。"""
    actual = str(document_language or "").strip().casefold().replace("_", "-")
    if not actual:
        return False
    return any(
        actual == code or actual.startswith(code + "-")
        for code in LANGUAGE_HTML_CODES.get(target, ())
    )


def read_profile_name(text_file: str, profile_name: str) -> str:
    match = re.search(r"(\d+)\s*$", profile_name.strip())
    if not match:
        raise ValueError("環境名稱尾端沒有數字，無法對應名字 TXT")
    line_no = int(match.group(1))
    path = Path(text_file).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"名字 TXT 檔案不存在：{path}")
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            lines = path.read_text(encoding=encoding).splitlines()
            if line_no > len(lines):
                raise IndexError(f"名字 TXT 只有 {len(lines)} 行，沒有第 {line_no} 行")
            value = lines[line_no - 1].strip()
            if not value:
                raise ValueError(f"名字 TXT 第 {line_no} 行是空白")
            return value
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"無法辨識名字 TXT 編碼：{last_error}")


def facebook_profile_id_from_url(profile_url: str) -> str:
    """從本人 Facebook 個人主頁／Accounts Center 網址提取數字 ID。"""
    value = str(profile_url or "").strip()
    if value.isdigit():
        return value
    match = re.search(
        r"(?:[?&]id=|accountscenter\.facebook\.com/profiles/)(\d+)(?:\D|$)",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"無法從本人個人主頁網址取得 Facebook 數字 ID：{value or '空白'}")
    return match.group(1)


def build_accounts_center_name_url(profile_url_or_id: str) -> str:
    """以本人 Facebook 數字 ID 組成新版 Accounts Center 姓名網址。"""
    profile_id = facebook_profile_id_from_url(profile_url_or_id)
    return (
        f"https://accountscenter.facebook.com/profiles/{profile_id}/name/"
        "?entrypoint=account_overview"
    )


def split_facebook_name_fields(new_name: str) -> tuple[str, str, str]:
    """將一行姓名依 Accounts Center 的前名／中間名／姓氏三欄拆分。"""
    parts = str(new_name or "").split()
    if not parts:
        raise ValueError("姓名不可為空")
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def change_facebook_name(
    driver,
    profile_name: str,
    new_name: str,
    timeout: int = 45,
    personal_profile_url: str = "",
) -> bool:
    """在新版 Meta Accounts Center 修改姓名並逐步驗證 React 畫面狀態。"""
    first_name, middle_name, last_name = split_facebook_name_fields(new_name)

    profile_sources = [
        personal_profile_url,
        getattr(driver, "_facebook_personal_profile_url", ""),
    ]
    try:
        profile_sources.append(driver.current_url or "")
    except Exception:
        pass

    name_url = ""
    for source in profile_sources:
        try:
            name_url = build_accounts_center_name_url(source)
            break
        except ValueError:
            continue
    if not name_url:
        raise RuntimeError("改名字前無法取得本人的 Facebook 數字 ID，已停止以避免操作錯帳號")

    _log.info("[%s] 使用本人 Facebook ID 開啟 Accounts Center 姓名頁：%s", profile_name, name_url)
    def norm(value: str) -> str:
        return " ".join((value or "").split()).casefold()

    def click_robust(element) -> bool:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(0.15)
            element.click()
            return True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                return False

    def replace_value(field, value: str) -> bool:
        """使用原生 value setter 觸發 React input/change，並驗證新值。"""
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
            field.click()
            field.send_keys("\ue009", "a")  # Ctrl+A
            field.send_keys("\ue003")       # Backspace
            field.send_keys(value)
        except Exception:
            pass

        try:
            current = field.get_attribute("value") or ""
        except Exception:
            current = ""
        if current != value:
            try:
                driver.execute_script(
                    """
                    const el=arguments[0], val=arguments[1];
                    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                    setter.call(el,val);
                    try {
                        el.dispatchEvent(new InputEvent('input',{
                            bubbles:true,inputType:'insertText',data:val
                        }));
                    } catch (_) {
                        el.dispatchEvent(new Event('input',{bubbles:true}));
                    }
                    el.dispatchEvent(new Event('change',{bubbles:true}));
                    """, field, value,
                )
            except Exception:
                return False
        try:
            field.send_keys("\ue004")  # Tab，讓 Meta 驗證欄位。
        except Exception:
            pass
        time.sleep(0.2)
        try:
            return (field.get_attribute("value") or "") == value
        except Exception:
            return False

    def name_fields():
        fields = []
        # 姓名視窗固定依序為前名、中間名、姓氏；僅取可見文字欄位。
        for field in driver.find_elements(By.CSS_SELECTOR, "[role='dialog'] input, main input"):
            try:
                if not _visible(field) or not field.is_enabled():
                    continue
                field_type = norm(field.get_attribute("type") or "text")
                hint = norm(" ".join(filter(None, (
                    field.get_attribute("aria-label"),
                    field.get_attribute("placeholder"),
                    field.get_attribute("name"),
                ))))
                if field_type in ("search", "radio", "hidden"):
                    continue
                if any(x in hint for x in ("search", "maghanap", "搜尋", "搜索")):
                    continue
                fields.append(field)
            except StaleElementReferenceException:
                continue
        return fields

    def field_values() -> list[str]:
        result = []
        for field in name_fields():
            try:
                result.append(field.get_attribute("value") or "")
            except StaleElementReferenceException:
                return []
        return result

    # Accounts Center 有時先完成 SSR 才掛上可操作 input；保留完整 timeout，
    # 並在第一次逾時後重開同一個精準姓名網址一次。
    fields = []
    for navigation_attempt in range(2):
        try:
            driver.get(name_url)
        except (TimeoutException, WebDriverException):
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            fields = name_fields()
            if len(fields) >= 2:
                break
            time.sleep(0.3)
        if len(fields) >= 2:
            break
        if navigation_attempt == 0:
            _log.warning("[%s] 姓名欄位尚未掛載，重新開啟精準姓名網址一次。", profile_name)
    if len(fields) < 2:
        raise RuntimeError("Facebook 名字設定頁已載入，但找不到可操作的姓名欄位")

    expected = (
        [first_name, middle_name, last_name]
        if len(fields) >= 3 else [first_name, last_name]
    )
    for attempt in range(3):
        for index, value in enumerate(expected):
            current_fields = name_fields()
            if len(current_fields) < len(expected):
                break
            replace_value(current_fields[index], value)
            time.sleep(0.15)
        time.sleep(0.7)
        actual = field_values()[:len(expected)]
        if actual == expected:
            break
        _log.warning(
            "[%s] 第 %d 次姓名輸入驗證未通過：預期=%s，實際=%s",
            profile_name, attempt + 1, expected, actual,
        )
    else:
        raise RuntimeError(
            f"姓名欄位未成功寫入；預期={expected}，實際={field_values()[:len(expected)]}"
        )

    _log.info(
        "[%s] 已填入並驗證姓名欄位：前名=%s，中間名=%s，姓氏=%s",
        profile_name, first_name, middle_name or "（空白）", last_name or "（空白）",
    )

    review_labels = (
        "review change", "review changes", "review the change",
        "suriin ang pagbabago", "suriin ang mga pagbabago",
        "檢查變更", "查看更改", "檢視變更", "审查更改",
        "examiner la modification", "examiner les modifications",
        "revisar cambio", "revisar cambios",
    )
    preview_markers = (
        "preview your new name", "name display order",
        "i-preview ang bago mong pangalan",
        "預覽你的新名字", "预览你的新名字",
        "prévisualiser votre nouveau nom", "معاينة اسمك الجديد",
    )
    restriction_markers = (
        "you can't change your name right now", "you cannot change your name right now",
        "hindi mo mapapalitan ang iyong pangalan sa ngayon",
        "目前無法變更你的名字", "目前无法更改你的名字",
        "vous ne pouvez pas changer votre nom pour le moment",
        "لا يمكنك تغيير اسمك الآن", "คุณไม่สามารถเปลี่ยนชื่อได้ในขณะนี้",
    )

    def visible_dialog_texts() -> list[str]:
        texts = []
        for dialog in driver.find_elements(By.CSS_SELECTOR, "[role='dialog']"):
            try:
                if _visible(dialog):
                    texts.append(norm(dialog.text))
            except StaleElementReferenceException:
                continue
        return texts

    def preview_visible() -> bool:
        return any(
            any(marker in text for marker in preview_markers)
            for text in visible_dialog_texts()
        )

    # 只有實際看到預覽或 radio 才進入最後步驟；按鈕 click 回傳成功不代表
    # React 已換頁。若仍停在編輯器，最多重新取得按鈕再試三次。
    for review_attempt in range(3):
        review_button = None
        review_end = time.monotonic() + 12
        while time.monotonic() < review_end and review_button is None:
            for button in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
                try:
                    if not _visible(button) or not button.is_enabled():
                        continue
                    text = norm(button.text or button.get_attribute("aria-label") or "")
                    if any(text == label or text.startswith(label) for label in review_labels):
                        if str(button.get_attribute("aria-disabled")).lower() != "true":
                            review_button = button
                            break
                except StaleElementReferenceException:
                    continue
            if review_button is None:
                time.sleep(0.3)
        if review_button is None:
            raise RuntimeError("姓名已填入並驗證，但找不到可用的『檢查變更』按鈕")
        click_robust(review_button)
        preview_end = time.monotonic() + 8
        while time.monotonic() < preview_end:
            if preview_visible():
                break
            time.sleep(0.3)
        if preview_visible():
            break
        _log.warning(
            "[%s] 第 %d 次點擊『檢查變更』後尚未進入預覽，重新取得按鈕再試。",
            profile_name, review_attempt + 1,
        )
    else:
        raise RuntimeError("姓名欄位正確，但 Facebook 按下『檢查變更』後未進入姓名預覽頁")

    # 預覽頁會顯示多個姓名排列。保留已選項；沒有預選時選第一項。
    radios = []
    radio_end = time.monotonic() + 8
    while time.monotonic() < radio_end and not radios:
        for radio in driver.find_elements(By.CSS_SELECTOR, "[role='radio'], input[type='radio']"):
            try:
                if _visible(radio) and radio.is_enabled():
                    radios.append(radio)
            except Exception:
                continue
        if not radios:
            time.sleep(0.25)
    has_selected = False
    for radio in radios:
        try:
            checked = str(radio.get_attribute("aria-checked") or radio.get_attribute("checked") or "").lower()
            if checked in ("true", "checked") or radio.is_selected():
                has_selected = True
                break
        except Exception:
            continue
    if radios and not has_selected:
        click_robust(radios[0])
        time.sleep(0.3)

    confirm_labels = (
        "save changes", "save", "confirm", "done",
        "i-save ang mga pagbabago", "i-save", "kumpirmahin", "tapos na",
        "儲存變更", "儲存", "確認", "完成", "保存更改", "保存", "确认",
        "enregistrer les modifications", "enregistrer", "confirmer",
        "guardar cambios", "guardar", "confirmar",
        "حفظ التغييرات", "حفظ", "تأكيد", "تم",
    )
    final_button = None
    confirm_end = time.monotonic() + 20
    while time.monotonic() < confirm_end and final_button is None:
        for button in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
            try:
                if not _visible(button) or not button.is_enabled():
                    continue
                text = norm(button.text or button.get_attribute("aria-label") or "")
                matched = any(text == label or text.startswith(label + " ") for label in confirm_labels)
                if matched and str(button.get_attribute("aria-disabled")).lower() != "true":
                    # 按鈕必須位於姓名預覽 dialog，不能誤點頁面其他 Save。
                    in_preview = bool(driver.execute_script(
                        """
                        let e=arguments[0];
                        while(e && e!==document.body){
                            if(e.getAttribute?.('role')==='dialog'){
                                const t=(e.innerText||'').toLowerCase();
                                return arguments[1].some(x=>t.includes(x));
                            }
                            e=e.parentElement;
                        }
                        return false;
                        """, button, list(preview_markers),
                    ))
                    if in_preview:
                        final_button = button
                        break
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        if final_button is None:
            time.sleep(0.3)
    if final_button is None or not click_robust(final_button):
        raise RuntimeError("已進入姓名預覽頁，但找不到最後的『完成／Tapos na』按鈕")
    _log.info("[%s] 已點擊姓名預覽頁最後確認按鈕。", profile_name)

    finish_end = time.monotonic() + 22
    while time.monotonic() < finish_end:
        dialog_texts = visible_dialog_texts()
        restricted = next(
            (text for text in dialog_texts if any(marker in text for marker in restriction_markers)),
            "",
        )
        if restricted:
            raise RuntimeError(
                "Facebook 拒絕改名：帳號目前不能變更名字，Accounts Center 要求先解決帳號問題"
            )
        if not any(any(marker in text for marker in preview_markers) for text in dialog_texts):
            try:
                current_url = str(driver.current_url or "").casefold()
                body_text = norm(driver.execute_script(
                    "return document.body ? document.body.innerText : '';"
                ) or "")
            except Exception:
                current_url, body_text = "", ""
            if norm(new_name) in body_text or "accountscenter.facebook.com/profiles" in current_url:
                _log.info("[%s] 已完成 Facebook 名字更新：%s", profile_name, new_name)
                return True
        time.sleep(0.4)

    raise RuntimeError("已點擊最後確認按鈕，但姓名預覽視窗未關閉且未顯示明確結果")


def set_facebook_language(driver, profile_name: str, target: str, timeout: int = 45) -> bool:
    """將 Facebook 介面語言切換為指定語言，支援新版 Account language 對話框與多語系。"""
    labels = LANGUAGE_OPTIONS.get(target)
    if not labels:
        raise ValueError(f"不支援的 Facebook 語言：{target}")

    language_row_labels = (
        "account language", "facebook language",
        "wika ng account", "wika ng facebook",
        "帳號語言", "賬號語言", "账户语言", "facebook 語言", "facebook 语言",
        "langue du compte", "langue de facebook",
        "kontosprache", "facebook-sprache",
        "idioma de la cuenta", "idioma de facebook",
        "idioma da conta", "idioma do facebook",
        "lingua dell'account", "lingua di facebook",
        "accounttaal", "facebook-taal",
        "bahasa akun", "bahasa akaun", "bahasa facebook",
        "ngôn ngữ tài khoản", "ngôn ngữ facebook",
        "ภาษาของบัญชี", "ภาษา facebook",
        "アカウントの言語", "facebookの言語",
        "계정 언어", "facebook 언어",
        "لغة الحساب", "لغة فيسبوك", "لغة facebook",
    )
    search_labels = (
        "search languages", "search language", "search",
        "maghanap ng mga wika", "maghanap ng wika", "maghanap",
        "搜尋語言", "搜索语言", "搜尋", "搜索",
        "rechercher des langues", "rechercher une langue", "rechercher",
        "sprachen suchen", "sprache suchen", "suchen",
        "buscar idiomas", "buscar idioma", "buscar",
        "pesquisar idiomas", "pesquisar idioma", "pesquisar",
        "cerca lingue", "cerca lingua", "cerca",
        "talen zoeken", "taal zoeken", "zoeken",
        "cari bahasa", "tìm kiếm ngôn ngữ", "ค้นหาภาษา",
        "言語を検索", "언어 검색",
        "بحث عن لغات", "البحث عن اللغات", "بحث عن لغة", "بحث",
    )
    save_labels = (
        "save changes", "save", "apply", "done", "ok", "okay",
        "i-save ang mga pagbabago", "i-save", "ilapat", "tapos na", "sige",
        "儲存變更", "儲存", "套用", "完成", "確定", "保存更改", "保存", "应用", "确定",
        "enregistrer les modifications", "enregistrer", "appliquer", "terminé", "d'accord", "d’accord",
        "änderungen speichern", "speichern", "anwenden", "fertig",
        "guardar cambios", "guardar", "aplicar", "listo", "aceptar",
        "salvar alterações", "salvar", "aplicar", "concluir",
        "salva modifiche", "salva", "applica", "fine",
        "wijzigingen opslaan", "opslaan", "toepassen", "gereed",
        "simpan perubahan", "simpan", "terapkan", "selesai",
        "lưu thay đổi", "lưu", "áp dụng", "xong",
        "บันทึกการเปลี่ยนแปลง", "บันทึก", "นำไปใช้", "เสร็จสิ้น", "ตกลง",
        "変更を保存", "保存", "適用", "完了",
        "변경 내용 저장", "저장", "적용", "완료",
        "حفظ التغييرات", "حفظ", "تطبيق", "تم", "موافق",
    )
    cancel_or_close_labels = (
        "cancel", "close", "not now",
        "i-cancel", "isara", "huwag ngayon",
        "取消", "關閉", "关闭", "稍後再說", "以后再说",
        "annuler", "fermer", "pas maintenant",
        "abbrechen", "schließen", "jetzt nicht",
        "cancelar", "cerrar", "ahora no",
        "إلغاء", "إغلاق", "ليس الآن",
        "ยกเลิก", "ปิด", "ไว้คราวหน้า",
        "キャンセル", "閉じる", "後で",
        "취소", "닫기", "나중에",
    )

    def norm(value: str) -> str:
        return " ".join((value or "").split()).casefold()

    def visible_dialogs():
        result = []
        for item in driver.find_elements(By.CSS_SELECTOR, "[role='dialog']"):
            try:
                if item.is_displayed():
                    result.append(item)
            except StaleElementReferenceException:
                continue
        return result

    def click_element(element) -> bool:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", element)
            time.sleep(0.15)
            element.click()
            return True
        except Exception:
            try:
                return bool(driver.execute_script(
                    """
                    const e=arguments[0];
                    if(!e) return false;
                    e.scrollIntoView({block:'center', inline:'nearest'});
                    for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                        e.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,view:window}));
                    }
                    return true;
                    """, element))
            except Exception:
                return False

    def current_document_language() -> str:
        try:
            return str(driver.execute_script(
                "return document.documentElement ? document.documentElement.lang : '';"
            ) or "")
        except Exception:
            return ""

    def wait_until_target_applied(wait_seconds: float = 18.0) -> bool:
        verify_end = time.monotonic() + wait_seconds
        while time.monotonic() < verify_end:
            if _facebook_document_language_matches(target, current_document_language()):
                return True
            time.sleep(0.3)
        return False

    try:
        driver.get("https://www.facebook.com/settings/?tab=language")
    except (TimeoutException, WebDriverException) as exc:
        _log.warning("[%s] 語言設定頁載入較慢，停止等待後檢查 DOM：%s", profile_name, exc)
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    end = time.monotonic() + timeout

    # 已是目標語言時不再重開對話框，避免點擊目前已選取的 radio 後
    # Facebook 不產生確認視窗，反而被舊流程誤判為逾時。
    initial_end = time.monotonic() + min(8, timeout)
    while time.monotonic() < initial_end:
        document_language = current_document_language()
        if _facebook_document_language_matches(target, document_language):
            _log.info(
                "[%s] Facebook 介面語言已是 %s（HTML lang=%s），略過修改。",
                profile_name, target, document_language,
            )
            return True
        if document_language:
            break
        time.sleep(0.25)

    def language_dialogs():
        dialogs = visible_dialogs()
        wanted_labels = {norm(value) for value in labels}
        for index, dialog in enumerate(dialogs):
            try:
                text = norm(dialog.text)
                has_controls = bool(dialog.find_elements(
                    By.CSS_SELECTOR, "[role='radio'], input[type='radio'], input[type='search']"
                ))
                has_language_text = any(value in text for value in wanted_labels)
                if has_controls or has_language_text:
                    # 原始語言清單上可能再疊一層「已儲存，請重新整理」確認框；
                    # 一併保留其後所有可見 dialog，current_dialog 才會取得最上層。
                    return dialogs[index:]
            except StaleElementReferenceException:
                continue
        return []

    # 只點真正的 Account language / Facebook Language 設定列。click() 成功
    # 不代表 React 已開窗；必須看到語言 radio/search dialog 才算完成此步。
    row_clicked = False
    while time.monotonic() < end and not row_clicked:
        candidates = driver.find_elements(
            By.CSS_SELECTOR,
            "[role='button'], span, h1, h2, h3, div[dir='auto']",
        )
        for node in candidates:
            try:
                if not node.is_displayed():
                    continue
                text = norm(node.text or node.get_attribute("aria-label") or "")
                if not any(text == label or text.startswith(label + " ") for label in language_row_labels):
                    continue
                row = node if (node.get_attribute("role") == "button" or node.tag_name in ("button", "a")) else None
                if row is None:
                    try:
                        row = node.find_element(By.XPATH, "./ancestor::*[@role='button' or self::button or self::a][1]")
                    except Exception:
                        continue
                if not click_element(row):
                    continue
                opened_end = time.monotonic() + 2.5
                while time.monotonic() < opened_end:
                    if language_dialogs():
                        row_clicked = True
                        break
                    if _facebook_document_language_matches(target, current_document_language()):
                        return True
                    time.sleep(0.2)
                if row_clicked:
                    break
            except StaleElementReferenceException:
                continue
        if not row_clicked:
            time.sleep(0.35)

    if not row_clicked:
        raise RuntimeError("語言和地區頁面找不到 Account language / Facebook Language 設定列")

    # 必須確認 Facebook 原生語言選擇對話框已實際出現。
    # Facebook 的 React 對話框會頻繁重新渲染，因此後續每一步都重新定位，
    # 不保留 dialog / input / radio 的舊 WebElement 參照。
    def current_dialog():
        dialogs = language_dialogs()
        return dialogs[-1] if dialogs else None

    def dialog_elements(css_selector: str):
        """每次從 driver 重新取得目前可見對話框中的元素，避開 stale element。"""
        for _ in range(4):
            dialog_now = current_dialog()
            if dialog_now is None:
                return []
            try:
                return dialog_now.find_elements(By.CSS_SELECTOR, css_selector)
            except StaleElementReferenceException:
                time.sleep(0.12)
        return []

    dialog_end = time.monotonic() + 12
    while time.monotonic() < dialog_end and current_dialog() is None:
        time.sleep(0.25)
    if current_dialog() is None:
        raise RuntimeError("已找到 Account language，但無法開啟語言選擇視窗")

    # 新版對話框提供搜尋欄；每次重新定位搜尋框，輸入後不再保留該元素。
    search_done = False
    search_end = time.monotonic() + 8
    while time.monotonic() < search_end and not search_done:
        fields = dialog_elements("input[type='search'], input[type='text'], input:not([type])")
        for field in fields:
            try:
                if not field.is_displayed() or not field.is_enabled():
                    continue
                hint = norm(" ".join(filter(None, (
                    field.get_attribute("placeholder"),
                    field.get_attribute("aria-label"),
                    field.get_attribute("name"),
                ))))
                if hint and not any(label in hint for label in search_labels):
                    continue
                field.click()
                try:
                    field.clear()
                except Exception:
                    pass
                field.send_keys(labels[0])
                search_done = True
                time.sleep(0.8)
                break
            except StaleElementReferenceException:
                break
            except Exception:
                continue
        if not search_done:
            time.sleep(0.2)

    # 在對話框內精準找目標語言。每次循環都重新取得 radio / 文字列。
    wanted = {norm(x) for x in labels}
    selected = False
    select_end = time.monotonic() + 20
    while time.monotonic() < select_end and not selected:
        radios = dialog_elements("[role='radio'], input[type='radio']")
        for radio in radios:
            try:
                if not radio.is_displayed():
                    continue
                text = norm(driver.execute_script(
                    r"""
                    let e=arguments[0];
                    for(let i=0;e && i<6;i++,e=e.parentElement){
                        const t=(e.innerText||'').replace(/\s+/g,' ').trim();
                        if(t) return t;
                    }
                    return '';
                    """, radio) or "")
                lines = {norm(line) for line in (text or "").split("\n") if norm(line)}
                if wanted.intersection(lines) or any(text == w or text.startswith(w + " ") for w in wanted):
                    selected = click_element(radio)
                    if selected:
                        break
            except StaleElementReferenceException:
                # React 已重建清單；下一輪重新定位。
                break
            except Exception:
                continue

        if selected:
            break

        nodes = dialog_elements("span, div[dir='auto'], label")
        for node in nodes:
            try:
                if not node.is_displayed() or norm(node.text) not in wanted:
                    continue
                clickable = None
                for xpath in (
                    "./ancestor::*[@role='radio'][1]",
                    "./ancestor::label[1]",
                    "./ancestor::*[@role='button'][1]",
                ):
                    try:
                        clickable = node.find_element(By.XPATH, xpath)
                        break
                    except StaleElementReferenceException:
                        clickable = None
                        break
                    except Exception:
                        continue
                selected = click_element(clickable or node)
                if selected:
                    break
            except StaleElementReferenceException:
                break
            except Exception:
                continue

        if not selected:
            time.sleep(0.3)

    if not selected:
        raise RuntimeError(f"Facebook 語言選擇視窗找不到 {target}")

    # 部分版本選取即生效；部分版本會顯示 Save / Apply / Done。
    # 按鈕同樣每次重新定位，避免選取語言後 React 重繪造成 stale。
    save_end = time.monotonic() + 12
    while time.monotonic() < save_end:
        if _facebook_document_language_matches(target, current_document_language()):
            _log.info(
                "[%s] Facebook 介面語言已設定為 %s（HTML lang=%s）。",
                profile_name, target, current_document_language(),
            )
            return True
        if current_dialog() is None:
            if wait_until_target_applied(6):
                _log.info("[%s] Facebook 介面語言已自動套用為 %s。", profile_name, target)
                return True
            # 少數版本只保存設定、不主動重新整理；對話框已關閉時安全
            # 重新載入一次，再以 HTML lang 驗證，不能只憑「曾點到 radio」。
            try:
                driver.refresh()
            except (TimeoutException, WebDriverException):
                try:
                    driver.execute_script("window.stop();")
                except Exception:
                    pass
            if wait_until_target_applied():
                _log.info("[%s] Facebook 介面語言已設定為 %s。", profile_name, target)
                return True
            break
        buttons = dialog_elements("button, [role='button']")

        # 選取語言後，Facebook 可能在原語言清單上再疊一層「變更已
        # 儲存，請重新整理」確認框。不同介面會顯示 OK、موافق、ตกลง
        # 等文字。兩層 dialog 存在時，排除 Close / Cancel 後只剩一個
        # 可用按鈕，便可安全視為重新整理確認，不必窮舉所有語言。
        if len(visible_dialogs()) >= 2:
            affirmative_buttons = []
            for button in buttons:
                try:
                    if not button.is_displayed() or not button.is_enabled():
                        continue
                    if str(button.get_attribute("aria-disabled")).lower() == "true":
                        continue
                    text = norm(button.text or button.get_attribute("aria-label") or "")
                    if not text or text in cancel_or_close_labels:
                        continue
                    affirmative_buttons.append(button)
                except StaleElementReferenceException:
                    affirmative_buttons = []
                    break
                except Exception:
                    continue
            if len(affirmative_buttons) == 1 and click_element(affirmative_buttons[0]):
                if wait_until_target_applied():
                    _log.info(
                        "[%s] 已確認重新整理，Facebook 介面語言已設定為 %s（HTML lang=%s）。",
                        profile_name, target, current_document_language(),
                    )
                    return True
                raise RuntimeError(
                    f"已確認語言設定重新整理，但頁面仍是 HTML lang="
                    f"{current_document_language() or '未知'}，目標={target}"
                )

        for button in buttons:
            try:
                if not button.is_displayed() or not button.is_enabled():
                    continue
                text = norm(button.text or button.get_attribute("aria-label") or "")
                if text in save_labels and str(button.get_attribute("aria-disabled")).lower() != "true":
                    if click_element(button):
                        if wait_until_target_applied():
                            _log.info(
                                "[%s] Facebook 介面語言已設定為 %s（HTML lang=%s）。",
                                profile_name, target, current_document_language(),
                            )
                            return True
                        raise RuntimeError(
                            f"已點擊語言確認按鈕，但頁面仍是 HTML lang="
                            f"{current_document_language() or '未知'}，目標={target}"
                        )
            except StaleElementReferenceException:
                break
            except Exception:
                continue
        time.sleep(0.3)

    if _facebook_document_language_matches(target, current_document_language()):
        _log.info(
            "[%s] Facebook 介面語言已設定為 %s（HTML lang=%s）。",
            profile_name, target, current_document_language(),
        )
        return True
    raise RuntimeError(
        f"已選擇 Facebook 語言 {target}，但找不到可用的確認按鈕，"
        f"且頁面仍是 HTML lang={current_document_language() or '未知'}"
    )
