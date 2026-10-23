#!/usr/bin/env python3
"""
build_viewer.py - Convert a MediaWiki XML export into a self-contained,
offline, searchable HTML viewer with per-page Markdown export.

Usage:
    python3 build_viewer.py path/to/export.xml [--out ./output] [--include-ns 0]

No third-party dependencies (stdlib only).
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BIG_PAGE_THRESHOLD_BYTES = 150_000


# ---------------------------------------------------------------------------
# XML loading
# ---------------------------------------------------------------------------

def strip_ns(tag):
    return tag.split('}', 1)[1] if '}' in tag else tag


def load_pages(xml_path):
    """Yield dicts: {id, title, ns, bytes, text} for each <page> in the export."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for elem in root.iter():
        elem.tag = strip_ns(elem.tag)

    for page_elem in root.iter('page'):
        title_elem = page_elem.find('title')
        ns_elem = page_elem.find('ns')
        id_elem = page_elem.find('id')
        if title_elem is None or id_elem is None:
            continue

        # Use the last <revision> (current revision in a standard export).
        revisions = page_elem.findall('revision')
        text = ''
        size = 0
        if revisions:
            text_elem = revisions[-1].find('text')
            if text_elem is not None:
                text = text_elem.text or ''
                size_attr = text_elem.get('bytes')
                size = int(size_attr) if size_attr else len(text.encode('utf-8'))

        yield {
            'id': id_elem.text.strip(),
            'title': title_elem.text or '',
            'ns': ns_elem.text.strip() if ns_elem is not None else '0',
            'bytes': size,
            'text': text,
        }


# ---------------------------------------------------------------------------
# Wikitext cleanup helpers
# ---------------------------------------------------------------------------

PARSER_OUTPUT_DIV = re.compile(r'^\s*<div\s+class="mw-parser-output">\s*')
TRAILING_DIV = re.compile(r'\s*</div>\s*$')
ACCESSCONTROL_RE = re.compile(r'<accesscontrol>(.*?)</accesscontrol>', re.IGNORECASE | re.DOTALL)
REDIRECT_RE = re.compile(r'^\s*#REDIRECT\s*:?\s*\[\[([^\]|#]+)', re.IGNORECASE)
INLINE_TAG_STRIP_RE = re.compile(r'</?(span|font|b|i|u|small|big|sup|sub)(\s[^>]*)?>', re.IGNORECASE)
BR_TAG_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)


def strip_simple_inline_html(text):
    """Drop common inline HTML wrappers (span/font/b/i/.../br) that old
    wikitext often mixes in, keeping their text content. Anything more
    exotic is left as-is and shows up as literal text in the viewer."""
    text = BR_TAG_RE.sub('\n', text)
    text = INLINE_TAG_STRIP_RE.sub('', text)
    return text


def strip_parser_output_divs(text):
    """Strip repeated <div class="mw-parser-output"> wrapper noise some
    MediaWiki exports leave around page bodies."""
    stripped = 0
    while True:
        new_text = PARSER_OUTPUT_DIV.sub('', text, count=1)
        if new_text == text:
            break
        text = new_text
        stripped += 1
    for _ in range(stripped):
        new_text = TRAILING_DIV.sub('', text, count=1)
        if new_text == text:
            break
        text = new_text
    return text


def extract_accesscontrol(text):
    tags = []
    def _capture(m):
        tags.append(m.group(1).strip())
        return ''
    text = ACCESSCONTROL_RE.sub(_capture, text)
    return text, tags


def detect_redirect(text):
    m = REDIRECT_RE.match(text)
    if m:
        return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Wikitext -> Markdown (heuristic, best-effort)
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r'^(={1,6})\s*(.*?)\s*\1\s*$')
BOLD_ITALIC_RE = re.compile(r"'''''(.+?)'''''")
BOLD_RE = re.compile(r"'''(.+?)'''")
ITALIC_RE = re.compile(r"''(.+?)''")
EXT_LINK_LABELLED_RE = re.compile(r'\[(https?://\S+)\s+([^\]]+)\]')
EXT_LINK_BARE_RE = re.compile(r'\[(https?://\S+)\]')
INTERNAL_LINK_RE = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]')
LIST_BULLET_RE = re.compile(r'^(\*+)\s*(.*)$')
LIST_NUMBER_RE = re.compile(r'^(#+)\s*(.*)$')
TABLE_BLOCK_RE = re.compile(r'\{\|.*?\n\|\}', re.DOTALL)


def convert_inline(text):
    text = BOLD_ITALIC_RE.sub(r'***\1***', text)
    text = BOLD_RE.sub(r'**\1**', text)
    text = ITALIC_RE.sub(r'*\1*', text)
    text = EXT_LINK_LABELLED_RE.sub(r'[\2](\1)', text)
    text = EXT_LINK_BARE_RE.sub(r'<\1>', text)
    text = INTERNAL_LINK_RE.sub(lambda m: m.group(2) if m.group(2) else m.group(1), text)
    return text


