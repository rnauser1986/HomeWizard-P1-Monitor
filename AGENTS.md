# HomeWizard P1 – the home power monitor

HomeWizard P1 is a small program that reads a **HomeWizard P1 meter** (Local API v1) every 10 seconds, stores each reading in SQLite, sends alarms through Signal and shows statistics and power quality in a web page. It runs on one Debian VM, as two systemd services. It started as the monitor for one home, run by a household that wants an early warning when a phase is overloaded or the grid voltage is bad.

The users are not technical. **Simplicity comes before features**: one page of numbers, plain words, no settings screen. Every setting is a line in `.env`.

**How the program is built and what it does** is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the architecture, the data model, the alarms, the routes, the configuration and the operations. Read the sections a change touches before you change it. The running pages are the reference for layout, texts and colours.

## Safety rules every change must keep

The program does not switch anything. The risk is a wrong or a missing alarm, or a lost measurement. Each rule is told in full in its section of `docs/ARCHITECTURE.md`.

- **The meter is the source of truth, and the program only reads from it.** The collector sends HTTP GET to the meter and nothing else. It never writes to the meter. ([Architecture](docs/ARCHITECTURE.md#architecture))
- **A measurement is never changed or deleted by the program.** Each poll adds one row, with the full answer in `raw_json`. Rows imported from CSV are tagged `csv_import` and stay out of the live kWh totals. ([Data model](docs/ARCHITECTURE.md#data-model-sqlite))
- **A failed poll never stops the collector.** It logs the error, runs the stale check and polls again. A failed Signal message never stops it either. ([Collector](docs/ARCHITECTURE.md#collector))
- **An alarm needs NEED samples in a row and obeys its cooldown.** A missing value never counts as a hit. One normal sample sets the counter back to zero. A change must not silence an alarm, repeat it without a cooldown or send it for a normal value. ([Alarms](docs/ARCHITECTURE.md#alarms))
- **Secrets stay secret.** The Signal phone numbers and the Signal URL are never logged, shown in a page or put in the repository. `.env` is never in the repository. ([Configuration](docs/ARCHITECTURE.md#configuration-env))
- **No default belongs to one installation.** The defaults in `config.py` hold no address and no number. A missing setting turns Signal off, and the collector says so in the log (`[CONFIG]`). ([Architecture](docs/ARCHITECTURE.md#architecture))
- **Never on the internet directly.** The program has no sign-in. Reach it only on the home network, or through a VPN or an access service such as Tailscale or Cloudflare. Never open a port to the internet. ([Architecture](docs/ARCHITECTURE.md#architecture))
- **Time is stored in UTC and shown in local time.** Never compare timestamp text from `ts_utc` with `datetime('now')` without the same format on both sides. ([Data model](docs/ARCHITECTURE.md#data-model-sqlite))
- **Every colour comes from a design token,** never a hex value of its own. ([Design tokens](docs/ARCHITECTURE.md#design-tokens))

---

## Languages

- **Everything is in English:** the pages, code, comments, log messages, alarm messages, tests, commit messages, pull requests, issues, `README.md` and this file. Translations of the pages may come later.
- Swedish names that are part of the domain stay as they are: the bidding zones `SE1` to `SE4`, the currency `SEK`, the price service `elprisetjustnu.se`.

---

## Stack and structure

- Python 3 and **the standard library only**: `sqlite3`, `urllib.request`, `http.server`, `json`. No package to install. No web framework.
- SQLite in WAL mode, one file. Two processes: `collector.py` writes, `api_server.py` reads. Run them as the systemd services in `install/`.
- No JavaScript framework and no CSS framework. The pages are plain HTML, CSS and SVG, with no library.
- Target: one Debian VM (1 vCPU, 1 GB RAM, 20 GB disk) with the program in `/opt/homewizard/`. A daily backup runs from a systemd timer.

No `src/` folder: every module sits directly in the repository root. All tests live in **one** folder, `tests/`, with one file per area. Don't create more test folders.

```
config.py                   # Reads .env and the environment. Every setting is a typed constant here.
collector.py                # Creates the database, polls the meter every HW_POLL_INTERVAL_SEC, runs the alarms
alarm_imbalance.py          # Alarm: phase currents too far apart
alarm_phase_amp.py          # Alarm: one phase current too high
alarm_spike.py              # Alarm: short current spike
alarm_stale.py              # Alarm: no new measurement in the database
alarm_voltage.py            # Alarm: phase voltage too low or too high
notify_signal.py            # send_signal(): log the message, send it through signal-cli-rest-api if set up
tariff_mod.py               # Price per kWh: fixed, or spot price from elprisetjustnu.se
pq_data.py                  # Power quality numbers, buckets and anomaly list, with a short cache
pq_page.py                  # The power quality page (PQ_HTML)
pq_route.py                 # Optional Flask blueprint. Not used by the program. Needs Flask.
api_server.py               # The web and API server: pages, /stats, /history, /power-quality, /health
ui.html                     # The statistics page
.env.example                # Template for .env. Every setting, with its default
tests/                      # The only test folder. unittest. One file per area (alarms, notify, collector, config, tariff, pq_data, api_server, install, repo_rules)
install/
  install.sh                # The install or update on a Debian host: files, units, timer, restart
  migrate-from-old-layout.sh # Move an older install (settings inside the .py files) to .env. Run it before install.sh
  homewizard-collector.service
  homewizard-api.service
  homewizard-db-backup.service
  homewizard-db-backup.timer
  backup-homewizard-db.sh   # Copy and check the database, archive the source, delete old backups
docs/ARCHITECTURE.md        # How the program is built and what it does: architecture, data, rules, pages, operations
.github/workflows/build.yml # Build & Test: compile and run the tests (pull requests, merges to main, by hand)
.github/pull_request_template.md # What every pull request description has
.agents/writing.md          # How to write pull requests, issues and comments
.agents/review.md           # The independent review's checklist
.claude/agents/code-reviewer.md # The reviewer as a Claude Code agent
```

---

## Adding or changing an alarm

- **Name the metric, the threshold, NEED and COOLDOWN.** Each one is a `HW_ALARM_<NAME>_*` setting in `config.py`, `.env.example` and the configuration table in `docs/ARCHITECTURE.md`.
- **One module per alarm,** `alarm_<name>.py`, with one `check_<name>()` function. The collector calls it after each stored measurement.
- **A missing value never counts as a hit.** Check each value for `None` before you compare it.
- **The state stays in memory.** A restart clears it. Do not write alarm state to the database.
- **Send only through `send_signal()`.** The function logs first and then sends. It must never raise. Keep the message short and put the threshold, the value now, the extreme value and all three phases in it.
- **The clock is a parameter,** for example `now=None` with `time.time()` as the default, so a test can set the time. Never call `time.time()` for a decision that has no parameter.
- **Test it:** below the threshold, exactly at the threshold, NEED samples in a row, one normal sample in between, `None` values, and the cooldown. See Testing.
- **Everything an alarm needs:** the module, the setting in `config.py` and `.env.example`, the tests, `docs/ARCHITECTURE.md`, `README.md` and the structure in this file.
- **Prove it:** every test passes. Paste the real output in the pull request. Never write "tests pass" without running them.
- **Start from the latest `main`.** A pull request that touches the same files as another open one is stacked on it.

## Testing

- **Run the tests:** `python3 -m unittest discover -s tests -t . -v`. The standard library is enough. Do not add a test package.
- **No real meter, network or Signal server.** A test makes its own temporary database with `tests/helpers.py` and replaces `send_signal` and the clock.
- **Alarms:** below the threshold, at the threshold, NEED in a row, reset by a normal sample, `None`, cooldown, and independent phases and directions.
- **Data:** the schema, the kWh totals with and without `csv_import` rows, the `hours` limits and the bucket size.
- **The rules in this file** are tests too: `tests/test_repo_rules.py` checks English only, no installation value in the files, standard library only, and that `config.py`, `.env.example` and `docs/ARCHITECTURE.md` list the same settings. Add a setting in all three.
- **The install scripts** are tested in a temporary folder (`tests/test_install.py`). They never touch `/opt` or systemd in a test.
- **Time:** the clock is a `FakeClock`. Test day, week and month groups across both daylight saving changes. Never a real-time wait.
- **A known gap is an `expectedFailure` test** with a docstring that points to "Known gaps" in `docs/ARCHITECTURE.md`. When you fix the gap, remove the decorator and the entry in the same pull request.
- **CI:** the Build & Test workflow compiles every module and runs the tests on every pull request to `main`, on every merge into `main` and by hand. A new push to a pull request cancels its older run.
- **While working, run only the related tests:** the tests you wrote and the tests of the code you changed, never the complete suite. The complete suite runs once, as the gate, on the final commit.
- **While the Build & Test workflow is disabled:** ignore the missing GitHub checks and don't bring them up. The gate is a full local run on a clean checkout of the pull request's latest commit, with the commit and the result in the pull request's description.

## Workflow

- The user drives. Never decide the next step yourself.
- Ask the user only what you can't decide yourself from the code, these rules and the project's goals. The most important goal is simplicity for people who aren't technical (see the top of this file). Don't ask about that.
- Before writing code, explain what you plan to implement (files, functions, behaviour) and wait for the user's tweaks and approval.
- **Scope is locked at the approved plan.** Anything outside it becomes its own issue, never more work in the same pull request. Only the user widens the scope.
- On your own working branch, commit and push freely, and keep its pull request up to date. No permission is needed for that.
- Never commit or push to `main`, and never merge a pull request, without explicit permission from the user. Permission to merge is explicit only when the user tells you to merge and names the pull requests. If it is unclear whether the user will merge themselves, ask.
- Work in small, reviewable chunks: one branch and one pull request per chunk.
- Before every push, review your own diff as a hostile reviewer would and fix what you find, with tests: crashes, unhandled input (empty, huge, negative, malformed, NULL), unclosed connections, races, the safety rules above, these conventions, and stale comments or docs. Never skip this to save time or tokens; keep scope small instead.
- **Independent review:** before a pull request is ready, a reviewer that did not write the code reviews its whole diff by the checklist in `.agents/review.md` (in Claude Code, the `code-reviewer` agent in `.claude/agents/`), knowing only the diff, the surrounding code and these rules, not the author's notes. Every bug, safety and security problem in the pull request's own changes, or caused or made worse by them, is fixed before the merge, with tests, and the reviewer checks the fixes once more. At most two review rounds: one full review, then one check of only the fixes. What is still open after that goes to the user as a decision, not a third round. What the reviewer finds outside the pull request's scope becomes its own issue. A finding the author disagrees with, or a judgement call, goes to the user. The remaining nitpicks of a pull request go together into one follow-up issue, "Nitpicks from the review of PR #N".
- A pull request is **ready to merge** only when all of these hold for its latest commit:
  - the independent review covers the latest commit, and nothing it found is left open;
  - every review thread is resolved, except a thread whose finding the user explicitly decided not to act on, with that decision replied on the thread;
  - it has no `needs attention` label;
  - GitHub shows every check done and green; while the Build & Test workflow is disabled, the full local run (see Testing) on that commit passed instead, with the result in the pull request's description.
  Check all four yourself before saying a pull request is ready; never on an agent's word or an older review.
- A pull request with a finding or decision waiting on the user (a review finding the author disagrees with, or an open design question) gets the `needs attention` label. Remove the label once it's resolved.
- Help the user keep the work fast and cheap: say so as soon as work is queuing up (jobs waiting on each other, overlapping files that will need merging), and propose a better order when one uses fewer tokens or queues less.
- Keep reports to the user short: only what they must decide or know.
- Every piece of work has a GitHub issue; create one first if it's missing. When work starts, open a **draft** pull request with "Fixes Issue #N"; mark it ready for review when it's done. Always write "Issue #N" or "PR #N" (in chat, issues, pull requests and commits), never "#N" or a number alone, so issues and pull requests are never mixed up.
- Pull requests get a clear title and a **short description** that follows `.github/pull_request_template.md`. Not an essay. Write pull request text, issues and comments by the rules in `.agents/writing.md`.

### Branches
- `type/issue-short-description`: the type, the issue number, and a few words in lower case with hyphens, e.g. `feature/12-stale-alarm-signal`. Without an issue, leave the number out (`chore/pin-python-version`). No user name, no `claude/` and no random suffix.
- The types:
  - `feature/`: adding, removing or changing a feature, e.g. `feature/14-export-alarm`
  - `bugfix/`: a bug that isn't urgent, e.g. `bugfix/9-week-history-crash`
  - `hotfix/`: an urgent, critical fix that goes straight to production, e.g. `hotfix/20-collector-stops`
  - `refactor/`: cleaning up or restructuring code without changing what it does, e.g. `refactor/clock-parameter`
  - `docs/`: documentation, e.g. `docs/update-readme`
  - `test/`: adding or updating tests, e.g. `test/pq-window-bounds`
  - `chore/`: maintenance, dependencies or build settings, e.g. `chore/update-workflow`
- One branch per pull request.
- A branch starts from the latest `main`. When it changes the same files or area as another open branch, it is stacked on that branch instead, and its pull request is chained to the other's (based on its branch, not `main`), so they merge in order.

## Conventions

- Standard library only. A new package needs the user's explicit approval.
- Time: store UTC. New and changed code takes the clock as a parameter. Never use `datetime.now()` without a time zone.
- SQL: every value from a request or from the meter goes in a `?` placeholder. Never build SQL from request text.
- Input: bound every input from a request (`hours` 1 to `HW_PQ_MAX_HOURS`, `offset` a whole number) and answer a bad value with a default or a 400, never with a crash.
- Log to standard output with a tag in square brackets, for example `[STALE]` or `[SIGNAL_ERR]`. The systemd journal keeps it. Never log a phone number or the Signal URL.
- Open an SQLite connection in a `with` block or close it in `finally`.
- Everything in English (see Languages).
