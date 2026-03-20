"""
tools/make_icon.py
Generates the RCRA Forge app icon (assets/icon.ico).
Run once before building: python tools/make_icon.py

Requires Pillow (pip install Pillow).
Creates a multi-size ICO with: 16, 32, 48, 64, 128, 256 px frames.
"""

import os
import math

def make_icon():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not installed. Run: pip install Pillow")
        return

    sizes = [256, 128, 64, 48, 32, 16]
    frames = []

    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        pad = max(2, size // 16)
        r = size - pad * 2

        # Background rounded rect
        draw.rounded_rectangle(
            [pad, pad, pad + r, pad + r],
            radius=max(4, size // 8),
            fill=(26, 28, 34, 255),
            outline=(58, 111, 191, 255),
            width=max(1, size // 32),
        )

        # Stylised "R" letter
        cx, cy = size // 2, size // 2
        font_size = int(size * 0.52)
        try:
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("Arial Bold.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        text = "R"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = cx - tw // 2 - bbox[0]
        ty = cy - th // 2 - bbox[1] - int(size * 0.02)

        # Drop shadow
        shadow_off = max(1, size // 48)
        draw.text((tx + shadow_off, ty + shadow_off), text, font=font,
                  fill=(10, 20, 50, 180))
        # Main glyph
        draw.text((tx, ty), text, font=font, fill=(93, 173, 226, 255))

        # Accent dot (bottom-right)
        dot_r = max(2, size // 14)
        dx = pad + r - dot_r - max(2, size // 20)
        dy = pad + r - dot_r - max(2, size // 20)
        draw.ellipse([dx, dy, dx + dot_r*2, dy + dot_r*2], fill=(255, 150, 50, 255))

        frames.append(img)

    os.makedirs("assets", exist_ok=True)
    out_path = os.path.join("assets", "icon.ico")
    frames[0].save(
        out_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"[OK] Icon saved to {out_path}  ({len(sizes)} sizes: {sizes})")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    make_icon()
