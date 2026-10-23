# MediaWiki Local Viewer

Turn a MediaWiki XML export (`Special:Export`, or a full dump) into a
searchable, offline, single-folder HTML viewer — no web server, no
database, no third-party dependencies. 

## Requirements

- Python 3.8+ (standard library only — nothing to `pip install`)
- Any modern browser

## Usage

```bash
python3 build_viewer.py /path/to/your-export.xml --out ./output
```

Then open `output/viewer.html` directly in your browser.

### Options

```
python3 build_viewer.py export.xml [--out ./output] [--include-ns 0] [--big-threshold 150000]
```

- `--out` — output directory (default `./output`). Always git-ignored.
- `--include-ns` — comma-separated MediaWiki namespace ids to include
  (default `0`, the main content namespace). E.g. `--include-ns 0,10,14`
  to also pull in Templates and Categories.
- `--big-threshold` — byte size above which a page switches to the
  large-page renderer instead of full Markdown conversion (default
  `150000`). See "Large pages" below.

## Conversion

Wikitext is converted to Markdown with a best-effort heuristic pass
(headings, bold/italic, lists, links, tables). Raw HTML mixed into the
wikitext is handled by a whitelist: `<pre>`/`<code>`/`<syntaxhighlight>`/
`<nowiki>` become real code blocks; `div`/`span`/`table`/`ul`/`ol`/`a`
(http(s) only) and similar tags are promoted to real, sanitized HTML in
the viewer (style/class attributes only — no inline event handlers, no
`url()`/`expression()` in CSS); everything else — unrecognized tags,
Apache-config-looking text, `<script>`/`<iframe>`/`<form>`/etc. — is either
shown as literal text or dropped outright, never executed. The exported
Markdown always has the HTML wrapper stripped back to plain text.

## Large pages

A handful of pages in a typical wiki export are enormous — multi-megabyte
tables are common (inventories, IP plans). Rendering those as one giant
HTML table would put tens of thousands of DOM nodes on the page and can
hang the tab. Pages over `--big-threshold` get a virtualized grid (only
~50 rows ever in the DOM, with a row filter) or paged plain text instead.

## Exporting

Every page has three export buttons:
- **Download as .md** / **Copy to clipboard** — plain Markdown, for
  pasting into Confluence or similar.
- **Export as standalone HTML** — a single, self-contained `.html` file
  with that page's full data and the same search/filter UI baked in. This
  is the one to use for the big tables: it embeds every row (not just
  what's on screen) plus the virtualized grid and filter box, so you can
  hand someone a single file and they get the same searchable table
  without the rest of the repo or build step. Normal pages export the
  same way, just without the table UI.
