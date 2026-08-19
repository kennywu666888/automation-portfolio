from dataclasses import dataclass
@dataclass(frozen=True)
class HealthResult:status:str;detail:str
def check_health(driver):
    url=(driver.current_url or '').lower();src=(driver.page_source or '').lower()
    if 'login' in url or driver.find_elements('css selector','input[name="email"],input[name="pass"]'):return HealthResult('login_required','偵測到登入頁')
    if 'checkpoint' in url:return HealthResult('checkpoint','網址為 checkpoint')
    if any(x in src for x in ('temporarily blocked','暫時封鎖')):return HealthResult('temporarily_blocked','偵測到暫時封鎖')
    if any(x in src for x in ('confirm your identity','驗證你的身分','security check')):return HealthResult('verification_required','需要人工驗證')
    if 'facebook.com' in url:return HealthResult('healthy','Facebook 可正常存取')
    return HealthResult('unknown','無法確認狀態')
