#!/usr/bin/env python3
"""Convert 101books SGF problems to per-book FXL epubs."""

import argparse
import io
import re
import struct
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).parent
BOOKS_DIR = REPO / "books"
PROBLEMS_DIR = REPO / "problems"

WIDTH, HEIGHT = 480, 800
OUT_DIR = REPO / "epub" / "x4"
XTCH_DIR = REPO / "xtch" / "x4"
IS_UNIVERSAL = False
UI_SCALE = 1.0   # scales fonts/text positions relative to 480px reference width
RENDER_SCALE = 3

def _set_device(device):
    global WIDTH, HEIGHT, OUT_DIR, XTCH_DIR, IS_UNIVERSAL, UI_SCALE, RENDER_SCALE
    IS_UNIVERSAL = (device == 'universal')
    if device == 'x4':
        WIDTH, HEIGHT = 480, 800
        RENDER_SCALE = 3
    elif device == 'x3':
        WIDTH, HEIGHT = 528, 792
        RENDER_SCALE = 3
    elif device == 'universal':
        WIDTH, HEIGHT = 1080, 1800
        RENDER_SCALE = 2
    UI_SCALE = WIDTH / 480
    OUT_DIR = REPO / 'epub' / device
    XTCH_DIR = REPO / 'xtch' / device

# --- Board rendering ---

MARGIN_TOP   = 40    # space above board for problem number label
PADDING      = 16    # min margin around board on all other sides
STONE_FRAC   = 0.44  # stone_r = cell * STONE_FRAC

_HIRAGINO_W3 = '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc'
_LATEX_MACRON = {'a':'ā','e':'ē','i':'ī','o':'ō','u':'ū',
                 'A':'Ā','E':'Ē','I':'Ī','O':'Ō','U':'Ū'}

HOSHI = {(3,3),(9,3),(15,3),(3,9),(9,9),(15,9),(3,15),(9,15),(15,15)}

def decode_latex(s):
    s = re.sub(r'\\=([aeiouAEIOU])', lambda m: _LATEX_MACRON[m.group(1)], s)
    return s.replace('~', ' ').replace('\\&', '&')

def sgf_coord(s):
    """'ab' -> (col 0-indexed, row 0-indexed from top of full 19x19)"""
    return ord(s[0]) - ord('a'), ord(s[1]) - ord('a')

def parse_sgf(text):
    ab = re.findall(r'AB((?:\[[a-z]{2}\])+)', text)
    aw = re.findall(r'AW((?:\[[a-z]{2}\])+)', text)
    moves = re.findall(r';[BW]\[([a-z]{2})\]', text)
    blacks = [sgf_coord(m) for ab_group in ab for m in re.findall(r'\[([a-z]{2})\]', ab_group)]
    whites = [sgf_coord(m) for aw_group in aw for m in re.findall(r'\[([a-z]{2})\]', aw_group)]
    return blacks, whites, [sgf_coord(m) for m in moves]

def compute_viewport(blacks, whites, solution_moves):
    """Compute the board region to display, consistent for problem+solution pages."""
    all_coords = blacks + whites + solution_moves
    if not all_coords:
        return 0, 8, 0, 8
    cols = [c for c, r in all_coords]
    rows = [r for c, r in all_coords]
    c0 = max(0,  min(cols) - 1)
    c1 = min(18, max(cols) + 1)
    r0 = max(0,  min(rows) - 1)
    r1 = min(18, max(rows) + 1)
    return c0, c1, r0, r1

def cell_size(c0, c1, r0, r1, avail_w, avail_h):
    """Square cell size that fits the board including stone overhang."""
    n_x = c1 - c0
    n_y = r1 - r0
    # Board center-to-center width = n_x * cell; stones overhang by STONE_FRAC*cell on each side.
    # Total width needed = cell * (n_x + 2*STONE_FRAC) ≤ avail_w
    cell = int(min(avail_w / (n_x + 2 * STONE_FRAC),
                   avail_h / (n_y + 2 * STONE_FRAC)))
    return max(cell, 16)