def convert_headings_and_lists(text):
    out_lines = []
    for line in text.split('\n'):
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            out_lines.append('#' * level + ' ' + convert_inline(m.group(2)))
            continue
        m = LIST_BULLET_RE.match(line)
        if m:
            depth = len(m.group(1))
            out_lines.append('  ' * (depth - 1) + '- ' + convert_inline(m.group(2)))
            continue
        m = LIST_NUMBER_RE.match(line)
        if m:
            depth = len(m.group(1))
            out_lines.append('  ' * (depth - 1) + '1. ' + convert_inline(m.group(2)))
            continue
        out_lines.append(convert_inline(line))
    return '\n'.join(out_lines)


def parse_wikitable(block_text):
    """Parse a single {| ... |} wikitable block into (headers, rows).
    Best-effort: does not handle nested tables or templates inside cells."""
    lines = block_text.split('\n')
    rows = []
    current = []
    current_is_header = False

    def flush():
        nonlocal current, current_is_header
        if current:
            rows.append((current_is_header, current))
        current = []
        current_is_header = False

    for line in lines[1:]:  # skip the opening {| line
        line = line.strip()
        if not line:
            continue
        if line.startswith('|}'):
            break
        if line.startswith('|-'):
            flush()
            continue
        if line.startswith('|+'):
            continue  # caption
        if line.startswith('!'):
            current_is_header = True
            # Normally header cells on one line are separated by '!!', but some
            # (often machine-generated) tables sloppily use '||' for header rows
            # too - split on either so we don't end up with one giant cell.
            cells = re.split(r'!!|\|\|', line[1:])
            current.extend(c.strip() for c in cells)
            continue
        if line.startswith('|'):
            cells = line[1:].split('||')
            current.extend(c.strip() for c in cells)
            continue
        # continuation of previous cell's content
        if current:
            current[-1] = (current[-1] + ' ' + line).strip()
    flush()

    headers = []
    data_rows = []
    for is_header, cells in rows:
        if is_header and not data_rows:
            headers = cells
        else:
            data_rows.append(cells)
    return headers, data_rows


def table_to_markdown(headers, rows):
    width = len(headers) if headers else (len(rows[0]) if rows else 0)
    if width == 0:
        return ''
    lines = []
    head = headers if headers else ['' for _ in range(width)]
    head = [convert_inline(c) for c in _pad(head, width)]
    lines.append('| ' + ' | '.join(head) + ' |')
    lines.append('|' + '|'.join(['---'] * width) + '|')
    for row in rows:
        row = [convert_inline(c) for c in _pad(row, width)]
        lines.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(lines)


def _pad(cells, width):
    cells = list(cells)
    if len(cells) < width:
        cells = cells + [''] * (width - len(cells))
    return cells[:width]


def find_largest_table(text):
    """Return (match_object, headers, rows) for the largest {| ... |} block, or None."""
    best = None
    for m in TABLE_BLOCK_RE.finditer(text):
        if best is None or len(m.group(0)) > len(best.group(0)):
            best = m
    if best is None:
        return None
    headers, rows = parse_wikitable(best.group(0))
    return best, headers, rows


