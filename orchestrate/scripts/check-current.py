#!/usr/bin/env python3
"""Say in one line whether this copy of the skill is current.

The skill's SKILL.md carries a stamp under metadata: the commit it was
published from, and that commit's date. This script compares it with the
stamp on the published page, at most once a day, and prints one line:

  current                       the stamps match
  checked <time>                a check ran less than a day ago; no fetch
  newer <stamp> auto            a newer version is published; take it
  newer <stamp> confirm         a newer version is published; ask the person once
  newer <stamp> pin             a newer version is published; keep this copy
  check could not run: <why>    go on with this copy
  source tree, no check         this SKILL.md has no stamp

The three words auto, confirm and pin come from a file named UPDATE in the
skill's directory. It holds one of them. No UPDATE file means auto.

The time of the last check and the stamp it saw are kept in .update-check in
the skill's directory. Delete that file to check again at once.

Exit 0 in every case but one: a malformed stamp in this SKILL.md exits 2.
"""
import argparse
import datetime
import pathlib
import re
import sys
import urllib.request

PUBLISHED = "https://raw.githubusercontent.com/wagmi-fyi/skills/main"
STAMP = re.compile(r'^([0-9a-f]{7,40}) (\d{4}-\d{2}-\d{2})$')
NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
WORDS = ("auto", "confirm", "pin")
DAY = datetime.timedelta(days=1)
TIMEOUT = 5


class Malformed(Exception):
    pass


def front_matter(text):
    """Return (name, stamp) from the front matter. Stamp is None when absent."""
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        raise Malformed("no front matter")
    name, stamp, in_meta = None, None, False
    for line in lines[1:lines.index("---", 1)]:
        if line.startswith("name:"):
            name = line[5:].strip().strip('"')
        if line.startswith("version:"):
            raise Malformed("version is a top-level field; it belongs under metadata")
        if line == "metadata:":
            in_meta = True
            continue
        if in_meta and not line.startswith("  "):
            in_meta = False
        if in_meta and line.startswith("  version:"):
            stamp = line[10:].strip().strip('"')
            if not STAMP.match(stamp):
                raise Malformed(f"malformed stamp: {stamp}")
    return name, stamp


def read_cache(path):
    """Return the time of the last check, or None."""
    try:
        first = path.read_text().split("\n")[0]
        when = datetime.datetime.fromisoformat(first.removeprefix("checked "))
    except (OSError, ValueError):
        return None
    return when if when.tzinfo else None


def write_cache(path, now, seen):
    try:
        path.write_text(f"checked {now.isoformat(timespec='seconds')}\nseen {seen}\n")
    except OSError:
        pass


def fetch_stamp(url):
    req = urllib.request.Request(url, headers={"User-Agent": "check-current"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        text = resp.read().decode("utf-8")
    try:
        _, stamp = front_matter(text)
    except Malformed as e:
        raise ValueError(f"the published page: {e}")
    if stamp is None:
        raise ValueError("the published page carries no stamp")
    return stamp


def check(skill, base, now):
    name, stamp = front_matter((skill / "SKILL.md").read_text())
    if stamp is None:
        return "source tree, no check"
    if not name or not NAME.match(name):
        raise Malformed(f"malformed name: {name}")
    cache = skill / ".update-check"
    last = read_cache(cache)
    if last is not None and datetime.timedelta(0) <= now - last < DAY:
        return f"checked {last.isoformat(timespec='seconds')}"
    try:
        seen = fetch_stamp(f"{base.rstrip('/')}/{name}/SKILL.md")
    except (OSError, ValueError) as e:
        return f"check could not run: {e}"
    write_cache(cache, now, seen)
    if seen == stamp:
        return "current"
    try:
        word = (skill / "UPDATE").read_text().strip()
    except FileNotFoundError:
        word = "auto"
    except OSError as e:
        return f"check could not run: {e}"
    if word not in WORDS:
        return f"check could not run: UPDATE holds {word!r}, not auto, confirm or pin"
    return f"newer {seen} {word}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parent.parent,
                    help="the skill's directory (default: the one above this script)")
    ap.add_argument("--published", default=PUBLISHED,
                    help="where the published pages are read from (default: %(default)s)")
    a = ap.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        print(check(a.skill, a.published, now))
    except Malformed as e:
        print(f"check could not run: {e}")
        sys.exit(2)
    except OSError as e:
        print(f"check could not run: {e}")


if __name__ == "__main__":
    main()
