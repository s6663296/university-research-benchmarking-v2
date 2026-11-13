"""python -m app：啟動伺服器並開啟瀏覽器。"""

import socket
import threading
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


if __name__ == "__main__":
    main()
