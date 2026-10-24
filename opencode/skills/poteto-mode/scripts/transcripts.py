#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def workspace(path: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return path.resolve()


def local_time(value: int | float | str) -> tuple[dt.datetime, str]:
    if isinstance(value, str):
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        stamp = stamp.astimezone()
    else:
        stamp = dt.datetime.fromtimestamp(
            float(value) / (1000 if float(value) > 10_000_000_000 else 1),
            tz=dt.timezone.utc,
        ).astimezone()
    return stamp, stamp.isoformat(timespec="seconds")


def opencode_dirs(db_path: Path) -> list[Path]:
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(
            "SELECT directory FROM session UNION SELECT directory FROM session_v2"
        ).fetchall()
    return sorted({Path(row[0]).resolve() for row in rows if row[0]})


def opencode_sessions(directory: Path) -> list[dict]:
    result = subprocess.run(
        ["opencode", "session", "list", "--format", "json", "-n", "500"],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)
    sessions = []
    for row in rows:
        _, updated = local_time(row["updated"])
        sessions.append(
            {
                "id": row["id"],
                "updated": updated,
                "sort_time": row["updated"],
                "title": (row.get("title") or "(untitled)")
                .replace("\t", " ")
                .replace("\n", " "),
                "directory": str(Path(row.get("directory") or directory).resolve()),
                "harness": "opencode",
            }
        )
    return sessions


def prime_sessions(session_dir: Path, root: Path | None) -> list[dict]:
    sessions = []
    for path in session_dir.glob("*.jsonl"):
        try:
            with path.open(encoding="utf-8") as stream:
                header = json.loads(next(stream))
            if header.get("type") != "session" or not header.get("cwd"):
                continue
            cwd = Path(header["cwd"]).resolve()
            if root is not None and cwd != root and root not in cwd.parents:
                continue
            stamp, updated = local_time(path.stat().st_mtime * 1000)
            message_rows = []
            title = "(untitled)"
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("type") == "message":
                        message = row.get("message", {})
                        message_rows.append(message)
                        if title == "(untitled)" and message.get("role") == "user":
                            title = first_user_text(message) or title
            sessions.append(
                {
                    "id": header.get("id", path.stem),
                    "updated": updated,
                    "sort_time": path.stat().st_mtime * 1000,
                    "directory": str(Path(header["cwd"]).resolve()),
                    "harness": "prime",
                    "file": path,
                    "title": title[:120].replace("\t", " ").replace("\n", " "),
                    "messages": message_rows,
                }
            )
        except (
            OSError,
            ValueError,
            StopIteration,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            continue
    return sessions


def first_user_text(message: dict) -> str:
    content = message.get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def detect_harness(requested: str | None) -> str:
    if requested:
        return requested
    if (
        shutil.which("opencode")
        and (Path.home() / ".local/share/opencode/opencode.db").is_file()
    ):
        return "opencode"
    if (Path.home() / ".prime/agent/sessions").is_dir():
        return "prime"
    raise SystemExit("cannot detect opencode or prime-agent sessions; pass --harness")


def collect_sessions(harness: str, root: Path, all_workspaces: bool) -> list[dict]:
    if harness == "prime":
        scope = None if all_workspaces else root
        return prime_sessions(Path.home() / ".prime/agent/sessions", scope)
    if all_workspaces:
        db = Path.home() / ".local/share/opencode/opencode.db"
        dirs = opencode_dirs(db)
    else:
        dirs = [root]
    sessions = []
    seen = set()
    for directory in dirs:
        for session in opencode_sessions(directory):
            if session["id"] not in seen:
                sessions.append(session)
                seen.add(session["id"])
    return sessions


def messages(session: dict) -> list[dict]:
    if session["harness"] == "opencode":
        with tempfile.TemporaryFile(mode="w+b") as export:
            subprocess.run(
                ["opencode", "session", "export", session["id"]],
                cwd=session["directory"],
                check=True,
                stdout=export,
            )
            export.seek(0)
            return json.load(export).get("messages", [])
    return session["messages"]


def short_json(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= 200 else text[:197] + "..."


def render(session: dict) -> str | None:
    """Return the session as markdown, or None when it has no messages."""
    lines = [
        f"# {session['title']}",
        "",
        f"Session: `{session['id']}`",
        f"Updated: {session['updated']}",
        "",
    ]
    for message in messages(session):
        role = message.get("type") or message.get("role")
        content = message.get("content", [])
        if role == "user":
            text = message.get("text") or first_user_text(message)
            if text.strip():
                user_text = text.strip()
                lines.extend(["## User", "", user_text, ""])
            continue
        if role != "assistant" or not isinstance(content, list):
            continue
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") in ("tool", "toolCall"):
                name = item.get("name", "unknown")
                state = item.get("state") or {}
                arguments = state.get("input", item.get("arguments", {}))
                parts.append(f"[tool {name}] {short_json(arguments)}")
                continue
        text = "\n".join(part for part in parts if part).strip()
        if text:
            lines.extend(["## Assistant", "", text, ""])
    if len(lines) == 5:
        return None
    return "\n".join(lines).rstrip() + "\n"


def filter_sessions(
    sessions: list[dict], root: Path, days: int, all_workspaces: bool, ids: list[str]
) -> list[dict]:
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=days)
    selected = []
    for session in sessions:
        stamp, _ = local_time(session["sort_time"])
        if not all_workspaces:
            directory = Path(session["directory"])
            in_scope = directory == root or root in directory.parents
            if not in_scope:
                continue
        if stamp < cutoff:
            continue
        if ids and session["id"] not in ids:
            continue
        selected.append(session)
    return sorted(selected, key=lambda item: item["sort_time"], reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List and export workspace session transcripts."
    )
    parser.add_argument("--harness", choices=("opencode", "prime"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument(
        "--harness", choices=("opencode", "prime"), default=argparse.SUPPRESS
    )
    list_parser.add_argument("--days", type=int, default=7)
    list_parser.add_argument("--dir", type=Path)
    list_parser.add_argument("--all-workspaces", action="store_true")
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument(
        "--harness", choices=("opencode", "prime"), default=argparse.SUPPRESS
    )
    export_parser.add_argument("--days", type=int, default=7)
    export_parser.add_argument("--dir", type=Path)
    export_parser.add_argument("--out", type=Path)
    export_parser.add_argument("--all-workspaces", action="store_true")
    export_parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days must be zero or greater")
    harness = detect_harness(args.harness)
    root = workspace((args.dir or Path.cwd()).resolve())
    sessions = filter_sessions(
        collect_sessions(harness, root, args.all_workspaces),
        root,
        args.days,
        args.all_workspaces,
        getattr(args, "ids", []),
    )
    if args.command == "list":
        for item in sessions:
            print(
                f"{item['id']}\t{item['updated']}\t{item['title']}\t{item['directory']}"
            )
        return
    default_out = Path(tempfile.gettempdir()) / "pstack/transcripts" / root.name
    out = (args.out or default_out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for item in sessions:
        filename = f"{item['updated']}-{item['id']}.md"
        filename = re.sub(r"[^A-Za-z0-9_.:+-]", "_", filename)
        text = render(item)
        if text is None:
            continue
        path = out / filename
        path.write_text(text, encoding="utf-8")
        files.append(path)
    print(f"Output directory: {out}")
    for path in files:
        print(path)


if __name__ == "__main__":
    main()
