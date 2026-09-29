"""
One-time slicing routine: extracts the 28 individual donor avatars from a
single uploaded reference sheet image into static/donors/, cropping out the
circular artwork only (text labels below each circle are excluded).

The source sheet is a 6/6/8/8-per-row grid of glowing circular avatars with
a caption below each one. Row 1 has its caption baked into the same glow
blob as the circle, so it needs an extra vertical trim; rows 2-4 already
have a clean gap between artwork and caption.

Usage:
    python3 slice_donor_sheet.py path/to/sheet.png
"""

import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

import donor_avatars

# Row-major identity mapping, matching the sheet's visual layout exactly.
AVATAR_NAME_ROWS = [
    ['donor_m_1', 'donor_m_2', 'donor_m_3', 'donor_m_4', 'donor_m_7a', 'donor_m_7b'],
    ['donor_m_5', 'donor_m_6a', 'donor_m_6b', 'donor_f_1a', 'donor_f_7', 'donor_m_8'],
    ['donor_f_2', 'donor_f_3a', 'donor_f_3b', 'donor_f_4b', 'donor_m_9', 'donor_f_8',
     'donor_m_10', 'donor_f_9'],
    ['donor_f_6', 'donor_f_5', 'donor_f_1b', 'donor_f_10', 'donor_m_11', 'donor_f_11',
     'donor_m_12', 'donor_f_12'],
]

MIN_BLOB_SIZE = 3000   # ignore thin text-label blobs when they're standalone
MERGE_SPLIT_WIDTH = 300  # blobs wider than this are >1 circle merged side by side
ROW_TRIM_RATIO = 0.90    # row 1 only: circle height as a fraction of its width
PAD = 2


def _detect_boxes(mask):
    lbl, n = ndimage.label(mask)
    boxes = ndimage.find_objects(lbl)
    sizes = ndimage.sum(mask, lbl, range(1, n + 1))
    return [b for i, b in enumerate(boxes, 1) if sizes[i - 1] > MIN_BLOB_SIZE]


def _group_into_rows(boxes):
    rows = {}
    for ys, xs in boxes:
        x0, x1, y0, y1 = xs.start, xs.stop, ys.start, ys.stop
        ycenter = (y0 + y1) / 2
        for key in rows:
            if abs(key - ycenter) < 50:
                rows[key].append((x0, x1, y0, y1))
                break
        else:
            rows[ycenter] = [(x0, x1, y0, y1)]
    return [sorted(rows[k], key=lambda t: t[0]) for k in sorted(rows.keys())]


def slice_sheet(sheet_path: str, out_dir: str = donor_avatars.DONOR_DIR):
    img = Image.open(sheet_path).convert('RGB')
    arr = np.array(img)
    mask = ~((arr[:, :, 0] > 235) & (arr[:, :, 1] > 235) & (arr[:, :, 2] > 235))

    grouped_rows = _group_into_rows(_detect_boxes(mask))
    if len(grouped_rows) != len(AVATAR_NAME_ROWS):
        raise ValueError(
            f'Expected {len(AVATAR_NAME_ROWS)} rows, detected {len(grouped_rows)}. '
            'The sheet layout has changed — re-check AVATAR_NAME_ROWS.'
        )

    os.makedirs(out_dir, exist_ok=True)
    saved = 0

    for row_idx, (row_boxes, row_names) in enumerate(zip(grouped_rows, AVATAR_NAME_ROWS)):
        expanded = []
        for x0, x1, y0, y1 in row_boxes:
            w = x1 - x0
            trimmed = (row_idx == 0)
            if trimmed:
                y1 = y0 + int(round(w * ROW_TRIM_RATIO))
            if w > MERGE_SPLIT_WIDTH:
                n_sub = round(w / 155)
                sub_w = w / n_sub
                for k in range(n_sub):
                    sx0, sx1 = int(x0 + k * sub_w), int(x0 + (k + 1) * sub_w)
                    expanded.append((sx0, sx1, y0, y0 + (sx1 - sx0), trimmed))
            else:
                expanded.append((x0, x1, y0, y1, trimmed))

        if len(expanded) != len(row_names):
            raise ValueError(
                f'Row {row_idx}: detected {len(expanded)} avatars, expected {len(row_names)}.'
            )

        for (x0, x1, y0, y1, trimmed), name in zip(expanded, row_names):
            x0p, y0p = max(0, x0 - PAD), max(0, y0 - PAD)
            x1p = min(arr.shape[1], x1 + PAD)
            y1p = y1 if trimmed else min(arr.shape[0], y1 + PAD)

            crop = img.crop((x0p, y0p, x1p, y1p))
            side = max(crop.size)
            canvas = Image.new('RGB', (side, side), (255, 255, 255))
            canvas.paste(crop, ((side - crop.size[0]) // 2, (side - crop.size[1]) // 2))
            canvas = canvas.resize((256, 256), Image.LANCZOS)
            canvas.save(os.path.join(out_dir, f'{name}.png'))
            saved += 1

    return saved


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 slice_donor_sheet.py path/to/sheet.png')
        sys.exit(1)
    n = slice_sheet(sys.argv[1])
    print(f'Sliced and saved {n} donor avatars into {donor_avatars.DONOR_DIR}/')
