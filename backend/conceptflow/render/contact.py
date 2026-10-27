"""Contact sheets: every keyframe of a lesson on one labelled image.

The visual critic is a vision model. Sending it twenty separate frames costs
twenty image parts; one sheet with each frame labelled by its beat id costs
one, and lets the critic compare scenes side by side the way an editor looks
at a storyboard wall.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import ROOT

THUMB = (480, 270)
PAD = 14
LABEL_H = 30
COLUMNS = 4
MAX_PER_SHEET = 16


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("Inter-Variable.ttf",):
        path = ROOT / "assets" / "fonts" / name
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
    return ImageFont.load_default()


def build(frames: list[tuple[str, Path, str]], out_dir: Path, stem: str = "contact") -> list[Path]:
    """frames: (beat_id, image_path, note). Returns the sheet paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets: list[Path] = []
    label_font, note_font = _font(17), _font(13)
    for start in range(0, len(frames), MAX_PER_SHEET):
        chunk = frames[start : start + MAX_PER_SHEET]
        rows = (len(chunk) + COLUMNS - 1) // COLUMNS
        w = COLUMNS * THUMB[0] + (COLUMNS + 1) * PAD
        h = rows * (THUMB[1] + LABEL_H) + (rows + 1) * PAD
        sheet = Image.new("RGB", (w, h), (236, 237, 244))
        draw = ImageDraw.Draw(sheet)
        for i, (beat, path, note) in enumerate(chunk):
            r, c = divmod(i, COLUMNS)
            x = PAD + c * (THUMB[0] + PAD)
            y = PAD + r * (THUMB[1] + LABEL_H + PAD)
            try:
                img = Image.open(path).convert("RGB").resize(THUMB)
            except OSError:
                img = Image.new("RGB", THUMB, (40, 40, 48))
            sheet.paste(img, (x, y + LABEL_H))
            draw.text((x + 2, y + 5), beat, fill=(21, 22, 29), font=label_font)
            if note:
                draw.text((x + 110, y + 9), note[:48], fill=(181, 70, 42), font=note_font)
        path = out_dir / f"{stem}_{start // MAX_PER_SHEET + 1}.jpg"
        sheet.save(path, quality=88)
        sheets.append(path)
    return sheets
