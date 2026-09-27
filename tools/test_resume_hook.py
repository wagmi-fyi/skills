#!/usr/bin/env python3
"""
Self-contained tests for orchestrate/scripts/resume-hook.

  * The exact phrase prints the skill's front page as the context. Every other
    prompt prints nothing.
  * The cap counts characters. A page over it, and a missing page, each print
    one fixed line as the context and send the same line to the journal.
  * --phrase prints the phrase the hook matches.
  * --check reads registered and not registered on scratch settings files.

The script runs as a byte-for-byte copy in a scratch skill directory, beside a
planted page. Each reading has a known-answer control: a planted fault that the
same reading refuses. No real settings file, journal or session is touched.

Run, from the repository root:
    uv run --no-project python3 -m unittest tools.test_resume_hook
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "orchestrate" / "scripts" / "resume-hook"
PHRASE = "/orchestrate full resume operation please"
CAP = 9500
LOGGER = """#!/bin/sh
printf '%s\\n' "$*" >> "$LOGGER_OUT"
"""


def event(prompt):
    return json.dumps({"session_id": "probe", "hook_event_name": "UserPromptSubmit",
                       "prompt": prompt})


def settings(command):
    return json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": command}]}]}})


class ResumeHook(unittest.TestCase):

    def setUp(self):
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix="resume-hook-"))
        self.addCleanup(shutil.rmtree, self.scratch)
        self.skill = self.scratch / "orchestrate"
        (self.skill / "scripts").mkdir(parents=True)
        self.hook = self.skill / "scripts" / "resume-hook"
        self.plant(HOOK.read_text())
        self.page = self.skill / "SKILL.md"
        self.bin = self.scratch / "bin"
        self.bin.mkdir()
        (self.bin / "logger").write_text(LOGGER)
        (self.bin / "logger").chmod(0o755)
        self.log = self.scratch / "log"
        self.home = self.scratch / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.machine = self.scratch / "managed-settings.json"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        LOGGER_OUT=str(self.log), HOME=str(self.home),
                        RESUME_HOOK_MANAGED_SETTINGS=str(self.machine))
        self.env.pop("CLAUDE_CONFIG_DIR", None)

    def plant(self, source):
        self.hook.write_text(source)
        self.hook.chmod(0o755)

    def run_hook(self, stdin, *args, env=None, argv0=None):
        self.log.write_text("")
        cmd = [str(argv0 or self.hook), *args]
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              env=env or self.env, timeout=10)

    def context(self, result):
        if not result.stdout:
            return None
        out = json.loads(result.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        return out["hookSpecificOutput"]["additionalContext"]

    def over_line(self, chars):
        return (f"The resume hook did not load the orchestrate skill: {self.page} is "
                f"{chars} characters, over the hook's cap of {CAP}. Open that file and "
                "run its resume operation by hand.")

    def miss_line(self):
        return (f"The resume hook did not load the orchestrate skill: there is no page at "
                f"{self.page}. Load the orchestrate skill and run its resume operation by hand.")

    # The page

    def test_the_exact_phrase_prints_the_page_whole(self):
        self.page.write_text("# Orchestrate\n" + "x" * 4669 + "\nPAGE-END\n")
        for prompt in (PHRASE, "  \n" + PHRASE + "\n  "):
            with self.subTest(prompt=prompt):
                r = self.run_hook(event(prompt))
                self.assertEqual(r.returncode, 0)
                self.assertEqual(r.stderr, "")
                ctx = self.context(r)
                self.assertIn(f"Base directory for this skill: {self.skill}", ctx)
                self.assertEqual(ctx.splitlines()[-1], "PAGE-END")
                self.assertLess(len(ctx), 10000)
                self.assertEqual(self.log.read_text(), "")

    def test_the_page_at_the_cap_fits_the_harness_limit_with_its_head(self):
        self.page.write_text("x" * CAP)
        ctx = self.context(self.run_hook(event(PHRASE)))
        self.assertEqual(ctx.splitlines()[-1], "x" * CAP)
        self.assertLess(len(ctx), 10000)

    def test_every_other_prompt_prints_nothing(self):
        self.page.write_text("# Orchestrate\n")
        for stdin in (event("hello there, please look at the bus"),
                      event(f"what does {PHRASE} do?"),
                      event(f"{PHRASE} and then stop"),
                      event("/orchestrate resume"),
                      event(""),
                      f"not json at all {PHRASE}",
                      ""):
            with self.subTest(stdin=stdin[:48]):
                r = self.run_hook(stdin)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
                self.assertEqual(self.log.read_text(), "")

    def test_control_a_hook_that_matches_on_contains_is_refused(self):
        self.plant(HOOK.read_text().replace('[ "$prompt" = "$PHRASE" ] || exit 0', ":"))
        self.page.write_text("# Orchestrate\n")
        self.assertNotEqual(self.run_hook(event(f"{PHRASE} and then stop")).stdout, "")

    # The cap and the two fixed lines

    def test_a_page_one_character_over_the_cap_is_refused_with_its_line(self):
        self.page.write_text("y" * (CAP + 1))
        r = self.run_hook(event(PHRASE))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.context(r), self.over_line(CAP + 1))
        self.assertEqual(self.log.read_text(),
                         f"-t orchestrate-resume-hook -p user.warning -- {self.over_line(CAP + 1)}\n")

    def test_the_cap_counts_characters(self):
        self.page.write_text("é" * CAP, encoding="utf-8")
        self.assertEqual(self.page.stat().st_size, 2 * CAP)
        ctx = self.context(self.run_hook(event(PHRASE)))
        self.assertEqual(ctx.splitlines()[-1], "é" * CAP)

    def test_control_a_hook_that_counts_bytes_refuses_that_page(self):
        self.plant(HOOK.read_text().replace('wc -m < "$page"', 'wc -c < "$page"'))
        self.page.write_text("é" * CAP, encoding="utf-8")
        self.assertEqual(self.context(self.run_hook(event(PHRASE))), self.over_line(2 * CAP))

    def test_a_missing_page_prints_its_line(self):
        r = self.run_hook(event(PHRASE))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.context(r), self.miss_line())
        self.assertEqual(self.log.read_text(),
                         f"-t orchestrate-resume-hook -p user.warning -- {self.miss_line()}\n")

    def test_control_a_hook_without_its_journal_line_journals_nothing(self):
        self.plant(re.sub(r'.*logger -t "\$LOG_TAG".*\n', "", HOOK.read_text()))
        self.run_hook(event(PHRASE))
        self.assertEqual(self.log.read_text(), "")

    def test_with_no_logger_the_line_still_reaches_the_model(self):
        tools = self.scratch / "nologger"
        tools.mkdir()
        for name in ("jq", "cat", "wc", "tr", "dirname"):
            (tools / name).symlink_to(shutil.which(name))
        env = dict(self.env, PATH=str(tools))
        self.page.write_text("y" * (CAP + 1))
        r = subprocess.run([shutil.which("bash"), str(self.hook)], input=event(PHRASE),
                           capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.context(r), self.over_line(CAP + 1))

    # --phrase

    def test_phrase_prints_the_phrase_the_hook_matches(self):
        r = self.run_hook("", "--phrase")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, PHRASE + "\n", ""))
        self.page.write_text("# Orchestrate\nPAGE-END\n")
        ctx = self.context(self.run_hook(event(r.stdout.strip())))
        self.assertEqual(ctx.splitlines()[-1], "PAGE-END")

    def test_control_a_phrase_one_word_off_loads_nothing(self):
        self.page.write_text("# Orchestrate\n")
        self.assertEqual(self.run_hook(event(PHRASE.replace("full ", ""))).stdout, "")

    # --check

    def check(self, argv0=None):
        r = self.run_hook("", "--check", argv0=argv0)
        self.assertEqual(r.stderr, "")
        self.assertEqual(r.stdout.count("\n"), 1, r.stdout)
        return r.returncode, r.stdout

    def account_file(self):
        return self.home / ".claude" / "settings.json"

    def test_check_reads_neither_file_as_not_ready(self):
        self.page.write_text("# Orchestrate\n")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn(f"account no file in {self.account_file()}", out)
        self.assertIn(f"machine no file in {self.machine}", out)
        self.assertIn("not ready: registered in neither settings file", out)

    def test_check_reads_the_account_tier(self):
        self.page.write_text("# Orchestrate\n")
        self.account_file().write_text(settings(str(self.hook)))
        self.machine.write_text(settings("/usr/local/bin/somebody-elses"))
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertIn(f"account registered in {self.account_file()}", out)
        self.assertIn(f"machine not registered in {self.machine}", out)
        self.assertIn("page 14 of 9500 characters; ready", out)

    def test_check_reads_the_machine_tier_through_a_link(self):
        self.page.write_text("# Orchestrate\n")
        link = self.scratch / "skills-link"
        link.symlink_to(self.skill)
        self.machine.write_text(settings(str(link / "scripts" / "resume-hook")))
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertIn(f"machine registered in {self.machine}", out)

    def test_check_refuses_both_tiers_an_unreadable_file_and_a_page_over_the_cap(self):
        self.page.write_text("# Orchestrate\n")
        self.account_file().write_text(settings(str(self.hook)))
        self.machine.write_text(settings(str(self.hook)))
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("not ready: registered in both, so the page arrives twice", out)
        self.machine.write_text("{not json")
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertIn(f"machine unreadable in {self.machine}", out)
        self.page.write_text("y" * (CAP + 1))
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("page 9501 of 9500 characters; not ready: the page is over the cap", out)

    def test_control_check_does_not_read_another_program_as_this_one(self):
        self.page.write_text("# Orchestrate\n")
        other = self.scratch / "claw-resume-hook"
        other.write_text("#!/bin/sh\n")
        self.account_file().write_text(settings(str(other)))
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("account not registered", out)


if __name__ == "__main__":
    unittest.main()
