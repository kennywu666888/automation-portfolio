import uuid
from PyQt6.QtCore import QThread, pyqtSignal
from scrapers import SCRAPERS


class CrawlWorker(QThread):
    company=pyqtSignal(object,bool); message=pyqtSignal(str); progress=pyqtSignal(dict); failed=pyqtSignal(str); done=pyqtSignal()
    def __init__(self,database,config):
        super().__init__();self.db=database;self.config=config;self.result_status="已完成"
        import threading
        self.pause_event=threading.Event();self.pause_event.set();self.stop_event=threading.Event();self.task_id=str(uuid.uuid4())
    def pause(self):self.pause_event.clear()
    def resume(self):self.pause_event.set()
    def stop(self):self.stop_event.set();self.pause_event.set()
    def run(self):
        stats={"discovered":0,"completed":0,"emails":0,"phones":0,"no_email":0,"duplicates":0,"errors":0}
        seen_urls=set();seen_emails=set();failed_categories=[];finished_categories=0
        total_categories=sum(len(self.config.get("categories_by_source",{}).get(source,[])) for source in self.config["sources"])
        try:
            remaining=self.config["max_results"]
            for source in self.config["sources"]:
                if self.stop_event.is_set():break
                scraper=SCRAPERS[source](self.config["min_delay"],self.config["max_delay"],self.pause_event,self.stop_event,self.message.emit)
                for category in self.config.get("categories_by_source",{}).get(source,[]):
                    if self.stop_event.is_set() or (self.config["max_results"] and remaining<=0):break
                    category_ok=False
                    for attempt in range(2):
                        self.message.emit(f"開始搜尋 {source}（分類：{category}，嘗試 {attempt+1}/2）");self.progress.emit({"source":source,"category":category})
                        try:
                            limit=remaining if self.config["max_results"] else 0
                            for c in scraper.search(self.config["keyword"],category,self.config["city"],limit):
                                if self.stop_event.is_set():break
                                url_key=c.source_urls[0] if c.source_urls else f"{source}:{c.company_name.lower()}"
                                if url_key in seen_urls:continue
                                seen_urls.add(url_key);stats["discovered"]+=1
                                exclusions=[x.strip().lower() for x in self.config["exclude"].splitlines() if x.strip()]
                                if any(x in c.company_name.lower() for x in exclusions):continue
                                _,duplicate=self.db.upsert_company(c);stats["completed"]+=1;stats["duplicates"]+=int(duplicate)
                                seen_emails.update(email.lower() for email in c.emails);stats["emails"]=len(seen_emails);stats["phones"]+=len(c.phones)+len(c.mobiles);stats["no_email"]+=int(not c.emails)
                                self.db.save_progress(self.task_id,source,category,0,url_key,"done")
                                self.company.emit(c,duplicate);self.progress.emit(stats.copy())
                                email_text="、".join(c.emails) if c.emails else "無 Email"
                                self.message.emit(f"完成：{c.company_name}｜{email_text}｜{'重複資料，已合併' if duplicate else '新增資料'}")
                                if self.config["max_results"]:
                                    remaining-=1
                                    if remaining<=0:break
                            if self.stop_event.is_set():break
                            category_ok=True;finished_categories+=1;self.message.emit(f"分類完成：{source}／{category}（{finished_categories}/{total_categories}）");break
                        except Exception as exc:
                            if attempt==0:self.message.emit(f"分類失敗，準備重試：{source}／{category}｜{type(exc).__name__}: {exc}")
                            else:
                                stats["errors"]+=1;failed_categories.append(f"{source}／{category}");self.failed.emit(f"{source}／{category}：重試後仍失敗：{type(exc).__name__}: {exc}");self.progress.emit(stats.copy())
                    if self.stop_event.is_set():break
                if self.config["max_results"] and remaining<=0:break
        finally:
            if self.stop_event.is_set():self.result_status="已停止"
            elif stats["errors"]:self.result_status="完成，但有錯誤"
            self.message.emit(f"工作{self.result_status}｜分類 {finished_categories}/{total_categories}｜完成 {stats['completed']} 家｜不重複 Email {stats['emails']} 個｜重複 {stats['duplicates']} 家｜錯誤 {stats['errors']} 個")
            if failed_categories:self.message.emit("未完成分類："+"、".join(failed_categories))
            self.done.emit()
