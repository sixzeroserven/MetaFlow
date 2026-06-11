import math
import random
import struct
import zlib
from pathlib import Path


class Canvas:
    def __init__(self, width: int = 1024, height: int = 1024) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray([255, 255, 255] * width * height)

    def blend_pixel(self, x: int, y: int, color: tuple[int, int, int], alpha: float = 1.0) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        i = (y * self.width + x) * 3
        a = max(0.0, min(1.0, alpha))
        self.pixels[i] = int(self.pixels[i] * (1 - a) + color[0] * a)
        self.pixels[i + 1] = int(self.pixels[i + 1] * (1 - a) + color[1] * a)
        self.pixels[i + 2] = int(self.pixels[i + 2] * (1 - a) + color[2] * a)

    def background_gradient(self, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
        for y in range(self.height):
            t = y / max(1, self.height - 1)
            color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
            for x in range(self.width):
                self.blend_pixel(x, y, color, 1.0)

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int], alpha: float = 1.0) -> None:
        for y in range(max(0, y0), min(self.height, y1)):
            for x in range(max(0, x0), min(self.width, x1)):
                self.blend_pixel(x, y, color, alpha)

    def circle(self, cx: float, cy: float, r: float, color: tuple[int, int, int], alpha: float = 1.0) -> None:
        r2 = r * r
        for y in range(int(cy - r), int(cy + r) + 1):
            for x in range(int(cx - r), int(cx + r) + 1):
                dx, dy = x - cx, y - cy
                d2 = dx * dx + dy * dy
                if d2 <= r2:
                    edge = r - math.sqrt(d2)
                    a = alpha if edge >= 1 else alpha * max(0, edge)
                    self.blend_pixel(x, y, color, a)

    def ellipse(
        self,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        color: tuple[int, int, int],
        alpha: float = 1.0,
        angle: float = 0.0,
    ) -> None:
        ca, sa = math.cos(angle), math.sin(angle)
        margin_x = abs(rx * ca) + abs(ry * sa) + 3
        margin_y = abs(rx * sa) + abs(ry * ca) + 3
        for y in range(int(cy - margin_y), int(cy + margin_y) + 1):
            for x in range(int(cx - margin_x), int(cx + margin_x) + 1):
                dx, dy = x - cx, y - cy
                xr = dx * ca + dy * sa
                yr = -dx * sa + dy * ca
                val = (xr * xr) / (rx * rx) + (yr * yr) / (ry * ry)
                if val <= 1:
                    edge = 1 - val
                    a = alpha if edge > 0.03 else alpha * edge / 0.03
                    self.blend_pixel(x, y, color, a)

    def line(self, x0: float, y0: float, x1: float, y1: float, color: tuple[int, int, int], width: float = 5) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0), 1))
        for i in range(steps + 1):
            t = i / steps
            self.circle(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width / 2, color)

    def polygon(self, points: list[tuple[float, float]], color: tuple[int, int, int], alpha: float = 1.0) -> None:
        ys = [p[1] for p in points]
        for y in range(max(0, int(min(ys))), min(self.height - 1, int(max(ys))) + 1):
            intersections = []
            for i, (x1, y1) in enumerate(points):
                x2, y2 = points[(i + 1) % len(points)]
                if (y1 <= y < y2) or (y2 <= y < y1):
                    intersections.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            intersections.sort()
            for start, end in zip(intersections[0::2], intersections[1::2]):
                for x in range(max(0, int(start)), min(self.width, int(end) + 1)):
                    self.blend_pixel(x, y, color, alpha)

    def save_png(self, path: str) -> str:
        raw = bytearray()
        for y in range(self.height):
            raw.append(0)
            raw.extend(self.pixels[y * self.width * 3 : (y + 1) * self.width * 3])

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        png += chunk(b"IEND", b"")

        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(png)
        return str(output)


