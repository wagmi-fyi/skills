#!/usr/bin/env python3
"""Refuse a published tree whose front pages disagree about staying current.

Checks, per PUBLISHING.md ("The stamp" and "The activation paragraph and the check script"):
  - every front page carries the update-check paragraph, byte for byte the same;
  - every published front page carries a well-formed stamp;
  - a skill sourced in this repository carries the stamp of the last commit that
    changed its tree, the stamp commits themselves left out;
  - every front page stays under the format's 500-line ceiling;
  - every skill carries scripts/check-current.py, byte for byte the same,
    executable, and answering --help with exit 0.

Usage:
  uv run tools/check-front-pages.py [--repo DIR] [SKILL_DIR ...] [--unstamped DIR ...]

With no SKILL_DIR, the skills listed in .claude-plugin/plugin.json are read.
--unstamped names a source tree: it must carry the paragraph and no stamp.
Prints JSON on stdout. Exit 0 is a pass, exit 1 a refusal.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

LEAD = "**First, check that this copy is current.**"
STAMP = re.compile(r'^  version: "([0-9a-f]{7,40}) (\d{4}-\d{2}-\d{2})"$')
MAX_LINES = 500
SCRIPT = "scripts/check-current.py"


def front_matter(text):
    lines = text.split("\n")
    if lines[0] != "---":
        return None
    end = lines.index("---", 1)
    return lines[1:end]


def stamp_of(fm):
    """Return (sha, date) from metadata.version, or None. Raise on a malformed one."""
    in_meta = False
    for line in fm:
        if line == "metadata:":
            in_meta = True
            continue
        if in_meta and not line.startswith("  "):
            in_meta = False
        if line.startswith("version:"):
            raise ValueError("version is a top-level field; it belongs under metadata")
        if in_meta and line.startswith("  version:"):
            m = STAMP.match(line)
            if not m:
                raise ValueError(f"malformed stamp: {line.strip()}")
            return m.group(1), m.group(2)
    return None


def paragraph_of(text):
    for line in text.split("\n"):
        if line.startswith(LEAD):
            return line
    return None


def check_script(d, scripts, problems):
    script = d / SCRIPT
    if not script.is_file():
        problems.append(f"{SCRIPT} missing")
        return
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    scripts.setdefault(digest, []).append(str(d))
    if not os.access(script, os.X_OK):
        problems.append(f"{SCRIPT} is not executable")
        return
    r = subprocess.run([str(script), "--help"], capture_output=True, text=True)
    if r.returncode != 0 or "UPDATE" not in r.stdout:
        problems.append(f"{SCRIPT} --help exits {r.returncode}")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def sourced_here(repo):
    """Top-level directories of this repository that hold a SKILL.md."""
    return {d.name for d in repo.iterdir() if (d / "SKILL.md").is_file()}


def is_stamp_only(repo, sha, skill):
    diff = git(repo, "show", "--format=", "--unified=0", sha, "--", skill)
    files = re.findall(r"^\+\+\+ b/(.*)$", diff, re.M) + re.findall(r"^--- a/(.*)$", diff, re.M)
    if set(files) - {f"{skill}/SKILL.md"}:
        return False
    changed = [l for l in diff.split("\n")
               if l[:1] in ("+", "-") and not l.startswith(("+++", "---"))]
    return (any(re.match(r'^[+-]  version: ', l) for l in changed)
            and all(re.match(r'^[+-](  version: |metadata:$)', l) for l in changed))


def expected_stamp(repo, skill):
    if git(repo, "status", "--porcelain", "--", skill).strip():
        raise ValueError("uncommitted changes in the skill tree")
    for sha in git(repo, "log", "--format=%H", "--", skill).split():
        if is_stamp_only(repo, sha, skill):
            continue
        ts = int(git(repo, "log", "-1", "--format=%ct", sha).strip())
        day = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
        return sha, day
    raise ValueError("no commit changes the skill tree")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills", nargs="*")
    ap.add_argument("--repo", default=pathlib.Path(__file__).resolve().parent.parent)
    ap.add_argument("--unstamped", action="append", default=[])
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    skills = a.skills or [s.removeprefix("./") for s in
                          json.loads((repo / ".claude-plugin/plugin.json").read_text())["skills"]]
    home = sourced_here(repo)
    results, paragraphs, scripts = [], {}, {}

    pages = [(pathlib.Path(s) if pathlib.Path(s).is_absolute() else repo / s, True) for s in skills]
    pages += [(pathlib.Path(s).resolve(), False) for s in a.unstamped]
    for d, published in pages:
        r = {"skill": str(d), "problems": []}
        text = (d / "SKILL.md").read_text()
        if len(text.split("\n")) > MAX_LINES:
            r["problems"].append(f"over {MAX_LINES} lines")
        para = paragraph_of(text)
        if para is None:
            r["problems"].append("update-check paragraph missing")
        else:
            paragraphs.setdefault(para, []).append(str(d))
        check_script(d, scripts, r["problems"])
        try:
            fm = front_matter(text)
            st = stamp_of(fm)
            if not published:
                if st:
                    r["problems"].append("a source tree carries a stamp")
            elif st is None:
                r["problems"].append("no stamp")
            else:
                r["stamp"] = " ".join(st)
                if d.parent == repo and d.name in home:
                    sha, day = expected_stamp(repo, d.name)
                    if not sha.startswith(st[0]) or day != st[1]:
                        r["problems"].append(f"stamp should be {sha[:7]} {day}")
        except (ValueError, subprocess.CalledProcessError) as e:
            r["problems"].append(str(e))
        results.append(r)

    problems = sum(len(r["problems"]) for r in results)
    if len(paragraphs) > 1:
        problems += 1
    if len(scripts) > 1:
        problems += 1
    out = {"pass": problems == 0, "paragraph_variants": len(paragraphs),
           "variants": list(paragraphs.values()), "script_variants": len(scripts),
           "scripts": list(scripts.values()), "pages": results}
    json.dump(out, sys.stdout, indent=2)
    print()
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
