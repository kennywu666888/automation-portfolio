import tkinter as tk
from tkinter import ttk,messagebox,filedialog
import threading,queue
from pathlib import Path
from 設定 import load_settings,save_settings,BASE_DIR,telegram_credentials,target_group
from 環境管理客戶端 import AdsPowerClient
from Telegram回報 import TelegramReporter, TelegramTargets
from 日誌 import setup_logger
from 主程式 import MonitorEngine
from 工具 import open_folder
from 個人資料工具 import profile_matches_search, sort_profiles_by_number
from 社團留言任務 import load_groups as load_group_urls
from text_sources import load_text_lines
from 媒體來源 import MediaPool

AUTHOR_DEDUPE_LABELS = {
    '每個群組內': 'group',
    '整個環境': 'environment',
    '不限制作者': 'none',
}
AUTHOR_DEDUPE_VALUES = {value: label for label, value in AUTHOR_DEDUPE_LABELS.items()}
MEDIA_MODE_LABELS = {
    '不附加媒體': 'none',
    '相片／影片隨機': 'random',
    '固定相片／影片': 'fixed',
}
MEDIA_MODE_VALUES = {value: label for label, value in MEDIA_MODE_LABELS.items()}

class App(tk.Tk):
    def __init__(self):
        super().__init__();self.title('Facebook 留言與回覆監控器 V1.0.0 RC19');self.geometry('1240x1000');self.minsize(1040,800)
        self.s=load_settings();self.groups=[];self.profiles=[];self.filtered=[];self.engine=None;self.q=queue.Queue();self.logger=setup_logger(lambda x:self.q.put(x));self.vars={};self.adspower_api_key=tk.StringVar(value='');self.api_key_visible=False;self._build();self.after(100,self._drain);self.after(400,self.auto_load_target_group)
    def v(self,k,default=''):self.vars[k]=tk.StringVar(value=str(self.s.get(k,default)));return self.vars[k]
    def _build(self):
        pan=ttk.Panedwindow(self,orient='vertical');pan.pack(fill='both',expand=True,padx=8,pady=8)
        top=ttk.Frame(pan);logf=ttk.Frame(pan);pan.add(top,weight=4);pan.add(logf,weight=2)
        left=ttk.LabelFrame(top,text='AdsPower 與環境');left.pack(side='left',fill='both',expand=True,padx=(0,6));right_shell=ttk.LabelFrame(top,text='任務與執行設定');right_shell.pack(side='left',fill='both',expand=True);right_canvas=tk.Canvas(right_shell,highlightthickness=0);right_scroll=ttk.Scrollbar(right_shell,orient='vertical',command=right_canvas.yview);right_scroll.pack(side='right',fill='y');right_canvas.pack(side='left',fill='both',expand=True);right_canvas.configure(yscrollcommand=right_scroll.set);right=ttk.Frame(right_canvas);right.columnconfigure(0,weight=1);right_window=right_canvas.create_window((0,0),window=right,anchor='nw');right.bind('<Configure>',lambda _event:right_canvas.configure(scrollregion=right_canvas.bbox('all')));right_canvas.bind('<Configure>',lambda event:right_canvas.itemconfigure(right_window,width=event.width))
        ttk.Label(left,text='API 位址').grid(row=0,column=0,sticky='w');ttk.Entry(left,textvariable=self.v('adspower_base_url'),width=42).grid(row=0,column=1,columnspan=3,sticky='ew')
        ttk.Label(left,text='API Key（只保留本次執行）').grid(row=1,column=0,sticky='w');self.api_key_entry=ttk.Entry(left,textvariable=self.adspower_api_key,show='*',width=42);self.api_key_entry.grid(row=1,column=1,columnspan=2,sticky='ew');self.api_key_toggle=ttk.Button(left,text='顯示',command=self.toggle_api_key,width=7);self.api_key_toggle.grid(row=1,column=3,sticky='ew')
        ttk.Button(left,text='讀取群組',command=self.load_groups).grid(row=2,column=0,pady=4);self.group=ttk.Combobox(left,state='readonly');self.group.grid(row=2,column=1,sticky='ew');ttk.Button(left,text='讀取環境',command=self.load_profiles).grid(row=2,column=2);ttk.Button(left,text='測試 API',command=self.test_adspower_api).grid(row=2,column=3)
        ttk.Label(left,text='搜尋（名稱／群組／ID／序號／IP）').grid(row=3,column=0);self.search=tk.StringVar();e=ttk.Entry(left,textvariable=self.search);e.grid(row=3,column=1,columnspan=3,sticky='ew');self.search.trace_add('write',lambda *_:self.apply_filter())
        self.list=tk.Listbox(left,selectmode='extended');self.list.grid(row=4,column=0,columnspan=4,sticky='nsew',pady=5);left.rowconfigure(4,weight=1);left.columnconfigure(1,weight=1)
        ttk.Button(left,text='全選目前清單',command=lambda:self.list.select_set(0,'end')).grid(row=5,column=0,columnspan=2,sticky='ew');ttk.Button(left,text='取消全部',command=lambda:self.list.select_clear(0,'end')).grid(row=5,column=2,columnspan=2,sticky='ew')
        r=0
        for label,key,default in [('每環境公開留言筆數','comments_per_profile',10),('群組留言最大捲動','group_comment_max_scrolls',20),('每環境最多通知回覆','max_replies',20),('通知最大捲動次數','max_scrolls',5),('執行線程','threads',1),('循環次數（0=無限）','cycles',1),('每輪等待分鐘','cycle_wait_minutes',30),('通知間隔秒數','notification_wait_seconds',2)]:
            ttk.Label(right,text=label).grid(row=r,column=0,sticky='w',pady=3);ttk.Spinbox(right,from_=0,to=200,textvariable=self.v(key,default),width=12).grid(row=r,column=1,sticky='w');r+=1
        ttk.Label(right,text='排序').grid(row=r,column=0,sticky='w');self.sort=ttk.Combobox(right,values=['oldest','newest'],state='readonly',width=12);self.sort.set(self.s.get('sort_order','oldest'));self.sort.grid(row=r,column=1,sticky='w');r+=1
        ttk.Label(right,text='公開留言作者去重').grid(row=r,column=0,sticky='w');self.author_dedupe=ttk.Combobox(right,values=list(AUTHOR_DEDUPE_LABELS),state='readonly',width=16);self.author_dedupe.set(AUTHOR_DEDUPE_VALUES.get(str(self.s.get('author_dedupe_scope','group')),'每個群組內'));self.author_dedupe.grid(row=r,column=1,sticky='w');r+=1
        ttk.Label(right,text='公開留言媒體').grid(row=r,column=0,sticky='w');self.media_mode=ttk.Combobox(right,values=list(MEDIA_MODE_LABELS),state='readonly',width=16);self.media_mode.set(MEDIA_MODE_VALUES.get(str(self.s.get('group_comment_media_mode','random')),'相片／影片隨機'));self.media_mode.grid(row=r,column=1,sticky='w');self.media_mode.bind('<<ComboboxSelected>>',lambda _event:self._refresh_media_controls());r+=1
        self.enable_group_comment=tk.BooleanVar(value=bool(self.s.get('enable_group_comment_task',False)));self.enable_notification=tk.BooleanVar(value=bool(self.s.get('enable_notification_task',True)));self.only_unread=tk.BooleanVar(value=bool(self.s.get('only_unread',False)));self.new_only=tk.BooleanVar(value=bool(self.s.get('new_section_only',True)));self.process_replies=tk.BooleanVar(value=bool(self.s.get('process_replies',True)));self.process_mentions=tk.BooleanVar(value=bool(self.s.get('process_mentions',True)));self.tg=tk.BooleanVar(value=bool(self.s.get('telegram_enabled',True)));self.auto_reply=tk.BooleanVar(value=bool(self.s.get('auto_reply_enabled',True)));self.close=tk.BooleanVar(value=bool(self.s.get('close_browser',False)));self.shuffle=tk.BooleanVar(value=False);self.delete_verified=tk.BooleanVar(value=bool(self.s.get('delete_verified_profile',False)));self.delete_group_after_success=tk.BooleanVar(value=bool(self.s.get('delete_group_url_after_success',self.s.get('delete_group_url_after_claim',True))))
        ttk.Label(right,text='環境固定依名稱尾端數字由小到大執行').grid(row=r,column=0,columnspan=2,sticky='w');r+=1
        for text,var in [('執行群組公開留言（桌面 group.txt）',self.enable_group_comment),('公開留言成功後才從 group.txt 移除該網址',self.delete_group_after_success),('執行通知留言回覆',self.enable_notification),('處理留言回覆（Reply）',self.process_replies),('處理留言提及（Mention）',self.process_mentions),('只處理 New 區段',self.new_only),('只處理未讀通知（嚴格模式）',self.only_unread),('啟用 Telegram 回報',self.tg),('啟用自動回覆客戶',self.auto_reply),('完成後關閉 AdsPower',self.close),('驗證／停權／睡眠／登入後刪除 AdsPower 環境（高風險；代理／IP失效不刪除）',self.delete_verified)]:ttk.Checkbutton(right,text=text,variable=var).grid(row=r,column=0,columnspan=2,sticky='w');r+=1
        ttk.Label(right,text='群組留言文案檔（隨機一則；多行文案用 --- 分隔）').grid(row=r,column=0,columnspan=2,sticky='w');r+=1
        self.group_comment_file_var=self.v('group_comment_text_file',str(Path.home()/'Desktop'/'文一.txt'));ttk.Entry(right,textvariable=self.group_comment_file_var,width=42).grid(row=r,column=0,sticky='ew');ttk.Button(right,text='選擇檔案',command=lambda:self.choose_text_file('group_comment_text_file','選擇群組留言文案')).grid(row=r,column=1,sticky='ew');r+=1
        ttk.Label(right,text='相片／影片隨機資料夾').grid(row=r,column=0,columnspan=2,sticky='w');r+=1
        self.random_media_dir_var=self.v('group_comment_random_media_dir',str(Path.home()/'Desktop'/'view'));self.random_media_entry=ttk.Entry(right,textvariable=self.random_media_dir_var,width=42);self.random_media_entry.grid(row=r,column=0,sticky='ew');self.random_media_button=ttk.Button(right,text='選擇資料夾',command=lambda:self.choose_folder('group_comment_random_media_dir','選擇隨機相片／影片資料夾'));self.random_media_button.grid(row=r,column=1,sticky='ew');r+=1
        ttk.Label(right,text='固定相片／影片檔案').grid(row=r,column=0,columnspan=2,sticky='w');r+=1
        self.fixed_media_file_var=self.v('group_comment_fixed_media_file','');self.fixed_media_entry=ttk.Entry(right,textvariable=self.fixed_media_file_var,width=42);self.fixed_media_entry.grid(row=r,column=0,sticky='ew');self.fixed_media_button=ttk.Button(right,text='選擇檔案',command=lambda:self.choose_media_file('group_comment_fixed_media_file','選擇固定相片／影片'));self.fixed_media_button.grid(row=r,column=1,sticky='ew');r+=1;self._refresh_media_controls()
        ttk.Label(right,text='客戶回覆文案檔（隨機一則；多行文案用 --- 分隔）').grid(row=r,column=0,columnspan=2,sticky='w');r+=1
        self.customer_reply_file_var=self.v('customer_reply_text_file',str(Path.home()/'Desktop'/'回覆文案.txt'));ttk.Entry(right,textvariable=self.customer_reply_file_var,width=42).grid(row=r,column=0,sticky='ew');ttk.Button(right,text='選擇檔案',command=lambda:self.choose_text_file('customer_reply_text_file','選擇客戶回覆文案')).grid(row=r,column=1,sticky='ew');r+=1
        ttk.Label(right,text='Telegram 帳號（與文案間隔一個空白行）').grid(row=r,column=0,sticky='w');ttk.Entry(right,textvariable=self.v('telegram_account','@telegram'),width=24).grid(row=r,column=1,sticky='ew');r+=1
        ttk.Button(right,text='測試收到留言群',command=self.test_telegram).grid(row=r,column=0,sticky='ew',pady=5);ttk.Button(right,text='測試已回覆群',command=self.test_reply_telegram).grid(row=r,column=1,sticky='ew');r+=1
        ttk.Button(right,text='開始執行',command=self.start_run).grid(row=r,column=0,columnspan=2,sticky='ew');r+=1
        ttk.Button(right,text='停止執行',command=self.stop_run).grid(row=r,column=0,sticky='ew');ttk.Button(right,text='開啟診斷資料夾',command=lambda:open_folder(BASE_DIR/'diagnostics')).grid(row=r,column=1,sticky='ew');r+=1
        ttk.Button(right,text='開啟 LOG 資料夾',command=lambda:open_folder(BASE_DIR/'logs')).grid(row=r,column=0,sticky='ew');ttk.Button(right,text='開啟資料庫資料夾',command=lambda:open_folder(BASE_DIR)).grid(row=r,column=1,sticky='ew');r+=1
        self.status=tk.StringVar(value='待命');ttk.Label(right,textvariable=self.status).grid(row=r,column=0,columnspan=2,sticky='w');r+=1;self.pb=ttk.Progressbar(right,mode='determinate');self.pb.grid(row=r,column=0,columnspan=2,sticky='ew')
        self.log=tk.Text(logf,height=14,wrap='word');self.log.pack(fill='both',expand=True)
    def _drain(self):
        while True:
            try:self.log.insert('end',self.q.get_nowait()+'\n');self.log.see('end')
            except queue.Empty:break
        self.after(100,self._drain)
    def toggle_api_key(self):
        self.api_key_visible=not self.api_key_visible
        self.api_key_entry.configure(show='' if self.api_key_visible else '*')
        self.api_key_toggle.configure(text='隱藏' if self.api_key_visible else '顯示')
    def _background(self,work,done,error_title='錯誤'):
        def worker():
            try:result=work()
            except Exception as exc:
                message=str(exc)
                self.after(0,lambda:messagebox.showerror(error_title,message))
            else:self.after(0,lambda:done(result))
        threading.Thread(target=worker,daemon=True).start()
    def choose_text_file(self,key,title):
        path=filedialog.askopenfilename(title=title,filetypes=[('文字檔案','*.txt'),('所有檔案','*.*')])
        if path:self.vars[key].set(path)
    def choose_folder(self,key,title):
        path=filedialog.askdirectory(title=title)
        if path:self.vars[key].set(path)
    def choose_media_file(self,key,title):
        path=filedialog.askopenfilename(title=title,filetypes=[('相片與影片','*.jpg *.jpeg *.png *.webp *.gif *.heic *.heif *.mp4 *.m4v *.mov *.webm *.avi *.mkv *.3gp'),('所有檔案','*.*')])
        if path:self.vars[key].set(path)
    def _refresh_media_controls(self):
        mode=MEDIA_MODE_LABELS.get(self.media_mode.get(),'none')
        if hasattr(self,'random_media_entry'):
            random_state='normal' if mode=='random' else 'disabled';self.random_media_entry.configure(state=random_state);self.random_media_button.configure(state=random_state)
        if hasattr(self,'fixed_media_entry'):
            fixed_state='normal' if mode=='fixed' else 'disabled';self.fixed_media_entry.configure(state=fixed_state);self.fixed_media_button.configure(state=fixed_state)
    def auto_load_target_group(self):
        gid,gname=target_group();base=self.vars['adspower_base_url'].get();api_key=self.adspower_api_key.get()
        def work():
            client=AdsPowerClient(base,api_key=api_key)
            groups=[('0','全部群組')]+client.list_groups()
            idx = 0
            for i,item in enumerate(groups):
                if (gid and item[0] == gid) or (gname and item[1] == gname):
                    idx=i;break
            profiles=client.list_profiles(groups[idx][0]) if groups else []
            return groups,idx,profiles
        def done(result):
            self.groups,idx,profiles=result;self.profiles=sort_profiles_by_number(profiles)
            self.group['values']=[x[1] for x in self.groups]
            if self.groups:self.group.current(idx);self.logger.info('程式啟動自動選擇群組：%s',self.groups[idx][1])
            self.apply_filter();self.logger.info('讀取到 %d 個環境',len(self.profiles))
        self._background(work,done,'啟動時自動讀取失敗')

    def load_groups(self):
        base=self.vars['adspower_base_url'].get();api_key=self.adspower_api_key.get()
        def done(groups):self.groups=groups;self.group['values']=[x[1] for x in groups];self.group.current(0)
        self._background(lambda:[('0','全部群組')]+AdsPowerClient(base,api_key=api_key).list_groups(),done)
    def test_adspower_api(self):
        base=self.vars['adspower_base_url'].get();api_key=self.adspower_api_key.get()
        self._background(
            lambda:AdsPowerClient(base,api_key=api_key).list_groups(),
            lambda groups:messagebox.showinfo('成功',f'AdsPower API 連線與授權成功，讀取到 {len(groups)} 個群組'),
            'API 測試失敗',
        )
    def load_profiles(self):
        gid=self.groups[self.group.current()][0] if self.groups else '0';base=self.vars['adspower_base_url'].get();api_key=self.adspower_api_key.get()
        def done(profiles):self.profiles=sort_profiles_by_number(profiles);self.apply_filter();self.logger.info('讀取到 %d 個環境',len(profiles))
        self._background(lambda:AdsPowerClient(base,api_key=api_key).list_profiles(gid),done)
    def apply_filter(self):
        q=self.search.get().strip();self.filtered=[p for p in self.profiles if profile_matches_search(p,q)];self.list.delete(0,'end');[self.list.insert('end',p.name) for p in self.filtered]
    def settings(self):
        d=self.s.copy()
        for k,v in self.vars.items():
            x=v.get();d[k]=int(x) if k in {'comments_per_profile','group_comment_max_scrolls','max_replies','max_scrolls','threads','cycles','cycle_wait_minutes','notification_wait_seconds'} else x
        d.update(enable_group_comment_task=self.enable_group_comment.get(),enable_notification_task=self.enable_notification.get(),sort_order=self.sort.get(),search_mode='contains',only_unread=self.only_unread.get(),new_section_only=self.new_only.get(),process_replies=self.process_replies.get(),process_mentions=self.process_mentions.get(),telegram_enabled=self.tg.get(),auto_reply_enabled=self.auto_reply.get(),close_browser=self.close.get(),shuffle=False,delete_verified_profile=self.delete_verified.get(),delete_group_url_after_success=self.delete_group_after_success.get(),author_dedupe_scope=AUTHOR_DEDUPE_LABELS.get(self.author_dedupe.get(),'group'),group_comment_media_mode=MEDIA_MODE_LABELS.get(self.media_mode.get(),'none'));save_settings(d);d['adspower_api_key']=self.adspower_api_key.get().strip();return d
    def selected(self):
        indices=list(self.list.curselection())
        if not indices:return []
        return sort_profiles_by_number(self.filtered[i] for i in indices)
    def start_run(self):
        ps=self.selected()
        if not ps:return messagebox.showwarning('提醒','請先選擇環境')
        if not self.enable_group_comment.get() and not self.enable_notification.get():return messagebox.showwarning('提醒','請至少選擇一種執行任務')
        if self.enable_group_comment.get():
            try:groups=load_group_urls()
            except Exception as exc:
                self.logger.warning('公開留言啟動前檢查失敗：%s',exc)
                self.status.set('無法開始：group.txt 沒有可用網址')
                return messagebox.showwarning('提醒',str(exc))
            try:comments=load_text_lines(self.group_comment_file_var.get())
            except Exception as exc:
                self.logger.warning('公開留言文案檢查失敗：%s',exc)
                self.status.set('無法開始：公開留言文案不可用')
                return messagebox.showwarning('提醒',str(exc))
            try:
                media=MediaPool.from_settings({
                    'group_comment_media_mode':MEDIA_MODE_LABELS.get(self.media_mode.get(),'none'),
                    'group_comment_random_media_dir':self.random_media_dir_var.get(),
                    'group_comment_fixed_media_file':self.fixed_media_file_var.get(),
                })
            except Exception as exc:
                self.logger.warning('公開留言媒體檢查失敗：%s',exc)
                self.status.set('無法開始：公開留言媒體不可用')
                return messagebox.showwarning('提醒',str(exc))
            self.logger.info('啟動前檢查通過：group.txt=%d 個網址｜公開留言文案=%d 則｜媒體模式=%s｜相片=%d｜影片=%d',len(groups),len(comments),media.mode,media.photo_count,media.video_count)
        if self.enable_notification.get() and self.auto_reply.get() and not Path(self.customer_reply_file_var.get()).exists():return messagebox.showwarning('提醒','找不到選擇的客戶回覆文案檔')
        if self.enable_notification.get() and self.auto_reply.get() and not self.vars['telegram_account'].get().strip():return messagebox.showwarning('提醒','請填寫 Telegram 帳號')
        if self.engine and self.engine.running:return messagebox.showinfo('提醒','任務正在執行')
        if self.delete_verified.get() and not self.adspower_api_key.get().strip():return messagebox.showwarning('提醒','啟用刪除環境時，必須先輸入 AdsPower API Key')
        if self.delete_verified.get() and not messagebox.askyesno(
            '高風險刪除確認',
            '已啟用「驗證／停權／睡眠／登入後刪除 AdsPower 環境」。\n\n'
            '偵測到真人驗證、帳號停權、Facebook Sleep Mode 或 Facebook 登入頁時，'
            '程式會先依狀態更名，接著關閉並刪除該環境。\n'
            '代理驗證／Tunnel／重試後仍逾時：只會更名為「IP到期＋原名稱」並關閉，不會刪除。\n\n'
            f'本次選擇 {len(ps)} 個環境。確定允許執行刪除嗎？',
        ):return
        s=self.settings()
        order=' → '.join(p.name for p in ps[:30])
        if len(ps)>30:order+=f' → …（另有 {len(ps)-30} 個）'
        self.logger.info('本次已選擇 %d 個環境；優先順序：%s',len(ps),order)
        self.pb['maximum']=len(ps);self.pb['value']=0;self.engine=MonitorEngine(s,ps,self.logger,self.progress);threading.Thread(target=self._run_engine,daemon=True).start();self.status.set(f'執行中：{ps[0].name}')
    def _run_engine(self):
        try:self.engine.run()
        except Exception as exc:
            self.logger.exception('背景執行失敗')
            message=str(exc)
            self.after(0,lambda:self.status.set(f'執行失敗：{message}（請查看 LOG）'))
        else:
            stopped=self.engine.stop.is_set()
            self.after(0,lambda:self.status.set('已停止' if stopped else '執行完成'))
    def progress(self,p,r):
        self.after(0,lambda:(self.pb.step(1),self.status.set(f'{p.name}：{r.status}｜發現 {r.found}｜回報 {r.reported}｜失敗 {r.failed}')))
    def stop_run(self):
        if self.engine:self.engine.request_stop();self.status.set('停止中')
    def test_telegram(self):
        t,c1,c2=telegram_credentials()
        self._background(lambda:TelegramReporter(t,TelegramTargets(c1,c2),True).test_incoming(),lambda _:messagebox.showinfo('成功','收到留言群測試訊息已送出'),'失敗')

    def test_reply_telegram(self):
        t,c1,c2=telegram_credentials()
        self._background(lambda:TelegramReporter(t,TelegramTargets(c1,c2),True).test_replied(),lambda _:messagebox.showinfo('成功','已回覆群測試訊息已送出'),'失敗')

def run():App().mainloop()
