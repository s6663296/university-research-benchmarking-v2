"""產生系統圖示 app/web/static/app.ico（exe、捷徑、系統匣與瀏覽器分頁共用）。

用法：python packaging/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

TARGET = Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "app.ico"
PRIMARY = (31, 92, 153)  # app.css 的 --primary
SIZE = 256


def draw() -> Image.Image:
    scale = 4  # 先畫大圖再縮小，邊緣才平滑
    s = SIZE * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.2, fill=PRIMARY)
    # 三根由低到高的長條：排名與指標
    base, width, gap = s * 0.78, s * 0.16, s * 0.07
    left = (s - 3 * width - 2 * gap) / 2
    for i, height in enumerate((0.26, 0.40, 0.56)):
        x = left + i * (width + gap)
        d.rounded_rectangle((x, base - s * height, x + width, base), radius=width * 0.18, fill="white")
    return img.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    draw().save(TARGET, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {TARGET}")
