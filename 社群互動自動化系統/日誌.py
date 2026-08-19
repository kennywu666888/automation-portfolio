import logging
import threading
from datetime import datetime
from pathlib import Path


BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
PROFILE_DIR = LOG_DIR / "profiles"
LOG_DIR.mkdir(exist_ok=True)
PROFILE_DIR.mkdir(exist_ok=True)


class ImmediateUtf8FileHandler(logging.Handler):
    """Append and close on every record so a crash cannot leave a 0-byte LOG."""

    def __init__(self, filename):
        super().__init__()
        self.baseFilename = str(Path(filename).resolve())
        self._write_lock = threading.RLock()
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        Path(self.baseFilename).touch(exist_ok=True)

    def emit(self, record):
        try:
            message = self.format(record)
            with self._write_lock:
                with Path(self.baseFilename).open(
                    "a", encoding="utf-8", newline=""
                ) as stream:
                    stream.write(message + "\n")
                    stream.flush()
        except Exception:
            self.handleError(record)


def _clear_handlers(logger):
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def setup_logger(callback=None):
    logging.disable(logging.NOTSET)
    logger = logging.getLogger("monitor")
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _clear_handlers(logger)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    log_path = LOG_DIR / f"monitor_{datetime.now():%Y%m%d}.log"
    file_handler = ImmediateUtf8FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if callback:
        class QueueHandler(logging.Handler):
            def emit(self, record):
                callback(formatter.format(record))

        logger.addHandler(QueueHandler())
    logger.info("RC19 LOG 系統已啟動｜UTF-8 即時寫入")
    if not log_path.exists() or log_path.stat().st_size == 0:
        raise RuntimeError(f"LOG 無法寫入：{log_path}")
    return logger


def profile_logger(name):
    safe = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in name
    )[:80]
    # Profile logs keep their own UTF-8 file and also propagate to the monitor
    # logger.  The latter is what feeds the GUI's lower LOG pane.
    logger = logging.getLogger(f"monitor.profile.{safe}.{datetime.now().timestamp()}")
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.propagate = True
    profile_path = PROFILE_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{safe}.log"
    file_handler = ImmediateUtf8FileHandler(profile_path)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)
    logger.info("環境 LOG 已建立：%s", name)
    if not profile_path.exists() or profile_path.stat().st_size == 0:
        raise RuntimeError(f"環境 LOG 無法寫入：{profile_path}")
    return logger, Path(file_handler.baseFilename)