def convert_page_body(text):
    """Full heuristic wikitext -> Markdown conversion for normal-sized pages."""
    def _table_sub(m):
        headers, rows = parse_wikitable(m.group(0))
        return table_to_markdown(headers, rows)
    text = TABLE_BLOCK_RE.sub(_table_sub, text)
    text = convert_headings_and_lists(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def sanitize_filename(title):
    safe = re.sub(r'[^A-Za-z0-9 _.-]', '_', title).strip()
    return safe[:80] if safe else 'page'


class ProgressReporter:
    """Prints a live [#####-----] NN% (done/total) elapsed/ETA bar to stderr
    when attached to a terminal; falls back to sparse percentage lines
    (10%, 20%, ...) when output is redirected, so logs stay readable."""

    def __init__(self, total, label='Converting pages', bar_width=30):
        self.total = max(total, 1)
        self.label = label
        self.bar_width = bar_width
        self.start = time.time()
        self.is_tty = sys.stderr.isatty()
        self._last_milestone = -1
        self._last_draw = 0.0

    @staticmethod
    def _fmt_duration(seconds):
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f'{m}m{s:02d}s' if m else f'{s}s'

    def update(self, done):
        pct = done / self.total
        elapsed = time.time() - self.start
        is_last = done >= self.total

        if self.is_tty:
            # Throttle redraws so writing the bar itself doesn't dominate
            # runtime on a fast, many-thousand-page build.
            if not is_last and (elapsed - self._last_draw) < 0.05:
                return
            self._last_draw = elapsed

            filled = int(self.bar_width * pct)
            bar = '#' * filled + '-' * (self.bar_width - filled)
            rate = done / elapsed if elapsed > 0 else 0
            eta = (self.total - done) / rate if rate > 0 else 0
            msg = (f'\r{self.label}: [{bar}] {pct*100:5.1f}% '
                   f'({done}/{self.total})  elapsed {self._fmt_duration(elapsed)}  '
                   f'eta {self._fmt_duration(eta)}   ')
            sys.stderr.write(msg)
            sys.stderr.flush()
        else:
            milestone = int(pct * 10)
            if milestone > self._last_milestone:
                self._last_milestone = milestone
                print(f'{self.label}: {pct*100:.0f}% ({done}/{self.total})', file=sys.stderr)

    def done(self):
        if self.is_tty:
            sys.stderr.write('\n')
            sys.stderr.flush()
        else:
            print(f'{self.label}: 100% ({self.total}/{self.total}) in {self._fmt_duration(time.time() - self.start)}',
                  file=sys.stderr)


def build(xml_path, out_dir, include_ns, big_threshold):
    pages_dir = os.path.join(out_dir, 'data', 'pages')
    os.makedirs(pages_dir, exist_ok=True)

    print('Reading XML export...', file=sys.stderr)
    all_pages = list(load_pages(xml_path))
    pages = [p for p in all_pages if p['ns'] in include_ns]

    index = []
    stats = {'normal': 0, 'big_table': 0, 'big_text': 0, 'redirect': 0}

    progress = ProgressReporter(len(pages))
    for done, page in enumerate(pages, start=1):
        pid = page['id']
        title = page['title']
        raw_text = strip_parser_output_divs(page['text'])
        raw_text, access_tags = extract_accesscontrol(raw_text)
        raw_text = strip_simple_inline_html(raw_text)

        entry = {
            'id': pid,
            'title': title,
            'size': page['bytes'],
            'access': access_tags,
        }

        redirect_target = detect_redirect(raw_text)
        if redirect_target:
            entry['redirect'] = redirect_target
            content = {'type': 'redirect', 'target': redirect_target}
            stats['redirect'] += 1
        elif page['bytes'] > big_threshold:
            found = find_largest_table(raw_text)
            if found and len(found[0].group(0)) > 0.5 * max(len(raw_text), 1):
                _, headers, rows = found
                entry['big'] = 'table'
                content = {'type': 'bigtable', 'headers': headers, 'rows': rows}
                stats['big_table'] += 1
            else:
                entry['big'] = 'text'
                content = {'type': 'bigtext', 'text': raw_text}
                stats['big_text'] += 1
        else:
            markdown = convert_page_body(raw_text)
            content = {'type': 'md', 'content': markdown}
            stats['normal'] += 1

        index.append(entry)

        page_js_path = os.path.join(pages_dir, f'{pid}.js')
        with open(page_js_path, 'w', encoding='utf-8') as f:
            f.write('PAGE_CONTENT[' + json.dumps(pid) + '] = ' + json.dumps(content, ensure_ascii=False) + ';\n')

        progress.update(done)

    progress.done()

    index.sort(key=lambda e: e['title'].lower())
    index_path = os.path.join(out_dir, 'data', 'index.js')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('const PAGES_INDEX = ' + json.dumps(index, ensure_ascii=False) + ';\n')

    template_path = os.path.join(SCRIPT_DIR, 'viewer_template', 'viewer.html')
    shutil.copyfile(template_path, os.path.join(out_dir, 'viewer.html'))

    return stats, len(index)


def main():
    parser = argparse.ArgumentParser(description='Build an offline, searchable HTML viewer from a MediaWiki XML export.')
    parser.add_argument('xml_path', help='Path to the MediaWiki XML export file')
    parser.add_argument('--out', default='./output', help='Output directory (default: ./output)')
    parser.add_argument('--include-ns', default='0', help='Comma-separated list of namespace ids to include (default: 0, the main namespace)')
    parser.add_argument('--big-threshold', type=int, default=BIG_PAGE_THRESHOLD_BYTES,
                         help=f'Page byte size above which the large-page renderer kicks in (default: {BIG_PAGE_THRESHOLD_BYTES})')
    args = parser.parse_args()

    if not os.path.isfile(args.xml_path):
        print(f'error: no such file: {args.xml_path}', file=sys.stderr)
        sys.exit(1)

    include_ns = set(s.strip() for s in args.include_ns.split(','))

    stats, total = build(args.xml_path, args.out, include_ns, args.big_threshold)

    print(f'Done. {total} pages written to {os.path.abspath(args.out)}')
    print(f"  normal markdown pages : {stats['normal']}")
    print(f"  big table pages       : {stats['big_table']}")
    print(f"  big text pages        : {stats['big_text']}")
    print(f"  redirect stubs        : {stats['redirect']}")
    print(f'Open {os.path.join(os.path.abspath(args.out), "viewer.html")} in a browser to view.')


if __name__ == '__main__':
    main()
