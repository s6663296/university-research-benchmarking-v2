"""安裝版的進入點：沒有終端機視窗，伺服器在背景執行，從系統匣圖示開啟或結束。"""

import ctypes
import sys
import threading
import time
import traceback
import webbrowser

from app.__main__ import already_running
from app.config import HOME, Settings
from app.web.deps import WEB_DIR

TITLE = "員額小組論文指標自動化系統"
LOG = HOME / "logs" / "app.log"


def _message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)  # MB_ICONERROR


def _failed() -> None:
    _message(f"系統無法啟動。\n\n請把這個檔案寄給開發人員：\n{LOG.resolve()}")


def run() -> None:
    try:
        _run()
    except Exception:
        traceback.print_exc()
        _failed()
        sys.exit(1)


def _run() -> None:
    settings = Settings()
    url = f"http://{settings.host}:{settings.port}"
    if already_running(settings.host, settings.port):
        # 重複點了捷徑：系統已經在背景執行，只開瀏覽器（也不能覆寫執行中那份紀錄檔）
        webbrowser.open(url)
        return

    # 沒有視窗時 stdout/stderr 是 None，訊息改寫進紀錄檔，出錯時才查得到原因
    LOG.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(LOG, "w", encoding="utf-8", buffering=1)

    import pystray
    import uvicorn
    from PIL import Image

    from app.main import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(settings), host=settings.host, port=settings.port,
                                           access_log=False, use_colors=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def open_browser(*_):
        webbrowser.open(url)

    def quit_(icon, _item):
        server.should_exit = True
        icon.stop()

    def wait_for_server(icon):
        icon.visible = True
        while not server.started:
            if not thread.is_alive():  # 例如連接埠被占用、資料庫無法開啟
                icon.stop()
                _failed()
                return
            time.sleep(0.2)
        open_browser()
        icon.notify("系統在背景執行中。要結束時，在右下角的這個圖示上按右鍵，選「結束」。", TITLE)

    icon = pystray.Icon("ResearchBenchmarking", Image.open(WEB_DIR / "static" / "app.ico"), TITLE,
                        menu=pystray.Menu(pystray.MenuItem("開啟系統", open_browser, default=True),
                                          pystray.MenuItem("結束", quit_)))
    icon.run(setup=wait_for_server)
    server.should_exit = True
    thread.join(timeout=10)
