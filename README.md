# pinboard.py

A small command-line client for reading and managing [Pinboard.in](https://pinboard.in) bookmarks. It is designed for both humans and AI agents: output is concise by default, every command supports JSON, and the client enforces Pinboard's published API rate limits across separate invocations.

## Features

- List bookmarks with date, tag, limit, and offset filters
- Add bookmarks with titles, descriptions, tags, privacy, and to-read status
- Update bookmarks by URL while preserving fields that were not supplied
- Get popular and personalized tag suggestions for a URL
- List tags with usage counts
- Rename or merge tags account-wide
- Human-readable and JSON output
- Persistent rate-limit coordination across CLI processes
- Direct use of the Pinboard v1 API through `requests`; no Pinboard client library

## Requirements

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- A Pinboard account and API token

The executable uses PEP 723 metadata, so `uv` installs `requests` automatically.

## Authentication

Find your token on the [Pinboard password settings page](https://pinboard.in/settings/password). It has the form `username:TOKEN`.

Setting an environment variable is recommended:

```bash
export PINBOARD_TOKEN="username:TOKEN"
```

You may instead supply it globally on the command line:

```bash
pinboard.py --token "username:TOKEN" bookmarks list
```

`--token` takes precedence over `PINBOARD_TOKEN`. Be aware that command-line arguments may be visible in shell history and process listings. The client never includes the token in normal output or error messages.

## Installation

```bash
git clone https://github.com/vicgarcia/pinboard.py.git
cd pinboard.py
chmod +x pinboard.py

./pinboard.py --help
```

To make it available in `PATH`:

```bash
install -m 755 pinboard.py ~/.local/bin/pinboard.py
```

## Command overview

```text
pinboard.py [--token TOKEN] [--wait] bookmarks list [...]
pinboard.py [--token TOKEN] [--wait] bookmarks add URL [...]
pinboard.py [--token TOKEN] [--wait] bookmarks update URL [...]
pinboard.py [--token TOKEN] [--wait] bookmarks suggest-tags URL [--json]
pinboard.py [--token TOKEN] [--wait] tags list [--json]
pinboard.py [--token TOKEN] [--wait] tags rename OLD_TAG NEW_TAG [--json]
```

Global options such as `--token` and `--wait` must appear before the `bookmarks` or `tags` domain. The `--json` option belongs to the individual command and appears after it.

## Bookmarks

### List bookmarks

```bash
# Most recent 20 bookmarks
pinboard.py bookmarks list

# Limit output and paginate
pinboard.py bookmarks list --limit 10 --offset 20

# Filter by date
pinboard.py bookmarks list --from 2026-01-01 --to 2026-01-31

# Filter by up to three tags
pinboard.py bookmarks list --tag python --tag api

# Machine-readable output
pinboard.py bookmarks list --limit 10 --json
```

Options:

| Option | Description |
|---|---|
| `--from YYYY-MM-DD` | Earliest bookmark date |
| `--to YYYY-MM-DD` | Latest bookmark date |
| `--tag TAG` | Filter tag; repeat up to three times |
| `--limit N` | Number of results, 1–500; default 20 |
| `--offset N` | Result offset; default 0 |
| `--json` | Print JSON |

`bookmarks list` calls Pinboard's `posts/all` endpoint, which Pinboard limits to once every five minutes per user. See [Rate limits](#rate-limits).

### Add a bookmark

```bash
pinboard.py bookmarks add "https://example.com/article" \
  --title "Useful article" \
  --description "Background material for the project" \
  --tag research \
  --tag python \
  --private \
  --to-read
```

Options:

| Option | Description |
|---|---|
| `--title TITLE` | Required bookmark title |
| `--description TEXT` | Extended description or notes |
| `--tag TAG` | Bookmark tag; repeat up to 100 times |
| `--private` | Save as private |
| `--public` | Save as public |
| `--to-read` | Mark as unread/to-read |
| `--json` | Print JSON |

If neither `--private` nor `--public` is supplied, the client omits the API's `shared` parameter. Pinboard then applies the account's default privacy setting.

Pinboard's historical API terminology is confusing: the API field named `description` is the bookmark title, while `extended` is the actual description. This CLI exposes the clearer names `--title` and `--description`.

### Update a bookmark

```bash
# Change one field and preserve everything else
pinboard.py bookmarks update "https://example.com/article" \
  --description "Updated notes"

# Replace all tags
pinboard.py bookmarks update "https://example.com/article" \
  --tag reference --tag reviewed

# Remove every tag
pinboard.py bookmarks update "https://example.com/article" --clear-tags

# Change status
pinboard.py bookmarks update "https://example.com/article" --private --read
```

Options:

| Option | Description |
|---|---|
| `--title TITLE` | Replace the title |
| `--description TEXT` | Replace the extended description; `""` clears it |
| `--tag TAG` | Replace the complete tag set; repeat for multiple tags |
| `--clear-tags` | Remove all tags |
| `--private` / `--public` | Change privacy |
| `--to-read` / `--read` | Change to-read status |
| `--json` | Print JSON |

The command first retrieves the existing bookmark and then sends a replacement containing the merged fields. At least one update option is required. `--tag` replaces the entire tag set; it does not append to existing tags.

### Suggest tags

```bash
pinboard.py bookmarks suggest-tags "https://example.com/article"
pinboard.py bookmarks suggest-tags "https://example.com/article" --json
```

The response contains:

- `popular`: tags used site-wide for the URL
- `recommended`: suggestions based on your own tagging history

## Tags

### List tags

```bash
pinboard.py tags list
pinboard.py tags list --json
```

Tags are sorted by descending use count and then alphabetically.

### Rename a tag

```bash
pinboard.py tags rename ppython python
```

Renaming affects every bookmark carrying the old tag. If the destination tag already exists, Pinboard folds the old tag into it. Treat this as an account-wide mutation and review both names before running it.

Tags are normalized to lowercase. Pinboard tags cannot contain commas or whitespace.

## JSON output

Every leaf command accepts `--json`. Example:

```bash
pinboard.py bookmarks list --limit 2 --json
```

```json
{
  "count": 2,
  "bookmarks": [
    {
      "url": "https://example.com",
      "title": "Example",
      "description": "Reference site",
      "tags": ["reference"],
      "time": "2026-01-15T12:00:00Z",
      "private": true,
      "toread": false,
      "hash": "...",
      "meta": "..."
    }
  ]
}
```

Errors are written to stderr and produce exit status 1. Successful commands return 0.

## Rate limits

The published Pinboard limits are:

| Endpoint class | Limit |
|---|---|
| Most API methods | One request every 3 seconds |
| `posts/recent` | One request every 60 seconds |
| `posts/all` (`bookmarks list`) | One request every 5 minutes |

The CLI records request timestamps in:

```text
${XDG_CACHE_HOME:-~/.cache}/pinboard.py/rate-limits.json
```

This coordinates separate CLI processes, not just calls within one process.

- Short three-second delays are handled automatically.
- Long limits fail quickly with a suggested retry time.
- Pass global `--wait` to wait instead:

```bash
pinboard.py --wait bookmarks list --limit 20
```

The client also handles HTTP 429 responses and reports `Retry-After` when Pinboard supplies it. Calls made by other Pinboard clients cannot be predicted by the local timestamp file, so a server-side 429 can still occur.

## Agent skill

The repository includes [`SKILL.md`](SKILL.md), which teaches compatible AI agents how to use the CLI safely and efficiently.

Install the executable in `PATH`, configure `PINBOARD_TOKEN`, then copy or link the skill into your agent's skill directory. For Pi:

```bash
mkdir -p ~/.pi/agent/skills/pinboard
cp SKILL.md ~/.pi/agent/skills/pinboard/SKILL.md
```

Restart Pi or use `/reload` after installing the skill.

## Development

Run the executable directly:

```bash
uv run --script pinboard.py --help
```

Run tests:

```bash
uv run --with pytest pytest
```

Tests mock the HTTP layer and do not require a Pinboard token or make live API calls.

## API reference

- [Official Pinboard API documentation](https://pinboard.in/api/)
- [Pinboard token settings](https://pinboard.in/settings/password)
