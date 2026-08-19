from pathlib import Path
import os,subprocess
def open_folder(path):
    p=str(Path(path).resolve())
    if os.name=='nt':os.startfile(p)
    else:subprocess.Popen(['xdg-open',p])
