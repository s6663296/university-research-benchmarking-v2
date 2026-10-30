"""python -m app：啟動伺服器並開啟瀏覽器。"""

import threading
import webbrowser

import uvicorn

from app.config import Settings
from app.main import create_app


def main() -> None:
    settings = Settings()
    url = f"http://{settings.host}:{settings.port}"
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
