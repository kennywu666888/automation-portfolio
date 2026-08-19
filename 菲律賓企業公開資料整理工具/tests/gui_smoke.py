import os
import sys
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from 圖形介面 import MainWindow

app = QApplication([])
window = MainWindow()
assert window.windowTitle()
assert window.table.columnCount() == 11
QTimer.singleShot(100, window.close)
app.exec()
print("GUI_SMOKE_OK")
