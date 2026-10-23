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
