import pytest

from label_agent.label import HEIGHT, MARGIN_X, MARGIN_Y, layout, mm_to_dots, render


def test_label_is_the_roll_height_and_fits_the_text():
    image = render(['SMITH', 'E1234'], 25.4, 101.6)
    assert image.size[1] == HEIGHT
    assert mm_to_dots(25.4) <= image.size[0] <= mm_to_dots(101.6)
    assert image.mode == '1'


def test_short_text_gets_a_short_label_with_the_biggest_letters():
    sizes_short, boxes, length_short = layout(['LI', 'W1'], 25.4, 101.6)
    sizes_long, _, length_long = layout(['WASHINGTON', 'E1234'], 25.4, 101.6)
    # The letters fill the roll's height, and the label is only as long as they need
    assert sum(box.height for box in boxes) > (HEIGHT - 2 * MARGIN_Y) * 0.85
    assert length_short < length_long
    assert sizes_short[0] > sizes_long[0]


def test_the_minimum_length():
    _, _, length = layout(['I'], 50, 101.6)
    assert length == mm_to_dots(50)


def test_long_names_stop_at_the_maximum_length_with_smaller_letters():
    sizes, boxes, length = layout(['VANDERSCHMIDTBERGER', 'I12345'], 25.4, 101.6)
    # As long as allowed, give or take a font size step
    assert mm_to_dots(101.6) - 40 <= length <= mm_to_dots(101.6)
    assert max(box.width for box in boxes) <= length - 2 * MARGIN_X
    longer_sizes, _, _ = layout(['VANDERSCHMIDTBERGER', 'I12345'], 25.4, 200)
    assert longer_sizes[0] > sizes[0]


def test_a_long_name_leaves_room_for_a_bigger_po_number():
    sizes, boxes, _ = layout(['VANDERSCHMIDT', 'I12345'], 25.4, 101.6)
    assert sizes[1] > sizes[0] * 1.25
    assert sum(box.height for box in boxes) <= HEIGHT - 2 * MARGIN_Y


def test_lines_about_the_same_length_are_the_same_size():
    sizes, _, _ = layout(['SMITH', 'E1234'], 25.4, 101.6)
    assert sizes[0] == sizes[1]


def test_lines_fit_the_height():
    _, boxes, _ = layout(['SMITH', 'E1234'], 25.4, 300)
    assert sum(box.height for box in boxes) < HEIGHT


def test_text_is_drawn_inside_the_margins():
    image = render(['SMITH', 'E1234'], 25.4, 101.6)
    left, top, right, bottom = image.point(lambda v: 255 - v).getbbox()
    assert left >= MARGIN_X - 1
    assert right <= image.size[0] - MARGIN_X + 1
    assert top > 0 and bottom < HEIGHT


def test_blank_lines_are_dropped_and_an_empty_label_is_refused():
    assert render(['  ', 'E1234'], 25.4, 101.6).size[1] == HEIGHT
    with pytest.raises(ValueError):
        render(['', '  '])


def test_accented_names():
    render(['MÜLLER', 'ŁUKASZ', 'W42'], 25.4, 101.6)
