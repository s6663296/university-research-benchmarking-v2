"""python -m app：啟動伺服器並開啟瀏覽器。"""

import socket
import sys
import threading
import traceback
import webbrowser

from app.config import Settings


def already_running(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    settings = Settings()
    url = f"http://{settings.host}:{settings.port}"
    if already_running(settings.host, settings.port):
        # 重複點了捷徑：系統已經在跑，只開瀏覽器
        webbrowser.open(url)
        return
    import uvicorn  # 載入整個系統要幾秒，重複點捷徑時用不到

    from app.main import create_app

    print(f"系統已啟動：{url}\n關閉這個視窗即結束系統。\n")
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


def run_frozen() -> None:
    """安裝版的進入點（packaging/launcher.py）：出錯時視窗會直接關掉，先留住錯誤訊息。"""
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("\n系統發生錯誤，請把上面的訊息複製給開發人員。按 Enter 關閉視窗。")
        sys.exit(1)


if __name__ == "__main__":
    main()
