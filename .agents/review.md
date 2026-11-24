# Independent review

This is the checklist for the independent review of a pull request on HomeWizard P1: a Python 3 program (standard
library only, SQLite) that reads a HomeWizard P1 meter, stores the readings, sends alarms through Signal and shows
statistics and power quality in a web page. The people who use it are not technical. The alarms protect a home: a
wrong or missing alarm can hide an overloaded phase or a bad grid voltage. The reviewer did not write the code it
reviews and doesn't know the author's reasoning: it judges the diff on its own. Any agent can do the review by this
file. In Claude Code it is the `code-reviewer` agent in `.claude/agents/`.

## What to read first

- `AGENTS.md`: how to work in this repository, the safety rules and the conventions. Review against them.
- `docs/ARCHITECTURE.md`: the architecture and the domain rules. Read the sections the change touches, and review
  against them. Read "Known gaps" so you do not report an open gap as new.
- `.agents/writing.md`, for how to write the report.
- The whole diff you're given (`git diff origin/<base>...origin/<branch>`, after fetching both with
  `git fetch origin <name>:refs/remotes/origin/<name>`), then whatever surrounding code you need to understand each
  change. Read the code before you state how something works.

## What you look for, in this order

- **Bugs:** wrong results, crashes and unhandled exceptions (the HTTP handler has no catch: one bad request ends its
  thread and the client gets no answer), off-by-one, null handling (the meter leaves out fields, and every column
  except `ts_utc` and `raw_json` can be NULL), races and thread safety (`ThreadingHTTPServer` runs each request in
  its own thread, and `_CACHE` in `pq_data.py` is shared), leaked resources (SQLite connections not closed on an
  exception), and edge cases: empty, huge, negative and malformed input, from a person or from the meter.
- **Safety:** the domain rules in `docs/ARCHITECTURE.md` (listed short in `AGENTS.md`). The program only reads from
  the meter. It never changes or deletes a measurement. A failed poll or a failed Signal message never stops the
  collector. An alarm needs NEED samples in a row and obeys its cooldown. A missing value never counts as a hit. A
  change that can silence an alarm, repeat it without a cooldown, or send it for a normal value is a blocker.
- **Security and privacy:** the program has no sign-in, so a new route must not give out more than the existing ones.
  Nothing opens a port to the internet. Signal phone numbers, the Signal URL and `.env` are never logged, shown, sent
  to a page or put in the repository. All SQL uses `?` placeholders for values that come from a request. Data from the
  meter and from the price service is untrusted.
- **Time:** times stored in UTC (`ts_utc`) and shown in local time, text compares of timestamps (ISO text with a `T`
  does not compare correctly with `datetime('now')`), both daylight saving changes in day, week and month groups, and
  the clock passed in as a parameter instead of `time.time()` in new code.
- **Simplicity and the UI:** plain English words, units on every number, every colour from a design token, the phone,
  tablet and desktop layouts, real `<button>` elements.
- **Conventions:** `AGENTS.md`'s conventions and structure: standard library only, one flat folder, one test folder,
  everything in English.
- **Tests:** risky paths without a test, tests that can't fail, timing-dependent tests that will be flaky on a busy
  machine, tests that call the real meter, the real price service or a real Signal server, and tests that depend on
  the host (its time zone, locale, clock or files).
- **Platform independence:** code and tests must behave the same on Windows, Linux and macOS. The program runs on a
  Debian VM under systemd. Flag path separators, case sensitivity, line endings, file locking, time zone ids and
  culture.
- **Stale text:** comments, `AGENTS.md`, `docs/ARCHITECTURE.md`, `README.md`, `.env.example` and the files in
  `install/` that the change made wrong.

## How you work

- Verify, don't guess. Follow each code path. Where it's cheap, prove a finding with a small reproduction (a temporary
  test) in a throwaway copy of your own, and remove it when you're done. Mark anything you couldn't verify.
- Never edit, commit or push the code under review, and never comment on GitHub. You report; the author fixes.
- For each finding give: severity (blocker, bug, safety, security, convention, test, nit), `file:line`, what goes
  wrong with a concrete input or scenario, and the fix in one or two lines. Most severe first.
- Don't report style preferences as bugs, and don't pad the list: no findings is a valid result.
- Run only the tests you need to confirm a finding, never the full suite.
- Never put phone numbers, URLs of the Signal server or the contents of `.env` in your report.
- End with a one-line verdict: ready, or what must be fixed first.
