"""產生系統圖示 app/web/static/app.ico（exe、捷徑、系統匣與瀏覽器分頁共用）。

用法：python packaging/make_icon.py
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "logo.png"
TARGET = ROOT / "app" / "web" / "static" / "app.ico"
SIZE = 256


def make_icon(source_path: Path = SOURCE, target_path: Path = TARGET) -> None:
    src = Image.open(source_path).convert("RGBA")

    # 計算在正方形畫布中的合適大小與邊距，保留原圖比例
    # 預留約 4% 邊距，避免邊緣在 Windows 捷徑或系統匣被裁切
    canvas_size = 1024
    padding = 40
    target_w = canvas_size - 2 * padding
    target_h = int(src.height * (target_w / src.width))
    if target_h > canvas_size - 2 * padding:
        target_h = canvas_size - 2 * padding
        target_w = int(src.width * (target_h / src.height))

    scaled = src.resize((target_w, target_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    offset_x = (canvas_size - target_w) // 2
    offset_y = (canvas_size - target_h) // 2
    canvas.paste(scaled, (offset_x, offset_y), scaled)

    # 產生多種尺寸的圖示
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    canvas.resize((SIZE, SIZE), Image.LANCZOS).save(target_path, sizes=sizes)
    print(f"wrote {target_path}")


if __name__ == "__main__":
    make_icon()

