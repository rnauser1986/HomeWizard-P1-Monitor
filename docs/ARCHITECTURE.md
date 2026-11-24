# HomeWizard P1 – architecture and operations

This file tells how HomeWizard P1 is built and what it does. It replaces the earlier Word documents
(`HomeWizard_P1_PQ_Monitoring_Documentation_EN` and `_SWE`). `AGENTS.md` lists the rules for a change. This file gives the
detail behind each rule. Write changes to this file by the rules in `.agents/writing.md`.

## Contents

- [Architecture](#architecture)
- [Data model (SQLite)](#data-model-sqlite)
- [Collector](#collector)
- [Alarms](#alarms)
- [Price and tariff](#price-and-tariff)
- [Web server and routes](#web-server-and-routes)
- [Power quality](#power-quality)
- [Pages](#pages)
- [Design tokens](#design-tokens)
- [Configuration (.env)](#configuration-env)
- [Operations](#operations)
- [Run with Docker](#run-with-docker)
- [Known gaps](#known-gaps)

## Architecture

The program uses the Python 3 standard library only (`sqlite3`, `urllib.request`, `http.server`, `json`). It has two
processes.

| Process | File | Job |
| --- | --- | --- |
| Collector | `collector.py` | Reads the meter, writes to SQLite, runs the alarms. |
| Web server | `api_server.py` | Reads SQLite, serves the pages and the JSON routes. |

The two processes share one SQLite file in WAL mode. The collector writes and the web server reads, so they do not block each
other.

**Data flow**

1. `config.py` reads `.env` and the environment. Every other module imports its settings from `config.py`.
2. `collector.py` sends an HTTP GET to the meter (Local API v1, JSON) every `HW_POLL_INTERVAL_SEC` seconds.
3. The collector writes the reading to `measurements`, then calls the alarm modules.
4. The alarm modules send messages through `notify_signal.py` (Signal, optional) and write tagged lines to standard output.
5. `api_server.py` answers page and JSON requests from the database. `tariff_mod.py` gives the price per kWh.
6. `pq_data.py` and `pq_page.py` give the power quality numbers and page.

**Rules**

- The program only reads from the meter. It never writes to it.
- The program has **no sign-in**. Reach it only on the home network, or through a VPN or an access service such as Tailscale or
  Cloudflare. Never open a port to the internet.
- The web server listens on `HW_API_HOST:HW_API_PORT` (default `0.0.0.0:8085`). Set `HW_API_HOST` to the address of one network
  interface when the host has more than one.
- Nothing that belongs to one installation is in the repository: not the meter address, not the Signal numbers, not `.env`.
- Every setting has a default in `config.py`, and no default belongs to one installation. Without `.env` the program uses the
  example meter address and sends no Signal message. The collector says so in the log (`[CONFIG]`).

## Data model (SQLite)

`collector.py` creates the database at the first start (`init_db`). The default file is `/opt/homewizard/homewizard.db`. Put it on
fast local storage. WAL does not work well on a network mount.

| Table | Columns | Purpose |
| --- | --- | --- |
| `measurements` | `id`, `ts_utc`, `wifi_strength`, `active_power_w`, `active_power_l1_w` to `l3_w`, `active_voltage_l1_v` to `l3_v`, `active_current_a`, `active_current_l1_a` to `l3_a`, `total_power_import_kwh`, `total_power_export_kwh`, `raw_json` | One row for each poll. Index `idx_measurements_ts` on `ts_utc`. |
| `historical_kwh` | `period_type` (`day`, `week`, `month`, `year`), `period_key`, `kwh` | Energy from before the live polling started. The web server adds it to the live totals. |
| `tariff_config` | `id` (always 1), `start_date`, `mode` (`fixed`, `dynamic`), `interval_min` (15, 60), `area`, `fixed_sek`, `add_sek`, `vat_pct` | The price settings. An empty table means the defaults from `.env`. |
| `spot_prices` | `slot_key` (`YYYY-MM-DD HH:MM`), `sek_per_kwh` | A cache of the spot prices from `elprisetjustnu.se`. |

**Rules**

- **The program never changes or deletes a measurement.** Each poll adds one row. `raw_json` holds the full answer of the meter.
- Every column of `measurements` except `ts_utc` and `raw_json` can be NULL. The meter leaves out fields it does not have.
- `ts_utc` is a UTC time in ISO 8601 text, for example `2026-11-24T06:11:14.123456+00:00`. The pages show local time. SQLite
  converts with `'localtime'`, so the host must have the right time zone.
- A row imported from CSV has `"source":"csv_import"` in `raw_json`. The kWh totals skip these rows. Their energy comes from
  `historical_kwh`.
- **Energy for a period** is `MAX(total_power_import_kwh) - MIN(total_power_import_kwh)` over the live rows of the period, plus
  the matching `historical_kwh` values. The program does not count exported energy.
- **Standby load** is the 15th percentile of the positive power values of the period. The standby kWh is that power times the
  hours. For day and now, one point is one minute. For week, month and year, one row is one poll, so the hours are the number of
  rows times `HW_POLL_INTERVAL_SEC` divided by 3600.

To switch to spot prices, write row 1 of `tariff_config`:

```sh
sqlite3 /opt/homewizard/homewizard.db \
  "INSERT OR REPLACE INTO tariff_config (id, start_date, mode, interval_min, area, fixed_sek, add_sek, vat_pct) \
   VALUES (1, '2026-11-01', 'dynamic', 60, 'SE3', 1.50, 0.08, 25.0);"
```

## Collector

`collector.py` runs one loop under `homewizard-collector.service`.

1. Read the meter URL. The timeout is `HW_METER_TIMEOUT_SEC` (6 s).
2. Add the reading to `measurements` with a UTC time stamp and the full answer in `raw_json`.
3. Call `check_spike`, `check_imbalance`, `check_phase_amp`, `check_voltage` and `check_stale`.
4. Sleep `HW_POLL_INTERVAL_SEC` seconds.

**Rules**

- **A failed poll never stops the collector.** The loop catches every exception, prints `[POLL_ERR] ...`, runs `check_stale` and
  tries again after the sleep.
- At the start the collector prints a `[CONFIG]` line when Signal is not set up, and one when `HW_METER_URL` is still the example
  address. A silent alarm is worse than a loud one.
- An exception in an alarm module ends that loop pass in the same way. Keep alarm modules free of exceptions.
- `check_stale` reads the database. It is the only alarm that works when the meter is silent.

## Alarms

Each alarm module keeps its state in memory: a counter of samples in a row (`NEED`), the time of the last alert and the
extreme value of the current series. A restart clears the state. Samples in a row filter out a short start surge, for example
a heat pump compressor.

| Module and function | Trigger | Output | Cooldown |
| --- | --- | --- | --- |
| `alarm_imbalance.py` `check_imbalance(l1, l2, l3)` | `max - min` of the three phase currents is at least `HW_ALARM_IMBALANCE_DIFF_A` (12 A) for 6 samples in a row | Signal and log `[IMBALANCE]` | 1200 s |
| `alarm_phase_amp.py` `check_phase_amp(l1, l2, l3)` | One phase current is at least `HW_ALARM_PHASE_LIMIT_A` (20 A) for 3 samples in a row | Signal and log `[PHASE_AMP]` | 1800 s for each phase |
| `alarm_voltage.py` `check_voltage(v1, v2, v3)` | A phase voltage is below `HW_ALARM_VOLTAGE_LOW_V` (215 V) or above `HW_ALARM_VOLTAGE_HIGH_V` (245 V) for 3 samples in a row | Signal and log `[VOLTAGE_LOW]` or `[VOLTAGE_HIGH]` | 1800 s for each phase and direction |
| `alarm_spike.py` `check_spike(l1, l2, l3)` | The highest phase current is at least `HW_ALARM_SPIKE_LIMIT_A` (22 A) for 3 samples in a row | Signal and log `[SPIKE]` | 1200 s |
| `alarm_stale.py` `check_stale(db_path)` | The newest `ts_utc` is older than `HW_ALARM_STALE_SEC` (300 s) | Signal and log `[STALE]`. When data returns: Signal and log `[STALE_OK]` | 1200 s |

`notify_signal.py` `send_signal(msg)` always prints `[SIGNAL_MSG] ...`. It sends an HTTP POST (JSON) to
`<HW_SIGNAL_URL>/v2/send` only when `HW_SIGNAL_URL`, `HW_SIGNAL_FROM` and `HW_SIGNAL_TO` are all set. The timeout is
`HW_SIGNAL_TIMEOUT` (4 s). It does not send the same text twice inside `HW_SIGNAL_COOLDOWN` (30 s), and it forgets older texts so
its memory stays small. It catches every exception and prints `[SIGNAL_ERR] ...`.

**Rules**

- A threshold hit counts when the value is at or beyond the limit. The voltage alarm uses `<` and `>`, so a value exactly on the
  limit is normal.
- A missing value (`None`) never counts as a hit. In `check_phase_amp` and `check_voltage` it sets the counter back to zero.
  `check_imbalance` and `check_spike` skip the sample when a value is missing.
- A normal sample sets the counter back to zero.
- A current of 22 A or more can send a phase alert (20 A) and a spike alert (22 A) for the same event.
- The stale alarm reads the database, so it works when the meter is silent. An error inside it is logged as `[STALE_ERR]`.
- An alert resets the extreme value. The next alert shows the extreme of its own series.
- The message holds the limit, the value now, the extreme value and all three phases.
- **Never log the Signal numbers or the Signal URL.**

## Price and tariff

`tariff_mod.py` gives the price in SEK per kWh with `effective_rate(cur, db_path)`.

- Before `HW_TARIFF_START_DATE`, or when `mode` is `fixed`, the price is `HW_DEFAULT_FIXED_SEK`.
- With `mode` set to `dynamic`, the module reads the price of the current slot (60 or 15 minutes) from `spot_prices`. It takes the
  newest price at or before the slot. A price older than `HW_TARIFF_MAX_SLOT_AGE_HOURS` (36 h) does not count.
- If it finds no price, it fetches the prices of today and tomorrow from `https://www.elprisetjustnu.se/api/v1/prices/` for the
  zone in `area`, and tries again. It fetches at most once in `HW_TARIFF_SYNC_COOLDOWN` seconds (300 s).
- If there is still no spot price, the price falls back to the fixed price.
- The final price is `(base + add_sek) * (1 + vat_pct / 100)`.
- The cost on the pages is the energy of the period times the **current** price. It is not a sum of the real price of each hour.

## Web server and routes

`api_server.py` starts a `ThreadingHTTPServer`. It answers GET requests only.

| Route | Answer |
| --- | --- |
| `/` | `ui.html`, the statistics page |
| `/stats` | JSON: power now (total, L1, L2, L3), and kWh and SEK for day, week, month and year |
| `/history?period=<now\|day\|week\|month\|year>&offset=<0\|-1\|...>` | JSON: the chart points, the phase voltage and current, and the standby load |
| `/power-quality` | The power quality page (`PQ_HTML`) |
| `/api/power-quality?hours=<1..HW_PQ_MAX_HOURS>` | JSON from `pq_data.py` |
| `/health` | JSON with `ok`, `status`, `latest_utc`, `age_sec`, `max_age_sec` |
| any other path | 404 with an empty body |

`/health` answers 200 when the newest measurement is not older than `HW_HEALTH_MAX_AGE_SEC` (120 s). It answers 503 with
`stale_data`, `no_data` or `db_error`. Use it with Uptime Kuma or a similar monitor.

**Rules**

- Every route is open. Reach the server only through the home network or an access service (see Architecture).
- Bound every input from a request. `hours` is 1 to `HW_PQ_MAX_HOURS` (168 by default). `offset` is a whole number and the server
  limits it to a safe range.
  A bad value gives the default, not an error.
- An unexpected error in a route gives HTTP 500 with the body `{"error":"internal error"}` and the log line `[HTTP_ERR] <path>: ...`.
  The body never holds details.
- Use `?` placeholders for every value from a request.

## Power quality

`pq_data.py` `get_power_quality(db_path, hours)` analyses the last 1 to 720 hours. The default is 24. It keeps each answer in
memory for `HW_PQ_CACHE_TTL_SEC` seconds for each combination of file and hours. The web server asks for at most `HW_PQ_MAX_HOURS`
hours (168 by default). See Known gaps.

The bucket size grows with the window:

| Window | Bucket |
| --- | --- |
| up to 24 h | 1 minute |
| up to 72 h | 5 minutes |
| up to 7 days | 15 minutes |
| up to 30 days | 60 minutes |

The answer holds:

- `summary`: the minimum and maximum voltage and the maximum current for each phase, the peak power and the largest voltage
  spread **ΔV** (`max(V) - min(V)`), with the time of each peak.
- `aggregates`: the number of samples, the counts of voltages below 215 V and above 245 V, the counts of currents at or above 20 A,
  and the counts above `HW_PQ_POWER_PEAK_W` and `HW_PQ_DV_WARN_V`.
- `series`: one point for each bucket (average voltage, maximum current, maximum power, maximum ΔV).
- `events`: up to 1000 anomaly rows, newest first.

The cut-off of the window is written as `YYYY-MM-DDTHH:MM:SS` text, the same format as `ts_utc`.
The limits 215 V, 245 V and 20 A in `pq_data.py` are fixed in the code. They do not follow the `HW_ALARM_*` settings.

## Pages

The pages are plain HTML, CSS and SVG with no library. They are dark only.

- **Statistics (`/`, `ui.html`).** Cards for NOW, DAY, WEEK, MONTH, YEAR, MONTH SEK and power quality. The page refreshes
  `/stats` and `/history` every 10 seconds. The chart shows the selected period with a hover tooltip. The `<` and `>` buttons move
  one day, week or year back. Below the chart are the voltage and the current of each phase.
- **Power quality (`/power-quality`, `pq_page.py`).** Window buttons (6 h, 24 h, 72 h, 1 week, 1 month). Three phase cards, four
  summary cards, five charts (voltage, current, current imbalance, minute maximum power, voltage spread ΔV) and the table
  "Anomalies & peaks".

## Design tokens

Every colour comes from a named token. A new or changed page defines the token once as a CSS variable and uses `var(--name)`. A
page never holds a hex value of its own. The values below are the palette of the pages today.

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#0b0f17` | Page background |
| `--surface` | `#121826` | Cards and buttons |
| `--surface-deep` | `#090c12` | Panels |
| `--chart-bg` | `#06080d` | Chart background |
| `--border` | `#263042` | Card and button border |
| `--border-panel` | `#1e2636` | Panel border |
| `--text` | `#e2e8f0` | Main text |
| `--text-muted` | `#94a3b8` | Labels and sub text |
| `--accent` | `#a855f7` | Selected card and button border |
| `--accent-bg` | `#1a132b` | Selected card background |
| `--accent-text` | `#e9d5ff` | Selected button text |
| `--warn` | `#f59e0b` | A value at or beyond a warning limit |
| `--ok` | `#34d399` | A value with no warning |
| `--info` | `#38bdf8` | A neutral highlight |
| `--danger` | `#fb7185` | The imbalance value in a tooltip |

## Configuration (.env)

`config.py` reads `.env` in the folder of the scripts. A variable in the systemd unit overrides `.env`. `.env` overrides the
default in `config.py`. Copy `.env.example` to `.env` and give it the mode 600.
**Set `HW_METER_URL`, and the three `HW_SIGNAL_*` values if you want Signal messages.** Without the Signal values the alarms go to
the log only.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HW_BASE_DIR` | folder of the scripts | Root folder of the program |
| `HW_DB_PATH` | `<BASE_DIR>/homewizard.db` | The SQLite file |
| `HW_UI_PATH` | `<BASE_DIR>/ui.html` | The statistics page |
| `HW_METER_URL` | `http://192.168.1.50/api/v1/data` | **Change this.** The Local API v1 address of the meter. Give the meter a fixed IP address |
| `HW_POLL_INTERVAL_SEC` | 10 | Seconds between two reads of the meter |
| `HW_METER_TIMEOUT_SEC` | 6 | Seconds the collector waits for the meter |
| `HW_API_HOST` | `0.0.0.0` | Address the web server listens on |
| `HW_API_PORT` | 8085 | Port of the web server |
| `HW_HEALTH_MAX_AGE_SEC` | 120 | The age at which `/health` answers 503 |
| `HW_SIGNAL_URL` | empty | Base URL of signal-cli-rest-api. Empty means no Signal messages |
| `HW_SIGNAL_FROM` | empty | The sender number, in international format |
| `HW_SIGNAL_TO` | empty | The recipient number or group |
| `HW_SIGNAL_TIMEOUT` | 4.0 | Seconds to wait for the Signal server |
| `HW_SIGNAL_COOLDOWN` | 30.0 | The same message text is not sent twice inside this many seconds |
| `HW_ALARM_IMBALANCE_DIFF_A` | 12.0 | Largest allowed difference between two phase currents (A) |
| `HW_ALARM_IMBALANCE_NEED` | 6 | Samples in a row (6 × 10 s = 60 s) |
| `HW_ALARM_IMBALANCE_COOLDOWN` | 1200 | Seconds between two imbalance alerts |
| `HW_ALARM_PHASE_LIMIT_A` | 20.0 | Phase current limit (A). Set it to the main fuse |
| `HW_ALARM_PHASE_NEED` | 3 | Samples in a row (30 s) |
| `HW_ALARM_PHASE_COOLDOWN` | 1800 | Seconds between two alerts for one phase |
| `HW_ALARM_SPIKE_LIMIT_A` | 22.0 | Spike limit (A) |
| `HW_ALARM_SPIKE_NEED` | 3 | Samples in a row |
| `HW_ALARM_SPIKE_COOLDOWN` | 1200 | Seconds between two spike alerts |
| `HW_ALARM_STALE_SEC` | 300 | Age of the newest measurement for a stale warning |
| `HW_ALARM_STALE_COOLDOWN` | 1200 | Seconds between two stale warnings |
| `HW_ALARM_VOLTAGE_LOW_V` | 215.0 | Low voltage limit (V) |
| `HW_ALARM_VOLTAGE_HIGH_V` | 245.0 | High voltage limit (V) |
| `HW_ALARM_VOLTAGE_NEED` | 3 | Samples in a row (30 s) |
| `HW_ALARM_VOLTAGE_COOLDOWN` | 1800 | Seconds between two alerts for one phase and direction |
| `HW_PQ_POWER_PEAK_W` | 9500.0 | Power limit (W) marked on the power quality page |
| `HW_PQ_DV_WARN_V` | 8.0 | Voltage spread limit ΔV (V) |
| `HW_PQ_CACHE_TTL_SEC` | 20.0 | Seconds the power quality answer stays in memory |
| `HW_PQ_MAX_HOURS` | 168 | The longest window the server allows, in hours (1 to 720) |
| `HW_PRICE_AREA` | `SE3` | Bidding zone `SE1` to `SE4` |
| `HW_TARIFF_START_DATE` | `2026-11-01` | Before this date the fixed price always applies |
| `HW_DEFAULT_FIXED_SEK` | 1.5 | Fixed price in SEK per kWh, and the fallback price |
| `HW_TARIFF_SYNC_COOLDOWN` | 300 | Fewest seconds between two fetches of the spot prices |
| `HW_TARIFF_MAX_SLOT_AGE_HOURS` | 36 | A spot price older than this is not used |

## Operations

**Files on the host**

| Path | Content |
| --- | --- |
| `/opt/homewizard/` | The program, `.env` and `homewizard.db` |
| `/usr/local/bin/backup-homewizard-db.sh` | The backup script |
| `/var/backups/homewizard/` | The backups (7 days) |
| `/etc/systemd/system/` | The two services, the backup service and the backup timer |

**Install on a new Debian host**

1. Unpack the repository on the host.
2. Run `sh install/install.sh` as root. The script installs the packages, copies the files, makes `.env` from `.env.example`
   and installs the units and the backup timer.
3. Edit `/opt/homewizard/.env`. Set `HW_METER_URL`. Set the Signal values if you want Signal messages.
4. Check the meter: `curl -fsS "http://<METER_IP>/api/v1/data" | head -c 300`.
5. Start the services: `systemctl enable homewizard-collector.service homewizard-api.service`, then
   `systemctl restart homewizard-collector.service homewizard-api.service`.
6. Open `http://<host>:8085/` and `http://<host>:8085/health`.

The script enables and restarts the two services itself when `HW_METER_URL` is already changed. It never replaces an existing
`.env`. Run it again for an update: it copies the new files and restarts the services. A restart sets the alarm counters back to
zero.

**Move an older install to this layout**

The older install keeps the meter address and the Signal values inside `collector.py` and `notify_signal.py`. `install.sh` stops
on such an install, so that the values are not lost.

1. Unpack the repository on the host.
2. Run `sh install/migrate-from-old-layout.sh`. It archives the old program files (without the database) in
   `/var/backups/homewizard`. It reads the values from the old files and writes `.env` with the mode 600. It lists the variables
   that the systemd units set. It never prints a Signal number or address.
3. Open `/opt/homewizard/.env` and check it. Copy the variables of the systemd units into it.
4. Run `sh install/install.sh`.
5. Read the log: `journalctl -u homewizard-collector.service -n 30 --no-pager`. There must be no `[CONFIG]` line.
6. To go back, run the command on the last line of the output of step 2.

What is different from the older install:

- The alarm messages and the pages are in English. The Signal messages change too.
- The settings come from `.env`. A setting that is missing is the default in `config.py`, not the value of the older install.
- The standby kWh of the now and day views counts one minute for each point. The older code used the poll interval for these
  points, which are minute averages.
- The collector writes `[POLL_ERR]` instead of `Fel vid pollning`, and prints `[CONFIG]` lines at the start.
- The same Signal text is forgotten after `HW_SIGNAL_COOLDOWN` seconds, so the memory of `notify_signal.py` does not grow.

**Backup.** `homewizard-db-backup.timer` runs every day at 05:30 (`Persistent=true`, so a missed run starts after a reboot).
`backup-homewizard-db.sh` does these steps in this order:

1. Copy the database with the SQLite `.backup` command. This is safe while the collector writes.
2. Run `PRAGMA integrity_check;` on the copy. If the result is not `ok`, stop with an error. No old backup is deleted.
3. Archive the program folder, with `.env` and without the database, `*.bak*` and `__pycache__`.
4. Delete backups older than 7 days.

The files have the mode 600. The archive holds `.env`, which holds the Signal numbers. Keep `/var/backups/homewizard` readable by
root only. Run a backup by hand: `systemctl start homewizard-db-backup.service`.

When you edit a script on the host, keep one `.bak` copy for each file at most.

**Troubleshooting**

| Symptom | Check | Healthy result |
| --- | --- | --- |
| No new measurements | `systemctl status homewizard-collector.service` and `journalctl -u homewizard-collector.service -n 50 --no-pager` | The service is active. No `[POLL_ERR]` and no `[STALE]` lines |
| Page does not open | `systemctl status homewizard-api.service` and `curl -i http://127.0.0.1:8085/health` | The service is active. `/health` answers 200 with `"status": "ok"` |
| No Signal messages | `journalctl -u homewizard-collector.service \| grep -E 'SIGNAL\|CONFIG'` | No `[CONFIG] Signal is not set up` line. `[SIGNAL_MSG]` lines appear when a limit is crossed. No `[SIGNAL_ERR]` |
| Two collectors | `ps aux \| grep collector.py` | One process, from the service |
| Disk is full | `df -h / /var/backups/homewizard` and `ls -lh /opt/homewizard/homewizard.db*` | Free space. Old backups are deleted |

## Run with Docker

The repository has a `Dockerfile` and a `docker-compose.yml`. They run the same two programs without an install on the host.

| Part | What it does |
| --- | --- |
| `Dockerfile` | One image (`python:3.12-slim`) for both programs. It holds only the program files and `tzdata`. The programs run as the user `hw` (uid 10001), not as root. |
| `collector` service | Runs `collector.py`. |
| `api` service | Runs `api_server.py` and publishes the web port. It has a health check on `/health`. |
| `data` volume | Mounted at `/data` in both services. It holds `homewizard.db`. It stays when the containers are removed. |

**Start**

1. Install Docker with the Compose plugin.
2. Copy `.env.example` to `.env` and set `HW_METER_URL`. This is the only required setting. Set the three `HW_SIGNAL_*` values for
   Signal messages. Every other setting in this file works the same way.
3. Run `docker compose up -d --build`.
4. Open `http://<host>:8085/`. Read the log with `docker compose logs collector`. There must be no `[CONFIG]` line.

Compose stops with an error message when `HW_METER_URL` is missing. It does not start with the example address.

**What the compose file sets**

- `HW_DB_PATH` (`/data/homewizard.db`), `HW_API_HOST` (`0.0.0.0`) and `HW_API_PORT` (8085) are fixed inside the container. A value
  for them in `.env` has no effect.
- `TZ` is `Europe/Stockholm` unless you set `TZ` in `.env` or in the shell. The statistics use this time zone.
- `HW_WEB_PORT` (default 8085) is a Compose variable, not a program setting. It is the port on the host. Set it in `.env` to use
  another port.
- Both services use the same image.

**Operations**

| Task | Command |
| --- | --- |
| Status | `docker compose ps` |
| Log | `docker compose logs -f collector` |
| Update | Get the new files, then `docker compose up -d --build` |
| Stop | `docker compose down`. The data stays. `docker compose down -v` also deletes the database. |
| Backup | `docker compose exec collector python -c "import sqlite3; s=sqlite3.connect('/data/homewizard.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); print(d.execute('PRAGMA integrity_check').fetchone()[0])"`, then `docker compose cp collector:/data/backup.db .` |

An update restarts the services, so the alarm counters start from zero. The GitHub workflow checks the compose file and builds the
image, but it does not run the containers.

**Move the data of a host install into Docker**

1. Stop the host services: `systemctl stop homewizard-collector.service homewizard-api.service`.
2. Run `docker compose up -d --no-start`, which creates the volume.
3. Copy the database into it:
   `docker run --rm -v homewizard-p1_data:/data -v /opt/homewizard:/old:ro homewizard-p1:local sh -c "cp /old/homewizard.db /data/ && chown hw:hw /data/homewizard.db"`
   The volume name starts with the name of the folder of the repository. `docker volume ls` shows it. The `chown` runs as root,
   so add `--user root` to the command if the user `hw` cannot do it.
4. Run `docker compose up -d`. Do not run the host services and the containers at the same time.

## Known gaps

These gaps are open. A gap is not a rule: fix it in its own pull request, with its test, and delete the entry in the same pull
request. Where a test can show the gap, it is an `expectedFailure` test until the fix.

1. **The 215 V, 245 V and 20 A limits in `pq_data.py` are fixed in the code.** They differ from the settings when someone changes
   `HW_ALARM_*`.
2. **`pq_route.py` needs Flask** and nothing uses it. It breaks the rule "standard library only". Remove it or move it out.
3. **The pages hold hex colours** and no CSS variables. The tokens above are not in the code yet.
4. **The alarm modules call `time.time()` directly.** The tests replace it. A clock parameter is the target.
5. **The time zone `Europe/Stockholm` and the locale `sv-SE` are fixed in `pq_page.py`.** The statistics page writes the numbers of its
   tooltips with a decimal comma. SQLite `'localtime'` uses the time zone of the host.
6. **A page request can wait for the price service.** In `dynamic` mode, `effective_rate` calls `elprisetjustnu.se` (two calls with a
   timeout of 6 s) when a spot price is missing, at most once in `HW_TARIFF_SYNC_COOLDOWN` seconds. A failed call leaves no log line.
7. **The server has no sign-in** and listens on all interfaces by default. See Architecture.
8. **The "1 month" button shows one week.** The button asks for 720 hours. The server allows `HW_PQ_MAX_HOURS`, 168 by default, which
   is the limit of the older install. Set `HW_PQ_MAX_HOURS=720` to see a month, or take the button out. The user decides.
9. **Two queries build SQL text with f-strings:** `d_sel` in the day view and `d0` in the week view of `history()`. The values come
   from SQLite, not from the request, so there is no injection today. They break the placeholder rule.
10. **Some code closes a SQLite connection only when nothing fails.** `stats()`, `history()` and `/health` in `api_server.py`, and
    `get_power_quality()` in `pq_data.py`, call `close()` at the end, not in `finally`. Read from the code, not run.
