import json,threading,time
from pathlib import Path
from datetime import datetime,timedelta
PATH=Path(__file__).resolve().parent/'schedules.json'
class Scheduler:
    def __init__(self,callback,logger):self.callback=callback;self.logger=logger;self.stop_event=threading.Event();self.last={};self.thread=None
    def load(self):
        try:return json.loads(PATH.read_text(encoding='utf-8'))
        except Exception:return []
    def save(self,d):PATH.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self._loop,daemon=True);self.thread.start()
    def _loop(self):
        while not self.stop_event.wait(20):
            now=datetime.now()
            for s in self.load():
                if not s.get('enabled',True):continue
                due=False
                if s.get('type')=='daily':due=now.strftime('%H:%M')==s.get('time')
                elif s.get('type')=='interval':
                    last=datetime.fromisoformat(s['last_run']) if s.get('last_run') else datetime.min;due=now-last>=timedelta(hours=float(s.get('hours',1)))
                key=str(s.get('id',''))
                if due and time.time()-self.last.get(key,0)>60:self.last[key]=time.time();self.callback(s)
