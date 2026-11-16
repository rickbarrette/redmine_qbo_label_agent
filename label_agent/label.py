"""Draws a part label: the lines stacked, left aligned, each in the largest bold letters that fit.

Keep in step with redmine_qbo_labels' assets/javascripts/qbo_labels.js, which draws the print dialog's
preview the same way with the same font.

The label runs along a 62mm continuous roll at 300 dpi: it is 696 dots tall (across the roll) and as long as
its text needs, between the minimum and maximum length. The image is drawn the way the label is read
(length x 696) and turned for the printer when it's converted.
"""

import functools
import math
import os

from PIL import Image, ImageDraw, ImageFont

DPI = 300
HEIGHT = 696       # printable dots across a 62mm roll
MARGIN_X = 36      # ~3mm at each end
MARGIN_Y = 24      # ~2mm top and bottom
GAP_RATIO = 0.12   # space between lines, as a share of the (smallest) font size
MIN_SIZE = 8
MAX_SIZE = 700
# Lines whose biggest sizes are this close are drawn the same size
SAME_SIZE_WITHIN = 1.25

DEFAULT_FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fonts',
                            'qbo-label-bold.ttf')


def mm_to_dots(mm):
    return int(float(mm) / 25.4 * DPI + 0.5)


def _round(value):
    # JavaScript's Math.round for the positive numbers used here (Python's round() rounds halves to even)
    return int(value + 0.5)


@functools.lru_cache(maxsize=512)
def _font(path, size):
    return ImageFont.truetype(path, size)


class Box:
    """A line's ink at a font size, relative to where it's drawn from (left end of its baseline)"""

    def __init__(self, font, text):
        left, top, right, bottom = font.getbbox(text, anchor='ls')
        self.left = -left       # ink left of the origin (canvas' actualBoundingBoxLeft)
        self.right = right
        self.ascent = -top
        self.descent = bottom
        self.width = self.left + self.right
        self.height = self.ascent + self.descent


def _box(font_path, text, size):
    return Box(_font(font_path, size), text)


def _largest(test):
    """The largest font size from MIN_SIZE to MAX_SIZE that passes the test (MIN_SIZE if none does)"""
    lo, hi = MIN_SIZE, MAX_SIZE
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if test(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def _gap(sizes):
    return _round(min(sizes) * GAP_RATIO)


def _total_height(font_path, lines, sizes):
    return (sum(_box(font_path, text, size).height for text, size in zip(lines, sizes))
            + _gap(sizes) * (len(lines) - 1))


def layout(lines, min_length_mm, max_length_mm, font_path=DEFAULT_FONT):
    """Each line's font size and box, and the label's length in dots.

    Every line is as big as it can be: no wider than the longest label allows, and together no taller than
    the roll. Lines that would come out about the same size are made the same size; when one is held back by
    its length (a long name), the others still grow to fill the height.
    """
    max_length = mm_to_dots(max_length_mm)
    min_length = mm_to_dots(min_length_mm)
    max_width = max_length - 2 * MARGIN_X
    max_height = HEIGHT - 2 * MARGIN_Y

    def fits_alone(text, size):
        box = _box(font_path, text, size)
        return box.width <= max_width and box.height <= max_height

    # The biggest each line can be on its own
    limits = [_largest(lambda size, text=text: fits_alone(text, size)) for text in lines]

    def sizes_at(cap):
        return [min(cap, limit) for limit in limits]

    cap = _largest(lambda cap: _total_height(font_path, lines, sizes_at(cap)) <= max_height)
    sizes = sizes_at(cap)
    if max(sizes) < min(sizes) * SAME_SIZE_WITHIN:
        sizes = [min(sizes)] * len(lines)

    boxes = [_box(font_path, text, size) for text, size in zip(lines, sizes)]
    width = max(box.width for box in boxes)
    length = min(max_length, max(min_length, math.ceil(width + 2 * MARGIN_X)))
    return sizes, boxes, length


def render(lines, min_length_mm=25.4, max_length_mm=101.6, font_path=DEFAULT_FONT):
    """The label as a 1-bit image, length x 696 dots"""
    lines = [' '.join(str(line).split()) for line in lines]
    lines = [line for line in lines if line]
    if not lines:
        raise ValueError('The label has no text')

    sizes, boxes, length = layout(lines, min_length_mm, max_length_mm, font_path)
    image = Image.new('L', (length, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    gap = _gap(sizes)
    total = sum(box.height for box in boxes) + gap * (len(lines) - 1)
    y = _round((HEIGHT - total) / 2)
    for text, size, box in zip(lines, sizes, boxes):
        draw.text((MARGIN_X + box.left, y + box.ascent), text, font=_font(font_path, size), fill=0, anchor='ls')
        y += box.height + gap
    return image.point(lambda value: 0 if value < 128 else 255, mode='1')