# --- Board rendering primitives ---

def draw_board(draw, board, c0, c1, r0, r1, x0, y0, cell, move_history=None):
    """Draw stones onto `draw`. move_history is [(num, col, row), ...]; each stone
    whose position appears in history gets its move number drawn on it.
    Positions in history that are NOT in board (captured) get a ghost circle."""
    stone_r = int(cell * STONE_FRAC)
    THIN = max(1, cell // 30)

    pos_to_num = {}
    if move_history:
        for num, col, row in move_history:
            if (col, row) not in pos_to_num:  # first occurrence: traditional "N at M" convention
                pos_to_num[(col, row)] = num

    for (col, row), color in board.items():
        if not (c0 <= col <= c1 and r0 <= row <= r1):
            continue
        cx = x0 + (col - c0) * cell
        cy = y0 + (row - r0) * cell
        num = pos_to_num.get((col, row))
        if color == 'B':
            draw.ellipse([cx-stone_r, cy-stone_r, cx+stone_r, cy+stone_r], fill=0)
            if num is not None:
                draw.text((cx, cy), str(num), fill=255, font=_num_font(cell), anchor='mm')
        else:
            draw.ellipse([cx-stone_r, cy-stone_r, cx+stone_r, cy+stone_r],
                         fill=255, outline=0, width=max(1, THIN+1))
            if num is not None:
                draw.text((cx, cy), str(num), fill=0, font=_num_font(cell), anchor='mm')



def draw_grid(draw, c0, c1, r0, r1, x0, y0, cell):
    n_cols = c1 - c0 + 1
    n_rows = r1 - r0 + 1
    board_w = (c1 - c0) * cell
    board_h = (r1 - r0) * cell
    THICK = max(2, cell // 12)
    dot_r = max(2, cell // 10)

    for ci in range(n_cols):
        col = c0 + ci
        is_board_edge = (col == 0 or col == 18)
        is_crop_edge  = (ci == 0 or ci == n_cols - 1) and not is_board_edge
        if is_crop_edge:
            continue
        x = x0 + ci * cell
        w = THICK if is_board_edge else max(1, cell // 30)
        draw.line([(x, y0), (x, y0 + board_h)], fill=0, width=w)
    for ri in range(n_rows):
        row = r0 + ri
        is_board_edge = (row == 0 or row == 18)
        is_crop_edge  = (ri == 0 or ri == n_rows - 1) and not is_board_edge
        if is_crop_edge:
            continue
        y = y0 + ri * cell
        w = THICK if is_board_edge else max(1, cell // 30)
        draw.line([(x0, y), (x0 + board_w, y)], fill=0, width=w)
    for ci in range(n_cols):
        for ri in range(n_rows):
            if (c0 + ci, r0 + ri) in HOSHI:
                cx, cy = x0 + ci * cell, y0 + ri * cell
                draw.ellipse([cx-dot_r, cy-dot_r, cx+dot_r, cy+dot_r], fill=0)

_font_cache = {}
def _label_font(size=22):
    key = ('label', size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', size)
        except Exception:
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]

def _bold_font(size=22):
    key = ('bold', size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', size, index=1)
        except Exception:
            _font_cache[key] = _label_font(size)
    return _font_cache[key]

def _num_font(cell):
    size = max(10, int(cell * 0.65))
    key = ('num', size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', size)
        except Exception:
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]

def _hiragino_font(size):
    key = ('hiragino', size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(_HIRAGINO_W3, size)
        except Exception:
            _font_cache[key] = _label_font(size)
    return _font_cache[key]

def _wrap_text(text, font, max_w):
    words = text.split()
    lines, current = [], []
    for word in words:
        test = ' '.join(current + [word])
        if font.getlength(test) <= max_w or not current:
            current.append(word)
        else:
            lines.append(' '.join(current))
            current = [word]
    if current:
        lines.append(' '.join(current))
    return lines

# --- Full-page images ---

def make_title_image(title, jp_title, level, num_problems, source=''):
    S = RENDER_SCALE
    img = Image.new('L', (WIDTH * S, HEIGHT * S), 255)
    draw = ImageDraw.Draw(img)

    max_w = (WIDTH - 2 * PADDING) * S
    cx = WIDTH * S // 2

    U = S * UI_SCALE
    en_font   = _bold_font(int(48 * U))
    jp_font   = _hiragino_font(int(30 * U))
    inf_font  = _label_font(int(24 * U))
    note_font = _label_font(int(20 * U))
    url_font  = _label_font(int(16 * U))

    title_lines = _wrap_text(title, en_font, max_w)
    en_line_h   = int(en_font.getbbox('Ag')[3] * 1.25)
    jp_line_h   = int(jp_font.getbbox('あ')[3] * 1.3) if jp_title else 0
    inf_line_h  = int(inf_font.getbbox('A')[3] * 1.3)
    note_line_h = int(note_font.getbbox('A')[3] * 1.3)
    gap         = 20 * S
    rule_h      = 2 * S

    has_jp = bool(jp_title)
    total_h = (en_line_h * len(title_lines)
               + (gap + jp_line_h if has_jp else 0)
               + gap + rule_h + gap
               + inf_line_h
               + gap + note_line_h)

    y = (HEIGHT * S - total_h) // 2

    for line in title_lines:
        draw.text((cx, y), line, fill=0, font=en_font, anchor='mt')
        y += en_line_h

    if has_jp:
        y += gap
        draw.text((cx, y), jp_title, fill=0, font=jp_font, anchor='mt')
        y += jp_line_h

    y += gap
    rule_w = max_w // 3
    draw.line([(cx - rule_w, y + rule_h // 2), (cx + rule_w, y + rule_h // 2)],
              fill=0, width=rule_h)
    y += rule_h + gap

    info_parts = []
    if level:
        info_parts.append(level)
    info_parts.append(f"{num_problems} problems")
    draw.text((cx, y), ' · '.join(info_parts), fill=0, font=inf_font, anchor='mt')
    y += inf_line_h + gap

    draw.text((cx, y), "All problems are black to play.", fill=0, font=note_font, anchor='mt')

    if source:
        url_line_h = int(url_font.getbbox('A')[3] * 1.3)
        url_y = HEIGHT * S - PADDING * S - url_line_h
        draw.text((cx, url_y), source, fill=0x80, font=url_font, anchor='mt')

    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)

def make_problem_image(blacks, whites, c0, c1, r0, r1, problem_num):
    S = RENDER_SCALE
    margin_top = int(MARGIN_TOP * UI_SCALE)
    avail_w = (WIDTH  - 2 * PADDING) * S
    avail_h = (HEIGHT - margin_top - PADDING) * S
    cell = cell_size(c0, c1, r0, r1, avail_w, avail_h)
    board_w = (c1 - c0) * cell
    board_h = (r1 - r0) * cell
    x0 = (WIDTH * S - board_w) // 2
    y0 = margin_top * S + (avail_h - board_h) // 2

    img  = Image.new('L', (WIDTH * S, HEIGHT * S), 255)
    draw = ImageDraw.Draw(img)
    draw_grid(draw, c0, c1, r0, r1, x0, y0, cell)

    stone_r = int(cell * STONE_FRAC)
    THIN = max(1, cell // 30)
    for col, row in blacks:
        if c0 <= col <= c1 and r0 <= row <= r1:
            cx, cy = x0 + (col-c0)*cell, y0 + (row-r0)*cell
            draw.ellipse([cx-stone_r, cy-stone_r, cx+stone_r, cy+stone_r], fill=0)
    for col, row in whites:
        if c0 <= col <= c1 and r0 <= row <= r1:
            cx, cy = x0 + (col-c0)*cell, y0 + (row-r0)*cell
            draw.ellipse([cx-stone_r, cy-stone_r, cx+stone_r, cy+stone_r],
                         fill=255, outline=0, width=max(1, THIN+1))

    draw.text((WIDTH * S // 2, int(80 * S * UI_SCALE)), f"Problem {problem_num}", fill=0,
              font=_bold_font(int(36 * S * UI_SCALE)), anchor='mt')
    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)

def make_solution_pages(blacks, whites, solution_moves, c0, c1, r0, r1, problem_num):
    """Return a single-element list with a full-page solution diagram."""
    if not solution_moves:
        return []

    S = RENDER_SCALE

    # Overlay approach: initial position stays, solution moves added without capture simulation.
    # Traditional tsumego single-diagram convention — captured initial stones remain visible.
    board = {}
    for c, r in blacks: board[(c, r)] = 'B'
    for c, r in whites: board[(c, r)] = 'W'
    history = []
    first_at = {}
    annotations = []
    for i, (col, row) in enumerate(solution_moves):
        color = 'B' if i % 2 == 0 else 'W'
        if (col, row) not in board:
            board[(col, row)] = color
        history.append((i + 1, col, row))
        if (col, row) in first_at:
            annotations.append(f"{i + 1} at {first_at[(col, row)]}")
        else:
            first_at[(col, row)] = i + 1

    HEADER = int(44 * UI_SCALE)
    avail_w = (WIDTH - 2 * PADDING) * S
    avail_h = (HEIGHT - HEADER - PADDING) * S
    cell_px = cell_size(c0, c1, r0, r1, avail_w, avail_h)
    board_w = (c1 - c0) * cell_px
    board_h = (r1 - r0) * cell_px
    gx0 = (WIDTH * S - board_w) // 2
    gy0 = HEADER * S + (avail_h - board_h) // 2

    img  = Image.new('L', (WIDTH * S, HEIGHT * S), 255)
    draw = ImageDraw.Draw(img)

    draw.text((WIDTH * S // 2, int(80 * S * UI_SCALE)), f"Solution {problem_num}", fill=0,
              font=_bold_font(int(36 * S * UI_SCALE)), anchor='mt')
    draw_grid(draw, c0, c1, r0, r1, gx0, gy0, cell_px)
    draw_board(draw, board, c0, c1, r0, r1, gx0, gy0, cell_px, history)

    if annotations:
        stone_r = int(cell_px * STONE_FRAC)
        font = _label_font(int(28 * S * UI_SCALE))
        line_h = font.getbbox("5 at 3")[3] + int(4 * S * UI_SCALE)
        ann_y = gy0 + board_h + stone_r + int(36 * S * UI_SCALE)
        for ann in annotations:
            draw.text((WIDTH * S // 2, ann_y), ann, fill=0, font=font, anchor='mt')
            ann_y += line_h

    return [img.resize((WIDTH, HEIGHT), Image.LANCZOS)]

# --- XTC/XTH encoding ---

def _quantize_dither(arr, t1=85, t2=170, t3=230):
    """Threshold quantization: 8-bit grayscale → 4-level (0-3). No dithering needed —
    LANCZOS antialiasing already provides smooth edge transitions."""
    levels = np.zeros(arr.shape, dtype=np.uint8)
    levels[(arr >= t1) & (arr < t2)] = 1
    levels[(arr >= t2) & (arr < t3)] = 2
    levels[arr >= t3] = 3
    return levels

def _image_to_xth(img):
    """Encode a PIL 'L' image to XTH bytes (4-level grayscale, Xteink format)."""
    w, h = img.size
    arr = np.array(img, dtype=np.uint8)  # (h, w)

    levels = _quantize_dither(arr)

    # LUT + invert as per Xteink display LUT: {0→3, 1→1, 2→2, 3→0}
    lut = np.array([3, 1, 2, 0], dtype=np.uint8)
    mapped = lut[levels]  # (h, w)

    # Bitplane encoding: column-major, right-to-left columns, groups of 8 rows MSB-first
    col_rtl = mapped[:, ::-1].T  # (w, h) — columns right-to-left

    h_pad = ((h + 7) // 8) * 8
    padded = np.zeros((w, h_pad), dtype=np.uint8)
    padded[:, :h] = col_rtl

    groups = padded.reshape(w, h_pad // 8, 8)  # (w, n_bytes, 8)
    shifts = np.array([7, 6, 5, 4, 3, 2, 1, 0], dtype=np.int32)
    plane1 = np.sum(((groups >> 1) & 1).astype(np.int32) * (1 << shifts), axis=2).astype(np.uint8).flatten().tobytes()
    plane2 = np.sum((groups & 1).astype(np.int32) * (1 << shifts), axis=2).astype(np.uint8).flatten().tobytes()

    data = plane1 + plane2
    checksum = int(np.sum(np.frombuffer(data, dtype=np.uint8), dtype=np.uint64))
    header = struct.pack('<IHHBBIQ', 0x00485458, w, h, 0, 0, len(data), checksum)
    return header + data

def _write_xtch(out_path, title, frames, width, height):
    """Write a list of XTH frame bytes into an XTCH container file.
    XTCH (magic 0x48435458) supports XTH grayscale pages; XTC only supports XTG mono."""
    n = len(frames)
    HEADER_SIZE    = 56
    METADATA_SIZE  = 256
    INDEX_SIZE     = 16

    metadata_offset = HEADER_SIZE
    index_offset    = metadata_offset + METADATA_SIZE
    data_offset     = index_offset + INDEX_SIZE * n

    def padded(s, length):
        b = s.encode('utf-8')[:length - 1]
        return b + b'\x00' * (length - len(b))

    buf = bytearray()
    buf += struct.pack('<I',  0x48435458)   # magic "XTCH"
    buf += struct.pack('<H',  0x0100)       # version
    buf += struct.pack('<H',  n)            # pageCount
    buf += struct.pack('<B',  0)            # readDirection (LTR)
    buf += struct.pack('<B',  1)            # hasMetadata
    buf += struct.pack('<B',  0)            # hasThumbnails
    buf += struct.pack('<B',  0)            # hasChapters
    buf += struct.pack('<I',  1)            # currentPage (1-based: start at first page)
    buf += struct.pack('<Q',  metadata_offset)
    buf += struct.pack('<Q',  index_offset)
    buf += struct.pack('<Q',  data_offset)
    buf += struct.pack('<Q',  0)            # thumbOffset
    buf += struct.pack('<Q',  0)            # chapterOffset

    # Metadata (256 bytes)
    buf += padded(title, 128)              # title
    buf += padded('', 64)                  # author
    buf += padded('', 32)                  # publisher
    buf += padded('en-US', 16)             # language
    buf += struct.pack('<I', int(time.time()))
    buf += struct.pack('<H', 0)            # coverPage = first page
    buf += struct.pack('<H', 0)            # chapterCount
    buf += b'\x00' * 8                     # reserved

    # Index table
    cur = data_offset
    for frame in frames:
        buf += struct.pack('<QIHH', cur, len(frame), width, height)
        cur += len(frame)

    # Page data
    for frame in frames:
        buf += frame

    out_path.write_bytes(bytes(buf))

# --- epub building ---

CONTAINER_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''

def opf(book_id, title, spine_items):
    manifest = '\n'.join(
        f'    <item id="{sid}" href="pages/{sid}.xhtml" media-type="application/xhtml+xml"/>\n'
        f'    <item id="{sid}-img" href="images/{sid}.png" media-type="image/png"/>'
        for sid in spine_items
    )
    spine = '\n'.join(f'    <itemref idref="{sid}"/>' for sid in spine_items)
    if IS_UNIVERSAL:
        layout_meta = ''
    else:
        layout_meta = f'''    <meta property="rendition:layout">pre-paginated</meta>
    <meta property="rendition:orientation">portrait</meta>
    <meta property="rendition:spread">none</meta>
    <meta property="rendition:viewport">width={WIDTH}; height={HEIGHT}</meta>
    <meta property="rendition:align-x-center">center</meta>'''
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">{book_id}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>en</dc:language>
{layout_meta}
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>'''

def page_xhtml(sid):
    if IS_UNIVERSAL:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <style>
    html, body {{ margin: 0; padding: 0; background: #fff; }}
    img {{ display: block; width: 100%; height: auto; }}
  </style>
</head>
<body><img src="../images/{sid}.png" alt=""/></body>
</html>'''
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta name="viewport" content="width={WIDTH}, height={HEIGHT}"/>
  <style>
    html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background: #fff;
                  display: flex; justify-content: center; align-items: center; }}
    img {{ display: block; width: {WIDTH}px; height: {HEIGHT}px; flex-shrink: 0; }}
  </style>
</head>
<body><img src="../images/{sid}.png" alt=""/></body>
</html>'''

def nav_xhtml(title, spine_items, labels):
    items = '\n'.join(
        f'      <li><a href="pages/{sid}.xhtml">{labels[sid]}</a></li>'
        for sid in spine_items if sid == 'title' or sid.startswith('p'))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body>
  <nav epub:type="toc">
    <ol>
{items}
    </ol>
  </nav>
</body>
</html>'''

def ncx(book_id, title, spine_items, labels):
    problem_items = [sid for sid in spine_items if sid == 'title' or sid.startswith('p')]
    points = '\n'.join(
        f'''  <navPoint id="np-{i}" playOrder="{i+1}">
    <navLabel><text>{labels[sid]}</text></navLabel>
    <content src="pages/{sid}.xhtml"/>
  </navPoint>'''
        for i, sid in enumerate(problem_items)
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{book_id}"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap>
{points}
  </navMap>
</ncx>'''

# --- tex parsing ---

def parse_tex(tex_path):
    text = tex_path.read_text()
    def extract(key):
        m = re.search(rf'\\def\\{key}\{{([^}}]+)\}}', text)
        return decode_latex(m.group(1)) if m else ''
    title    = extract('entitle') or tex_path.stem
    jp_title = extract('jptitle')
    level    = extract('level')
    source   = extract('source')
    problems = re.findall(r'\\p\{(\d+)\}\{(\d+)\}', text)
    return title, jp_title, level, source, problems

# --- main ---

def convert_book(tex_path, book_slug):
    title, jp_title, level, source, problem_refs = parse_tex(tex_path)
    print(f"  {title}: {len(problem_refs)} problems")

    book_id = f"urn:uuid:{uuid.uuid4()}"
    out_path = OUT_DIR / f"{book_slug}.epub"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    spine_items = []
    labels = {}  # sid -> human-readable TOC label
    pages = {}   # sid -> xhtml str
    images = {}  # sid -> PIL Image

    # Title page (first in spine, not in TOC)
    spine_items.append('title')
    labels['title'] = 'Title Page'
    pages['title'] = page_xhtml('title')
    images['title'] = make_title_image(title, jp_title, level, len(problem_refs), source)

    for prob_num, (chapter_id, problem_id) in enumerate(problem_refs, 1):
        sgf_path = PROBLEMS_DIR / book_slug / chapter_id / f"{problem_id}.sgf"
        sol_path = PROBLEMS_DIR / book_slug / chapter_id / f"{problem_id}.solution"

        if not sgf_path.exists():
            print(f"    WARNING: missing {sgf_path}")
            continue

        sgf_text = sgf_path.read_text()
        blacks, whites, moves = parse_sgf(sgf_text)

        COLS_MAP = list('ABCDEFGHJKLMNOPQRST')
        solution_moves = []
        if sol_path.exists():
            for token in sol_path.read_text().strip().split():
                if len(token) >= 2 and token[0] in COLS_MAP:
                    solution_moves.append((COLS_MAP.index(token[0]), 19 - int(token[1:])))

        # Shared viewport for problem + solution
        c0, c1, r0, r1 = compute_viewport(blacks, whites, solution_moves)

        # Problem page
        p_sid = f"p{prob_num:04d}"
        spine_items.append(p_sid)
        labels[p_sid] = f"Problem {prob_num}"
        pages[p_sid] = page_xhtml(p_sid)
        images[p_sid] = make_problem_image(blacks, whites, c0, c1, r0, r1, prob_num)

        # Solution page
        sol_imgs = make_solution_pages(blacks, whites, solution_moves, c0, c1, r0, r1, prob_num)
        total_sol = len(sol_imgs)
        for si, sol_img in enumerate(sol_imgs):
            s_sid = f"s{prob_num:04d}" if total_sol == 1 else f"s{prob_num:04d}p{si}"
            spine_items.append(s_sid)
            if total_sol == 1:
                labels[s_sid] = f"Solution {prob_num}"
            else:
                labels[s_sid] = f"Solution {prob_num} ({si+1}/{total_sol})"
            pages[s_sid] = page_xhtml(s_sid)
            images[s_sid] = sol_img

    if len(spine_items) <= 1:  # only title page, no problems
        print(f"    SKIP: no problems found")
        return

    # XTCH output (device-specific only, not universal)
    if not IS_UNIVERSAL:
        XTCH_DIR.mkdir(parents=True, exist_ok=True)
        xtch_path = XTCH_DIR / f"{book_slug}.xtch"
        xtch_frames = [_image_to_xth(images[sid]) for sid in spine_items]
        _write_xtch(xtch_path, title, xtch_frames, WIDTH, HEIGHT)
        xtch_kb = xtch_path.stat().st_size // 1024
        print(f"    -> {xtch_path.name} ({xtch_kb} KB)")

    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        zf.writestr('META-INF/container.xml', CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', opf(book_id, title, spine_items))
        zf.writestr('OEBPS/nav.xhtml', nav_xhtml(title, spine_items, labels))
        zf.writestr('OEBPS/toc.ncx', ncx(book_id, title, spine_items, labels))
        for sid, xhtml in pages.items():
            zf.writestr(f'OEBPS/pages/{sid}.xhtml', xhtml)
        for sid, img in images.items():
            buf = io.BytesIO()
            img.save(buf, 'PNG', optimize=True, compress_level=9)
            zf.writestr(f'OEBPS/images/{sid}.png', buf.getvalue())

    size_kb = out_path.stat().st_size // 1024
    print(f"    -> {out_path.name} ({size_kb} KB)")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('books', nargs='*', help='book slugs to convert (default: all)')
    parser.add_argument('--device', choices=['x4', 'x3', 'universal', 'both', 'all'],
                        default='both',
                        help='target device (both=x3+x4, all=x3+x4+universal)')
    args = parser.parse_args()

    tex_files = sorted(BOOKS_DIR.glob('*.tex'))
    tex_files = [t for t in tex_files if t.name != 'header.tex']
    if args.books:
        names = set(args.books)
        tex_files = [t for t in tex_files if t.stem in names]

    if args.device == 'both':
        devices = ['x4', 'x3']
    elif args.device == 'all':
        devices = ['x4', 'x3', 'universal']
    else:
        devices = [args.device]

    for device in devices:
        _set_device(device)
        print(f"Converting {len(tex_files)} books for {device} ({WIDTH}x{HEIGHT})...")
        for tex_path in tex_files:
            convert_book(tex_path, tex_path.stem)

if __name__ == '__main__':
    main()
