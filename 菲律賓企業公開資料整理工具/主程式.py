import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for folder in ("data", "logs", "output", "database"):
    (ROOT / folder).mkdir(exist_ok=True)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor,QPalette
from 圖形介面 import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Philippines Construction Company Data Collector")
    app.setStyle("Fusion")
    palette=app.palette();palette.setColor(QPalette.ColorRole.Highlight,QColor("#2563eb"));palette.setColor(QPalette.ColorRole.HighlightedText,QColor("#ffffff"));app.setPalette(palette)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
