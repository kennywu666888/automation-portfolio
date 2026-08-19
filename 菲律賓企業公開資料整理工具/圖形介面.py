import json
import logging
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QGroupBox,QLabel,QLineEdit,
 QComboBox,QCheckBox,QSpinBox,QDoubleSpinBox,QPushButton,QTableWidget,QTableWidgetItem,QTextEdit,QFileDialog,QMessageBox,QTabWidget,QListView,QListWidget,QListWidgetItem)
from database import Database
from exporter import Exporter
from worker import CrawlWorker
from scrapers.businesslist import BusinessListScraper
from 瀏覽器 import open_businesslist_login
from category_catalog import load_category_catalog

CATEGORY_TREE,CATEGORY_URLS=load_category_catalog()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.root=Path(__file__).parent;self.db=Database();self.exporter=Exporter(self.db);self.worker=None
        self.setWindowTitle("Philippines Construction Company Data Collector");self.resize(1450,900);self._logging();self._ui();self._load_settings();self._reload_table()
    def _logging(self):
        path=self.root/"logs"/f"{datetime.now():%Y-%m-%d}.log";path.parent.mkdir(exist_ok=True)
        logging.basicConfig(filename=path,level=logging.INFO,encoding="utf-8",format="%(asctime)s %(levelname)s %(message)s")
    def _ui(self):
        self.setStyleSheet("""QMainWindow{background:#f4f7fb} QGroupBox{font-weight:600;border:1px solid #d9e2ef;border-radius:10px;margin-top:10px;padding:12px;background:white} QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 5px} QPushButton{background:#2563eb;color:white;border:0;border-radius:6px;padding:7px 14px;font-weight:600} QPushButton:hover{background:#1d4ed8} QPushButton:disabled{background:#94a3b8} QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit{border:1px solid #cbd5e1;border-radius:5px;padding:5px;background:white} QComboBox:focus{background:#dbeafe;color:#1d4ed8;border:2px solid #2563eb;font-weight:700} QComboBox QAbstractItemView{background:white;color:#111827;selection-background-color:#2563eb;selection-color:white;outline:0} QComboBox QAbstractItemView::item:selected{background:#2563eb;color:white} QCheckBox{padding:5px;border:1px solid transparent;border-radius:5px} QCheckBox:checked{background:#dbeafe;color:#1d4ed8;border:1px solid #2563eb;font-weight:700} QCheckBox::indicator{width:17px;height:17px;border:1px solid #94a3b8;border-radius:3px;background:white} QCheckBox::indicator:checked{background:#2563eb;border:2px solid #1d4ed8} QTableWidget{background:white;border:1px solid #d9e2ef;gridline-color:#e5e7eb} QHeaderView::section{background:#eaf1ff;padding:6px;border:0;font-weight:600}""")
        central=QWidget();self.setCentralWidget(central);outer=QVBoxLayout(central)
        top=QHBoxLayout();outer.addLayout(top);settings=QGroupBox("搜尋設定");top.addWidget(settings,3);sg=QGridLayout(settings)
        self.sources={};source_bar=QHBoxLayout();sg.addLayout(source_bar,0,0,1,2)
        for name in ("BusinessList Philippines","Filbuild"):
            cb=QCheckBox(name);cb.setChecked(True);cb.stateChanged.connect(self._source_checked);self.sources[name]=cb;source_bar.addWidget(cb,1)
        self.category_source=QComboBox();self.category_source.addItems(CATEGORY_TREE.keys());self.category_source.currentTextChanged.connect(self._load_major_categories)
        self.category_selections={};self.category_major=QListWidget();self.category_major.setMinimumHeight(105);self.category_major.setMaximumHeight(145);self.category_major.itemChanged.connect(self._major_changed)
        self.category=QListWidget();self.category.setMinimumHeight(145);self.category.setMaximumHeight(195);self.category.itemChanged.connect(self._category_changed)
        self.major_all=QCheckBox("全選大分類");self.major_all.stateChanged.connect(self._toggle_all_majors)
        self.category_all=QCheckBox("全選小分類");self.category_all.stateChanged.connect(self._toggle_all_categories)
        self.major_box=QWidget();major_layout=QVBoxLayout(self.major_box);major_layout.setContentsMargins(0,0,0,0);major_layout.addWidget(self.major_all);major_layout.addWidget(self.category_major)
        category_box=QWidget();category_layout=QVBoxLayout(category_box);category_layout.setContentsMargins(0,0,0,0);category_layout.addWidget(self.category_all);category_layout.addWidget(self.category)
        for combo in (self.category_source,):
            view=QListView();view.setStyleSheet("QListView{background:white;color:#111827;border:1px solid #64748b} QListView::item{padding:7px} QListView::item:selected{background:#2563eb;color:white} QListView::item:hover{background:#bfdbfe;color:#111827}");combo.setView(view)
        self.maximum=QSpinBox();self.maximum.setRange(0,1000000);self.maximum.setSpecialValueText("0（不限制）")
        sg.addWidget(QLabel("分類來源"),1,0);sg.addWidget(self.category_source,1,1)
        self.major_label=QLabel("大分類");sg.addWidget(self.major_label,2,0);sg.addWidget(self.major_box,2,1)
        sg.addWidget(QLabel("小分類"),3,0);sg.addWidget(category_box,3,1)
        sg.addWidget(QLabel("最大公司數"),4,0);sg.addWidget(self.maximum,4,1)
        self._load_major_categories(self.category_source.currentText())
        options=QGroupBox("執行選項");top.addWidget(options,2);og=QGridLayout(options)
        self.threads=QSpinBox();self.threads.setRange(1,10);self.threads.setValue(3);self.min_delay=QDoubleSpinBox();self.min_delay.setRange(.2,60);self.min_delay.setValue(1.5);self.max_delay=QDoubleSpinBox();self.max_delay.setRange(.2,120);self.max_delay.setValue(3);self.visible=QCheckBox("顯示瀏覽器");self.visible.setChecked(True);self.exclude=QTextEdit();self.exclude.setPlaceholderText("排除公司名稱包含（每行一個）");self.exclude.setMaximumHeight(65)
        for r,(label,w) in enumerate((("工作執行緒",self.threads),("最小間隔（秒）",self.min_delay),("最大間隔（秒）",self.max_delay))):og.addWidget(QLabel(label),r,0);og.addWidget(w,r,1)
        og.addWidget(self.visible,3,0,1,2);og.addWidget(self.exclude,4,0,1,2);self.login_status=QLabel("BusinessList 狀態：未接管");og.addWidget(self.login_status,5,0,1,2)
        login_open=QPushButton("① 開啟人工登入瀏覽器");login_open.clicked.connect(self.open_login_browser);og.addWidget(login_open,6,0,1,2)
        login_takeover=QPushButton("② 我已登入，接管");login_takeover.clicked.connect(self.test_login);og.addWidget(login_takeover,7,0,1,2)
        bar=QHBoxLayout();outer.addLayout(bar);self.start_btn=QPushButton("開始搜尋");self.pause_btn=QPushButton("暫停");self.resume_btn=QPushButton("繼續");self.stop_btn=QPushButton("停止")
        for b,fn in ((self.start_btn,self.start),(self.pause_btn,self.pause),(self.resume_btn,self.resume),(self.stop_btn,self.stop)):b.clicked.connect(fn);bar.addWidget(b)
        for name in self.sources:
            b=QPushButton("測試 "+name.replace(" Philippines",""));b.clicked.connect(lambda _,n=name:self.start(test_source=n));bar.addWidget(b)
        self.stats={};statsbox=QGroupBox("即時統計");outer.addWidget(statsbox);sl=QHBoxLayout(statsbox)
        for key,label in (("source","目前來源"),("discovered","已發現公司"),("completed","已完成公司"),("emails","不重複 Email"),("phones","找到電話"),("no_email","沒有 Email"),("duplicates","重複公司"),("errors","錯誤")):
            w=QLabel(f"{label}：0" if key!="source" else f"{label}：-");self.stats[key]=(label,w);sl.addWidget(w)
        filt=QHBoxLayout();outer.addLayout(filt);self.filter_text=QLineEdit();self.filter_text.setPlaceholderText("搜尋目前資料表");self.filter_text.textChanged.connect(self.apply_filter);self.email_only=QCheckBox("Email Only");self.email_only.stateChanged.connect(self.apply_filter);self.phone_only=QCheckBox("有電話");self.phone_only.stateChanged.connect(self.apply_filter);self.no_email=QCheckBox("無 Email");self.no_email.stateChanged.connect(self.apply_filter);filt.addWidget(self.filter_text);filt.addWidget(self.email_only);filt.addWidget(self.phone_only);filt.addWidget(self.no_email)
        headers=["公司名稱","分類","Email","電話","手機","城市","Province","網站","來源","完整度","狀態"]
        self.table=QTableWidget(0,len(headers));self.table.setHorizontalHeaderLabels(headers);self.table.setSortingEnabled(True);self.table.horizontalHeader().setStretchLastSection(True);outer.addWidget(self.table,4)
        bottom=QHBoxLayout();outer.addLayout(bottom);log_group=QGroupBox("執行 LOG");log_layout=QVBoxLayout(log_group);self.task_status=QLabel("狀態：待命");self.task_status.setStyleSheet("font-size:14px;font-weight:700;color:#334155;padding:4px");self.logbox=QTextEdit();self.logbox.setReadOnly(True);self.logbox.setMinimumHeight(90);self.logbox.setMaximumHeight(135);log_layout.addWidget(self.task_status);log_layout.addWidget(self.logbox);bottom.addWidget(log_group,4)
        exports=QVBoxLayout();bottom.addLayout(exports,1)
        for label,kind in (("匯出全部","all"),("匯出 Excel","excel"),("匯出完整 TXT","full"),("匯出 Email TXT","email"),("匯出電話 TXT","phone")):
            b=QPushButton(label);b.clicked.connect(lambda _,k=kind:self.export(k));exports.addWidget(b)
        self._set_running(False)
    def log(self,text):
        safe=str(text);self.logbox.append(f"[{datetime.now():%H:%M:%S}] {safe}");logging.info(safe)
    def config(self):
        categories_by_source={}
        for name,groups in CATEGORY_TREE.items():
            saved=self.category_selections.get(name,{}).get("categories",set())
            categories_by_source[name]=sorted(saved) or [next(iter(groups.values()))[0]]
        return {"sources":[n for n,c in self.sources.items() if c.isChecked()],"keyword":"","categories_by_source":categories_by_source,"city":"","max_results":self.maximum.value(),"min_delay":self.min_delay.value(),"max_delay":max(self.min_delay.value(),self.max_delay.value()),"exclude":self.exclude.toPlainText()}
    def start(self,checked=False,test_source=None):
        if self.worker and self.worker.isRunning():return
        cfg=self.config()
        if test_source:
            cfg["sources"]=[test_source];cfg["max_results"]=min(cfg["max_results"] or 3,5)
            major=next(iter(CATEGORY_TREE[test_source]));cfg["categories_by_source"][test_source]=[CATEGORY_TREE[test_source][major][0]]
        if not cfg["sources"]:QMessageBox.warning(self,"沒有來源","請至少勾選一個資料來源。");return
        if not any(cfg["categories_by_source"].get(s) for s in cfg["sources"]):QMessageBox.warning(self,"沒有分類","請至少勾選一個小分類。");return
        self._save_settings();self.worker=CrawlWorker(self.db,cfg);self.worker.message.connect(self.log);self.worker.failed.connect(self.task_failed);self.worker.company.connect(self.add_company);self.worker.progress.connect(self.update_stats);self.worker.done.connect(self.finished);self.worker.start();self._set_running(True);self._set_status("執行中","#1d4ed8")
    def pause(self):
        if self.worker:self.worker.pause();self.log("任務已暫停，已完成資料仍保存在 SQLite");self._set_status("已暫停","#a16207")
    def resume(self):
        if self.worker:self.worker.resume();self.log("任務繼續");self._set_status("執行中","#1d4ed8")
    def stop(self):
        if self.worker:self.worker.stop();self.log("正在安全停止；已完成資料不會消失");self._set_status("停止中","#a16207")
    def task_failed(self,text):self.log("錯誤："+text);self._set_status("發生錯誤，仍在處理","#b91c1c")
    def _set_status(self,text,color):self.task_status.setText("狀態："+text);self.task_status.setStyleSheet(f"font-size:14px;font-weight:700;color:{color};padding:4px")
    def finished(self):
        self._set_running(False);status=self.worker.result_status if self.worker else "已完成";color="#15803d" if status=="已完成" else ("#a16207" if status=="已停止" else "#b91c1c");self._set_status(status,color);self.log("任務結束："+status);self._reload_table()
    def _set_running(self,v):self.start_btn.setEnabled(not v);self.pause_btn.setEnabled(v);self.resume_btn.setEnabled(v);self.stop_btn.setEnabled(v)
    def update_stats(self,data):
        for key,val in data.items():
            if key in self.stats:self.stats[key][1].setText(f"{self.stats[key][0]}：{val}")
    def add_company(self,c,duplicate):
        if duplicate:return
        self.table.setSortingEnabled(False);r=self.table.rowCount();self.table.insertRow(r);vals=[c.company_name,c.category,"; ".join(c.emails),"; ".join(c.phones),"; ".join(c.mobiles),c.city,c.province,c.website,"; ".join(c.sources),f"{c.completeness}%",c.status]
        for i,v in enumerate(vals):self.table.setItem(r,i,QTableWidgetItem(str(v)))
        self.table.setSortingEnabled(True);self.apply_filter()
    def _reload_table(self):
        self.table.setRowCount(0)
        for row in self.db.all_companies():
            from models import Company
            c=Company(row["company_name"],row["category"] or "",(row["email"] or "").split("; ") if row["email"] else [],(row["phone"] or "").split("; ") if row["phone"] else [],(row["mobile"] or "").split("; ") if row["mobile"] else [],address=row["address"] or "",city=row["city"] or "",province=row["province"] or "",website=row["website"] or "",sources=(row["source"] or "").split("; "));self.add_company(c,False)
    def apply_filter(self):
        needle=self.filter_text.text().lower()
        for r in range(self.table.rowCount()):
            vals=[self.table.item(r,c).text() if self.table.item(r,c) else "" for c in range(self.table.columnCount())]
            show=(not needle or any(needle in x.lower() for x in vals)) and (not self.email_only.isChecked() or bool(vals[2])) and (not self.phone_only.isChecked() or bool(vals[3] or vals[4])) and (not self.no_email.isChecked() or not vals[2]);self.table.setRowHidden(r,not show)
    def test_login(self):
        self.login_status.setText("BusinessList 狀態：檢查中…")
        scraper=BusinessListScraper(.2,.3,log=self.log);ok,msg=scraper.login(self.visible.isChecked());self.login_status.setText("BusinessList 狀態："+("登入成功" if ok else "登入失敗"));self.log(msg)
    def open_login_browser(self):
        ok,msg=open_businesslist_login();self.login_status.setText("BusinessList 狀態：等待人工登入" if ok else "BusinessList 狀態：瀏覽器開啟失敗");self.log(msg)
    def _source_checked(self):
        if not hasattr(self,"category_source"):return
        checked=[name for name,box in self.sources.items() if box.isChecked()]
        if len(checked)==1:self.category_source.setCurrentText(checked[0])
    def _load_major_categories(self,source):
        groups=CATEGORY_TREE.get(source,{});has_major=any(groups.keys());self.major_label.setVisible(has_major);self.major_box.setVisible(has_major)
        state=self.category_selections.setdefault(source,{"majors":set(),"categories":set()})
        if has_major and not state["majors"]:state["majors"]={next(iter(groups))}
        self.category_major.blockSignals(True);self.category_major.clear()
        for label in groups:
            if not label:continue
            item=QListWidgetItem(label);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked if label in state["majors"] else Qt.CheckState.Unchecked);self.category_major.addItem(item)
        self.category_major.blockSignals(False);self._load_subcategories()
    def _load_subcategories(self):
        source=self.category_source.currentText();groups=CATEGORY_TREE.get(source,{});state=self.category_selections.setdefault(source,{"majors":set(),"categories":set()})
        labels=[]
        for major,values in groups.items():
            if not major or major in state["majors"]:
                labels.extend(x for x in values if x not in labels)
        state["categories"]&=set(labels)
        if not state["categories"] and labels:state["categories"]={labels[0]}
        self.category.blockSignals(True);self.category.clear()
        for label in labels:
            item=QListWidgetItem(label);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked if label in state["categories"] else Qt.CheckState.Unchecked);self.category.addItem(item)
        self.category.blockSignals(False);self._sync_all_checks()
    def _major_changed(self):
        source=self.category_source.currentText();self.category_selections.setdefault(source,{"majors":set(),"categories":set()})["majors"]={self.category_major.item(i).text() for i in range(self.category_major.count()) if self.category_major.item(i).checkState()==Qt.CheckState.Checked};self._load_subcategories()
    def _category_changed(self):
        source=self.category_source.currentText();self.category_selections.setdefault(source,{"majors":set(),"categories":set()})["categories"]={self.category.item(i).text() for i in range(self.category.count()) if self.category.item(i).checkState()==Qt.CheckState.Checked};self._sync_all_checks()
    def _toggle_all_majors(self,state):
        self.category_major.blockSignals(True)
        for i in range(self.category_major.count()):self.category_major.item(i).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self.category_major.blockSignals(False);self._major_changed()
    def _toggle_all_categories(self,state):
        self.category.blockSignals(True)
        for i in range(self.category.count()):self.category.item(i).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self.category.blockSignals(False);self._category_changed()
    def _sync_all_checks(self):
        for box,widget in ((self.major_all,self.category_major),(self.category_all,self.category)):
            box.blockSignals(True);box.setChecked(widget.count()>0 and all(widget.item(i).checkState()==Qt.CheckState.Checked for i in range(widget.count())));box.blockSignals(False)
    def export(self,kind):
        folder=QFileDialog.getExistingDirectory(self,"選擇輸出資料夾",self.db.get_setting("output_folder",str(self.root/"output")))
        if not folder:return
        self.db.set_setting("output_folder",folder)
        try:
            paths=self.exporter.export_all(folder) if kind=="all" else ([self.exporter.export_excel(folder)] if kind=="excel" else self.exporter.export_txt(folder,[kind]));QMessageBox.information(self,"匯出完成","已建立：\n"+"\n".join(str(p) for p in paths))
        except Exception as exc:self.log(f"匯出失敗：{exc}");QMessageBox.critical(self,"匯出失敗",str(exc))
    def _save_settings(self):
        data={"category_source":self.category_source.currentText(),"category_selections":{source:{"majors":sorted(values["majors"]),"categories":sorted(values["categories"])} for source,values in self.category_selections.items()},"threads":self.threads.value(),"min_delay":self.min_delay.value(),"max_delay":self.max_delay.value(),"visible":self.visible.isChecked(),"sources":{k:v.isChecked() for k,v in self.sources.items()}}
        (self.root/"data"/"settings.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    def _load_settings(self):
        path=self.root/"data"/"settings.json"
        if not path.exists():return
        try:
            d=json.loads(path.read_text(encoding="utf-8"));self.category_selections={source:{"majors":set(values.get("majors",[])),"categories":set(values.get("categories",[]))} for source,values in d.get("category_selections",{}).items()};self.category_source.setCurrentText(d.get("category_source","BusinessList Philippines"));self._load_major_categories(self.category_source.currentText());self.threads.setValue(d.get("threads",3));self.min_delay.setValue(d.get("min_delay",1.5));self.max_delay.setValue(d.get("max_delay",3));self.visible.setChecked(d.get("visible",True))
            for k,v in d.get("sources",{}).items():
                if k in self.sources:self.sources[k].setChecked(v)
            if not any(box.isChecked() for box in self.sources.values()):
                for box in self.sources.values():box.setChecked(True)
        except Exception as exc:self.log(f"設定檔讀取失敗：{exc}")
    def closeEvent(self,event):
        self._save_settings()
        if self.worker and self.worker.isRunning():self.worker.stop();self.worker.wait(3000)
        event.accept()
