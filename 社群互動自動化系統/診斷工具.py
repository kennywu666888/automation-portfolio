from pathlib import Path
from datetime import datetime
import json,zipfile,traceback,re,shutil
BASE=Path(__file__).resolve().parent/'diagnostics'/'notification_monitor';BASE.mkdir(parents=True,exist_ok=True)
def mask(s):
    value=re.sub(r'(?i)(token|secret|password|authorization)\s*[:=]\s*[^\s,]+',r'\1=***',str(s))
    return re.sub(r'(?i)(https://api\.telegram\.org/bot)[^/\s]+',r'\1***',value)
def safe_settings(settings):
    return {
        key: ('***' if key.lower() in {'adspower_api_key','api_key'} else value)
        for key,value in dict(settings or {}).items()
    }
def save_diagnostic(driver,profile,pid,stage,exc,settings,candidates,log_path=None):
    safe=''.join(c if c.isalnum() or c in '-_' else '_' for c in profile)[:60];stamp=f'{datetime.now():%Y%m%d_%H%M%S}_{safe}';tmp=BASE/stamp;tmp.mkdir()
    (tmp/'diagnostic.txt').write_text(mask(f'環境：{profile}\nProfile ID：{pid}\n階段：{stage}\nURL：{getattr(driver,"current_url","")}\n錯誤：{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}'),encoding='utf-8')
    (tmp/'page_source.html').write_text(mask(getattr(driver,'page_source','')),encoding='utf-8')
    try:driver.save_screenshot(str(tmp/'screenshot.png'))
    except Exception:pass
    (tmp/'settings_snapshot.json').write_text(mask(json.dumps(safe_settings(settings),ensure_ascii=False,indent=2)),encoding='utf-8');(tmp/'notification_candidates.json').write_text(json.dumps([x.to_dict() for x in candidates],ensure_ascii=False,indent=2),encoding='utf-8')
    if log_path and Path(log_path).exists():shutil.copy2(log_path,tmp/'task.log')
    z=BASE/f'{stamp}.zip'
    with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as f:
        for p in tmp.iterdir():f.write(p,p.name)
    shutil.rmtree(tmp);return z