def _draw_flower_basket(canvas: Canvas) -> None:
    # Hanging chains and basket.
    canvas.line(512, 190, 420, 475, (101, 88, 72), 7)
    canvas.line(512, 190, 604, 475, (101, 88, 72), 7)
    canvas.line(420, 475, 604, 475, (101, 88, 72), 8)
    canvas.ellipse(512, 565, 190, 90, (112, 75, 45), 1.0)
    canvas.ellipse(512, 520, 210, 95, (154, 101, 58), 1.0)
    canvas.ellipse(512, 493, 210, 45, (192, 132, 76), 1.0)

    for x in range(350, 680, 28):
        canvas.line(x, 470, x + 80, 625, (94, 59, 35), 5)
        canvas.line(x + 80, 470, x, 625, (129, 82, 48), 4)

    random.seed(12)
    flower_colors = [(195, 32, 49), (245, 245, 238), (37, 80, 160)]
    centers = [(512, 405)]
    for _ in range(58):
        centers.append((random.randint(325, 705), random.randint(285, 515)))
    for cx, cy in centers:
        color = flower_colors[(cx + cy) % len(flower_colors)]
        for i in range(5):
            angle = i * math.tau / 5
            canvas.ellipse(cx + math.cos(angle) * 15, cy + math.sin(angle) * 10, 18, 11, color, 0.95, angle)
        canvas.circle(cx, cy, 7, (246, 203, 77))

    # Cascading greenery.
    for _ in range(65):
        x = random.randint(310, 720)
        y = random.randint(420, 700)
        angle = random.uniform(-1.2, 1.2)
        canvas.ellipse(x, y, random.randint(15, 34), random.randint(5, 10), (61, 141, 80), 0.9, angle)


def generate_local_product_scene(
    output_path: str,
    post_content: str = "",
    product_context: str = "",
    use_cases: str = "",
) -> str:
    text = f"{post_content}\n{product_context}\n{use_cases}".lower()
    canvas = Canvas()
    canvas.background_gradient((232, 244, 255), (255, 238, 207))

    # Porch backdrop.
    canvas.rect(0, 650, 1024, 1024, (178, 127, 82), 1.0)
    for y in range(650, 1024, 54):
        canvas.line(0, y, 1024, y, (143, 95, 59), 3)
    canvas.rect(85, 250, 220, 650, (255, 255, 248), 0.75)
    canvas.rect(804, 250, 939, 650, (255, 255, 248), 0.75)
    canvas.line(220, 250, 220, 650, (160, 173, 184), 9)
    canvas.line(804, 250, 804, 650, (160, 173, 184), 9)
    canvas.rect(90, 610, 936, 650, (216, 222, 226), 1.0)

    # Patriotic bunting and soft holiday context.
    for x in [170, 325, 480, 635, 790]:
        canvas.line(x - 75, 220, x, 265, (44, 76, 150), 6)
        canvas.line(x, 265, x + 75, 220, (44, 76, 150), 6)
        canvas.ellipse(x - 38, 250, 34, 18, (196, 35, 51), 0.9)
        canvas.ellipse(x, 262, 34, 18, (248, 248, 240), 0.95)
        canvas.ellipse(x + 38, 250, 34, 18, (44, 76, 150), 0.9)

    if "flower" in text or "petunia" in text or "basket" in text:
        _draw_flower_basket(canvas)
    else:
        canvas.ellipse(512, 500, 185, 130, (230, 230, 230), 1.0)
        canvas.ellipse(512, 495, 150, 95, (90, 140, 210), 1.0)

    # Warm lifestyle props.
    canvas.ellipse(210, 790, 95, 55, (74, 144, 92), 0.85)
    canvas.rect(158, 780, 262, 880, (156, 95, 60), 1.0)
    for x in [175, 205, 235]:
        canvas.ellipse(x, 765, 32, 75, (45, 132, 77), 0.9, random.uniform(-0.3, 0.3))

    canvas.ellipse(790, 820, 125, 45, (120, 78, 48), 0.22)
    canvas.rect(730, 745, 855, 825, (221, 232, 238), 1.0)
    canvas.ellipse(792, 744, 63, 23, (245, 249, 250), 1.0)
    canvas.ellipse(792, 748, 43, 13, (117, 76, 45), 0.65)

    # Subtle sparkles.
    for x, y in [(128, 145), (890, 178), (785, 360), (214, 395), (920, 585)]:
        canvas.line(x - 18, y, x + 18, y, (255, 218, 81), 5)
        canvas.line(x, y - 18, x, y + 18, (255, 218, 81), 5)

    return canvas.save_png(output_path)
