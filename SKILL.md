---
name: pinboard
description: Read and manage bookmarks and tags in Pinboard.in. Use when the user wants to list, find, save, update, classify, or analyze Pinboard bookmarks; get tag suggestions; inspect tag usage; or rename Pinboard tags.
compatibility: Requires the 'pinboard.py' executable in PATH and a PINBOARD_TOKEN environment variable or a token supplied with global --token.
---

# Pinboard Tool

Use the `pinboard.py` CLI to read and manage the user's Pinboard.in bookmarks.

## Authentication

Prefer the environment variable:

```bash
export PINBOARD_TOKEN="username:TOKEN"
```

A global argument is also supported:

```bash
pinboard.py --token "username:TOKEN" tags list
```

Prefer `PINBOARD_TOKEN` because command arguments may appear in shell history and process listings. Never echo, quote back, log, or expose the token in an answer.

Global options must precede the domain:

```bash
pinboard.py --wait bookmarks list
```

The `--json` option follows the leaf command:

```bash
pinboard.py bookmarks list --json
```

## Commands

| Command | Purpose | Mutates account? |
|---|---|---|
| `bookmarks list` | Retrieve bookmarks with filters | No |
| `bookmarks add` | Save a bookmark | Yes |
| `bookmarks update` | Change a bookmark by URL | Yes |
| `bookmarks suggest-tags` | Get popular and personalized tag suggestions | No |
| `tags list` | List tags and usage counts | No |
| `tags rename` | Rename or merge a tag across all bookmarks | Yes, account-wide |

Use `--json` for machine-readable output.

## List bookmarks

```bash
pinboard.py bookmarks list --limit 20 --json
pinboard.py bookmarks list --from 2026-01-01 --to 2026-01-31 --limit 50 --json
pinboard.py bookmarks list --tag python --tag api --limit 20 --json
pinboard.py bookmarks list --offset 20 --limit 20 --json
```

Options:

- `--from YYYY-MM-DD`: earliest date
- `--to YYYY-MM-DD`: latest date
- `--tag TAG`: repeat up to three times
- `--limit N`: 1–500, default 20
- `--offset N`: pagination offset
- `--json`: structured output

Always choose the smallest useful limit. The command uses Pinboard's `posts/all` endpoint, which is limited to one call every five minutes. Reuse results already present in context rather than immediately listing again.

## Add a bookmark

```bash
pinboard.py bookmarks add "https://example.com/article" \
  --title "Article title" \
  --description "Why this is useful" \
  --tag research \
  --tag python \
  --private \
  --to-read \
  --json
```

- `--title` is required.
- Repeat `--tag` for multiple tags. Comma-separated input is also accepted.
- Use `--private` or `--public` only when the user states a preference.
- If neither privacy flag is supplied, Pinboard applies the user's account default.
- `--to-read` marks the bookmark unread/to-read.

Adding changes the user's external account. Ensure the user requested the save and verify the URL, title, privacy, and tags before executing it.

## Update a bookmark

```bash
pinboard.py bookmarks update URL --title "New title" --json
pinboard.py bookmarks update URL --description "New notes" --json
pinboard.py bookmarks update URL --tag python --tag reference --json
pinboard.py bookmarks update URL --clear-tags --json
pinboard.py bookmarks update URL --private --to-read --json
pinboard.py bookmarks update URL --public --read --json
```

Only supplied fields change. Important semantics:

- `--tag` replaces the complete tag set; it does not append.
- `--clear-tags` removes all tags.
- `--description ""` clears the description.
- `--private` and `--public` are mutually exclusive.
- `--to-read` and `--read` are mutually exclusive.

When the user asks to add one tag without replacing existing tags, first obtain the bookmark data from a recent list result. Preserve the existing tags and pass the full combined set to `update`.

Updating changes the user's external account. Confirm the intended values when the request is ambiguous.

## Suggest tags

```bash
pinboard.py bookmarks suggest-tags URL --json
```

Returns `popular` tags used site-wide and `recommended` tags based on the user's history. Suggestions do not change the bookmark.

## List tags

```bash
pinboard.py tags list --json
```

The output is sorted by descending bookmark count. Use it to summarize topics, check whether a tag exists, or identify possible duplicate tags.

## Rename or merge a tag

```bash
pinboard.py tags rename OLD_TAG NEW_TAG --json
```

This changes every bookmark carrying `OLD_TAG`. If `NEW_TAG` already exists, Pinboard merges the old tag into it. Show the proposed old and new names and obtain confirmation before executing unless the user explicitly requested that exact rename.

## Pinboard terminology

The upstream API uses historical Delicious field names. CLI and JSON output normalize them:

| CLI/JSON | Pinboard API | Meaning |
|---|---|---|
| `title` | `description` | Displayed bookmark title |
| `description` | `extended` | Notes or extended description |
| `private` | inverse of `shared` | Bookmark visibility |
| `toread` | `toread` | Unread/to-read status |

Do not describe the JSON `description` field as the title.

## Rate limits

The CLI proactively coordinates rate limits across invocations using a file under the user's cache directory:

- Most methods: one request every 3 seconds; short waits happen automatically.
- `posts/all` / `bookmarks list`: one request every 5 minutes.
- Long waits fail with retry guidance unless global `--wait` is passed.

Do not use `--wait` reflexively: a list call can block for almost five minutes. Prefer existing results, wait only when the user needs a fresh request, and tell the user if a rate limit prevents immediate retrieval.

## Workflow patterns

### Find and summarize recent bookmarks

```bash
pinboard.py bookmarks list --from 2026-01-01 --limit 50 --json
```

Parse the returned bookmarks and summarize only the requested dimensions. Do not issue another list call to regroup the same data.

### Suggest tags, then save

```bash
pinboard.py bookmarks suggest-tags URL --json
# Review suggestions with the user when tag intent is unclear.
pinboard.py bookmarks add URL --title TITLE --tag TAG --json
```

### Inspect tags before a rename

```bash
pinboard.py tags list --json
# Confirm exact account-wide mutation.
pinboard.py tags rename old-tag new-tag --json
```

## Safety and context discipline

- Never expose `PINBOARD_TOKEN` or authenticated request URLs.
- Use `--json` when processing results programmatically.
- Use small `--limit` values and increase only when analysis requires it.
- Reuse prior list results because `bookmarks list` has a five-minute limit.
- Treat add, update, and rename as external account changes.
- Tag rename is account-wide and may merge tags; confirm it explicitly.
- This version intentionally has no bookmark or tag deletion command.
