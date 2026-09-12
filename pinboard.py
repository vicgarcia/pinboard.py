#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///
"""A small, agent-friendly CLI for the Pinboard.in API."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import requests


BASE_URL = "https://api.pinboard.in/v1"
DEFAULT_TIMEOUT = 20
MAX_LIST_RESULTS = 500

CLI_EPILOG = """\
Examples:
  pinboard.py bookmarks list --limit 20
  pinboard.py bookmarks list --from 2026-01-01 --to 2026-01-31 --tag python --json
  pinboard.py bookmarks add https://example.com --title "Example" --tag reference --private
  pinboard.py bookmarks update https://example.com --description "Useful reference"
  pinboard.py bookmarks suggest-tags https://example.com
  pinboard.py tags list
  pinboard.py tags rename old-tag new-tag

Authentication:
  export PINBOARD_TOKEN="username:TOKEN"       # recommended
  pinboard.py --token "username:TOKEN" tags list

The --token argument can be visible in shell history and process listings. Prefer the
PINBOARD_TOKEN environment variable when possible.
"""


class PinboardError(Exception):
    """Base exception for user-facing Pinboard errors."""


class PinboardRateLimitError(PinboardError):
    """Raised when a request would violate a long Pinboard rate limit."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    """Coordinate Pinboard rate limits across separate CLI invocations."""

    LIMITS = {"posts/all": 300.0, "posts/recent": 60.0}
    STANDARD_LIMIT = 3.0
    AUTO_WAIT_MAX = 3.1

    def __init__(self, state_path: Path | None = None):
        if state_path is None:
            cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            state_path = cache_home / "pinboard.py" / "rate-limits.json"
        self.state_path = state_path

    def _key(self, path: str) -> str:
        return path if path in self.LIMITS else "standard"

    def _limit(self, path: str) -> float:
        return self.LIMITS.get(path, self.STANDARD_LIMIT)

    def wait(self, path: str, allow_long_wait: bool = False) -> None:
        key = self._key(path)
        limit = self._limit(path)

        while True:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with self.state_path.open("a+", encoding="utf-8") as state_file:
                fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
                state_file.seek(0)
                try:
                    state = json.load(state_file)
                except (json.JSONDecodeError, ValueError):
                    state = {}

                now = time.time()
                remaining = max(0.0, float(state.get(key, 0.0)) + limit - now)
                if remaining <= 0:
                    state[key] = now
                    state_file.seek(0)
                    state_file.truncate()
                    json.dump(state, state_file)
                    state_file.flush()
                    fcntl.flock(state_file.fileno(), fcntl.LOCK_UN)
                    return

                fcntl.flock(state_file.fileno(), fcntl.LOCK_UN)

            if remaining > self.AUTO_WAIT_MAX and not allow_long_wait:
                seconds = max(1, int(remaining + 0.999))
                raise PinboardRateLimitError(
                    f"Pinboard rate limit for {path}; retry in about {seconds} seconds "
                    "or pass --wait",
                    retry_after=remaining,
                )
            time.sleep(remaining)


class PinboardApi:
    """First-party client for the Pinboard v1 JSON API."""

    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        rate_limiter: RateLimiter | None = None,
        allow_long_wait: bool = False,
    ):
        token = token.strip()
        if not token or ":" not in token:
            raise PinboardError("Pinboard token must have the form username:TOKEN")
        self._token = token
        self.session = session or requests.Session()
        self.timeout = timeout
        self.rate_limiter = rate_limiter or RateLimiter()
        self.allow_long_wait = allow_long_wait
        self.session.headers.setdefault("User-Agent", "pinboard.py-cli/1.0")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        request_params = dict(params or {})
        request_params["auth_token"] = self._token
        request_params["format"] = "json"
        self.rate_limiter.wait(path, allow_long_wait=self.allow_long_wait)

        try:
            response = self.session.get(
                f"{BASE_URL}/{path}", params=request_params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise PinboardError(f"Could not reach Pinboard: {exc.__class__.__name__}") from None

        if response.status_code == 429:
            retry_after = _retry_after(response.headers.get("Retry-After"))
            suffix = f"; retry in about {int(retry_after + 0.999)} seconds" if retry_after else ""
            raise PinboardRateLimitError(f"Pinboard rate limit exceeded{suffix}", retry_after)
        if response.status_code in (401, 403):
            raise PinboardError("Pinboard authentication failed; check PINBOARD_TOKEN or --token")
        if not response.ok:
            raise PinboardError(f"Pinboard returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError):
            raise PinboardError("Pinboard returned an invalid JSON response") from None

        if isinstance(payload, dict) and "result_code" in payload:
            if payload["result_code"] != "done":
                raise PinboardError(f"Pinboard API error: {payload['result_code']}")
        return payload

    def get_bookmarks(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"results": limit, "start": offset}
        if start_date:
            params["fromdt"] = start_date
        if end_date:
            params["todt"] = end_date
        if tags:
            params["tag"] = " ".join(tags)
        payload = self._get("posts/all", params)
        if not isinstance(payload, list):
            raise PinboardError("Unexpected response from posts/all")
        return [normalize_bookmark(item) for item in payload]

    def get_bookmark(self, url: str) -> dict[str, Any] | None:
        payload = self._get("posts/get", {"url": url})
        posts = payload.get("posts", []) if isinstance(payload, dict) else []
        return normalize_bookmark(posts[0]) if posts else None

    def add_bookmark(
        self,
        *,
        url: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        private: bool | None = None,
        toread: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "url": url,
            "description": title,
            "extended": description,
            "tags": " ".join(tags or []),
            "toread": yes_no(toread),
        }
        if private is not None:
            params["shared"] = yes_no(not private)
        self._get("posts/add", params)
        return {
            "url": url,
            "title": title,
            "description": description,
            "tags": tags or [],
            "private": private,
            "toread": toread,
        }

    def update_bookmark(
        self,
        url: str,
        *,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        private: bool | None = None,
        toread: bool | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        existing = self.get_bookmark(url)
        if existing is None:
            raise PinboardError(f"No bookmark found for URL: {url}")

        updates: list[str] = []
        merged = dict(existing)
        for field, value in (
            ("title", title),
            ("description", description),
            ("tags", tags),
            ("private", private),
            ("toread", toread),
        ):
            if value is not None:
                merged[field] = value
                updates.append(field)
        if not updates:
            raise PinboardError("No updates provided")

        params = {
            "url": url,
            "description": merged["title"],
            "extended": merged["description"],
            "tags": " ".join(merged["tags"]),
            "shared": yes_no(not merged["private"]),
            "toread": yes_no(merged["toread"]),
            "replace": "yes",
        }
        self._get("posts/add", params)
        return merged, updates

    def suggest_tags(self, url: str) -> dict[str, list[str]]:
        payload = self._get("posts/suggest", {"url": url})
        popular: list[str] = []
        recommended: list[str] = []
        for group in payload if isinstance(payload, list) else []:
            popular.extend(group.get("popular", []))
            recommended.extend(group.get("recommended", []))
        return {"popular": popular, "recommended": recommended}

    def get_tags(self) -> list[dict[str, Any]]:
        payload = self._get("tags/get")
        if not isinstance(payload, dict):
            raise PinboardError("Unexpected response from tags/get")
        tags = [{"tag": name, "count": int(count)} for name, count in payload.items()]
        return sorted(tags, key=lambda item: (-item["count"], item["tag"].lower()))

    def rename_tag(self, old_tag: str, new_tag: str) -> dict[str, str]:
        self._get("tags/rename", {"old": old_tag, "new": new_tag})
        return {"old_tag": old_tag, "new_tag": new_tag}


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def api_bool(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "yes")


def normalize_bookmark(item: dict[str, Any]) -> dict[str, Any]:
    tags = item.get("tags", "")
    if isinstance(tags, str):
        tags = [tag for tag in tags.split() if tag]
    return {
        "url": item.get("href") or item.get("url") or "",
        "title": item.get("description", ""),
        "description": item.get("extended", ""),
        "tags": tags or [],
        "time": item.get("time"),
        "private": not api_bool(item.get("shared", "yes")),
        "toread": api_bool(item.get("toread", "no")),
        "hash": item.get("hash"),
        "meta": item.get("meta"),
    }


def parse_tags(values: Iterable[str] | None) -> list[str]:
    tags: list[str] = []
    for value in values or []:
        for tag in value.split(","):
            tag = tag.strip().lower()
            if not tag:
                continue
            if any(char.isspace() for char in tag):
                raise PinboardError(f"Tags cannot contain whitespace: {tag!r}")
            if tag not in tags:
                tags.append(tag)
    if len(tags) > 3:
        raise PinboardError("Pinboard supports at most three tags when filtering bookmarks")
    return tags


def validate_date(value: str, option: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise PinboardError(f"{option} must use YYYY-MM-DD format") from None
    return value


def output_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False))


def truncate(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3].rstrip() + "..."


def render_bookmarks(bookmarks: list[dict[str, Any]]) -> None:
    if not bookmarks:
        print("No bookmarks found")
        return
    for bookmark in bookmarks:
        flags = []
        if bookmark["private"]:
            flags.append("private")
        if bookmark["toread"]:
            flags.append("to-read")
        suffix = f"  [{' · '.join(flags)}]" if flags else ""
        print(truncate(bookmark["title"] or "(untitled)", 88) + suffix)
        print(f"  {bookmark['url']}")
        if bookmark["tags"]:
            print(f"  tags: {', '.join(bookmark['tags'])}")
        if bookmark["description"]:
            print(f"  {truncate(bookmark['description'].replace(chr(10), ' '), 100)}")
        print()
    print(f"{len(bookmarks)} bookmark(s)")


def render_bookmark_result(action: str, bookmark: dict[str, Any]) -> None:
    print(f"Bookmark {action}")
    print(f"  Title: {bookmark['title']}")
    print(f"  URL:   {bookmark['url']}")
    if bookmark.get("tags"):
        print(f"  Tags:  {', '.join(bookmark['tags'])}")


def add_common_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pinboard.py",
        description="Read and manage Pinboard.in bookmarks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EPILOG,
    )
    parser.add_argument("--token", help="Pinboard API token (or set PINBOARD_TOKEN)")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait through Pinboard's one- and five-minute endpoint limits",
    )
    domains = parser.add_subparsers(dest="domain", required=True)

    bookmarks = domains.add_parser("bookmarks", help="Bookmark operations")
    bookmark_commands = bookmarks.add_subparsers(dest="action", required=True)

    list_parser = bookmark_commands.add_parser("list", help="List bookmarks")
    list_parser.add_argument("--from", dest="start_date", help="Earliest date (YYYY-MM-DD)")
    list_parser.add_argument("--to", dest="end_date", help="Latest date (YYYY-MM-DD)")
    list_parser.add_argument("--tag", action="append", help="Filter tag; repeat up to three times")
    list_parser.add_argument("--limit", type=int, default=20, help="Maximum results (default: 20)")
    list_parser.add_argument("--offset", type=int, default=0, help="Result offset (default: 0)")
    add_common_output_option(list_parser)

    add_parser = bookmark_commands.add_parser("add", help="Add a bookmark")
    add_parser.add_argument("url")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--description", default="")
    add_parser.add_argument("--tag", action="append", help="Tag; repeat for multiple tags")
    privacy = add_parser.add_mutually_exclusive_group()
    privacy.add_argument("--private", dest="private", action="store_true")
    privacy.add_argument("--public", dest="private", action="store_false")
    add_parser.set_defaults(private=None)
    add_parser.add_argument("--to-read", action="store_true", help="Mark as unread/to-read")
    add_common_output_option(add_parser)

    update_parser = bookmark_commands.add_parser("update", help="Update a bookmark by URL")
    update_parser.add_argument("url")
    update_parser.add_argument("--title")
    update_parser.add_argument("--description")
    tag_group = update_parser.add_mutually_exclusive_group()
    tag_group.add_argument("--tag", action="append", help="Replacement tag; repeat for multiple")
    tag_group.add_argument("--clear-tags", action="store_true")
    privacy = update_parser.add_mutually_exclusive_group()
    privacy.add_argument("--private", dest="private", action="store_true")
    privacy.add_argument("--public", dest="private", action="store_false")
    update_parser.set_defaults(private=None)
    reading = update_parser.add_mutually_exclusive_group()
    reading.add_argument("--to-read", dest="toread", action="store_true")
    reading.add_argument("--read", dest="toread", action="store_false")
    update_parser.set_defaults(toread=None)
    add_common_output_option(update_parser)

    suggest_parser = bookmark_commands.add_parser("suggest-tags", help="Suggest tags for a URL")
    suggest_parser.add_argument("url")
    add_common_output_option(suggest_parser)

    tags = domains.add_parser("tags", help="Tag operations")
    tag_commands = tags.add_subparsers(dest="action", required=True)
    tags_list = tag_commands.add_parser("list", help="List tags and usage counts")
    add_common_output_option(tags_list)
    rename = tag_commands.add_parser("rename", help="Rename or merge a tag account-wide")
    rename.add_argument("old_tag")
    rename.add_argument("new_tag")
    add_common_output_option(rename)
    return parser


def run_command(api: PinboardApi, args: argparse.Namespace) -> int:
    if args.domain == "bookmarks" and args.action == "list":
        if not 1 <= args.limit <= MAX_LIST_RESULTS:
            raise PinboardError(f"--limit must be between 1 and {MAX_LIST_RESULTS}")
        if args.offset < 0:
            raise PinboardError("--offset cannot be negative")
        start = validate_date(args.start_date, "--from") if args.start_date else None
        end = validate_date(args.end_date, "--to") if args.end_date else None
        if start and end and start > end:
            raise PinboardError("--from must not be later than --to")
        tags = parse_tags(args.tag)
        result = api.get_bookmarks(
            start_date=start, end_date=end, tags=tags, limit=args.limit, offset=args.offset
        )
        if args.json:
            output_json({"count": len(result), "bookmarks": result})
        else:
            render_bookmarks(result)
        return 0

    if args.domain == "bookmarks" and args.action == "add":
        result = api.add_bookmark(
            url=args.url.strip(),
            title=args.title.strip(),
            description=args.description.strip(),
            tags=parse_tags_unlimited(args.tag),
            private=args.private,
            toread=args.to_read,
        )
        if args.json:
            output_json({"bookmark": result, "message": "bookmark created"})
        else:
            render_bookmark_result("created", result)
        return 0

    if args.domain == "bookmarks" and args.action == "update":
        tags = [] if args.clear_tags else (parse_tags_unlimited(args.tag) if args.tag is not None else None)
        result, updates = api.update_bookmark(
            args.url.strip(),
            title=args.title.strip() if args.title is not None else None,
            description=args.description.strip() if args.description is not None else None,
            tags=tags,
            private=args.private,
            toread=args.toread,
        )
        if args.json:
            output_json({"bookmark": result, "updates_applied": updates})
        else:
            render_bookmark_result("updated", result)
            print(f"  Updated: {', '.join(updates)}")
        return 0

    if args.domain == "bookmarks" and args.action == "suggest-tags":
        result = api.suggest_tags(args.url.strip())
        if args.json:
            output_json(result)
        else:
            print("Popular:     " + (", ".join(result["popular"]) or "(none)"))
            print("Recommended: " + (", ".join(result["recommended"]) or "(none)"))
        return 0

    if args.domain == "tags" and args.action == "list":
        result = api.get_tags()
        if args.json:
            output_json({"count": len(result), "tags": result})
        elif not result:
            print("No tags found")
        else:
            width = max(len(item["tag"]) for item in result)
            for item in result:
                print(f"{item['tag']:<{width}}  {item['count']:>6}")
            print(f"\n{len(result)} tag(s)")
        return 0

    if args.domain == "tags" and args.action == "rename":
        old_tag = normalize_single_tag(args.old_tag)
        new_tag = normalize_single_tag(args.new_tag)
        if old_tag == new_tag:
            raise PinboardError("Old and new tags cannot be the same")
        result = api.rename_tag(old_tag, new_tag)
        if args.json:
            output_json(result)
        else:
            print(f"Renamed tag {old_tag!r} to {new_tag!r}")
        return 0

    raise PinboardError("Unknown command")


def parse_tags_unlimited(values: Iterable[str] | None) -> list[str]:
    tags: list[str] = []
    for value in values or []:
        for raw_tag in value.split(","):
            tag = normalize_single_tag(raw_tag)
            if tag and tag not in tags:
                tags.append(tag)
    if len(tags) > 100:
        raise PinboardError("A bookmark can have at most 100 tags")
    return tags


def normalize_single_tag(value: str) -> str:
    tag = value.strip().lower()
    if not tag:
        raise PinboardError("Tag cannot be empty")
    if any(char.isspace() for char in tag) or "," in tag:
        raise PinboardError(f"Tags cannot contain commas or whitespace: {tag!r}")
    if len(tag) > 255:
        raise PinboardError("Tags cannot exceed 255 characters")
    return tag


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token = args.token or os.environ.get("PINBOARD_TOKEN")
    if not token:
        print(
            "Error: Pinboard token required; set PINBOARD_TOKEN or use --token",
            file=sys.stderr,
        )
        return 1
    try:
        api = PinboardApi(token, allow_long_wait=args.wait)
        return run_command(api, args)
    except PinboardError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
