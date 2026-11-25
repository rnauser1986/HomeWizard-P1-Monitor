# HomeWizard P1 Logger & Dashboard

HomeWizard P1 reads a local HomeWizard P1 meter (Local API v1) every 10 seconds. It stores each reading in SQLite. It watches
the phases and the voltage, sends alarms through Signal, and shows statistics and power quality in a web page.

It uses the Python 3 standard library only. Nothing to install with `pip`.

> **Built with AI assistance.** Google Gemini helped to create the first version of this program. Claude (Anthropic) helped to
> clean it up, test it, fix problems that a review found, package it for Docker and write the documentation. The owner decides what
> the program does, adjusts the installation and runs it on their own network. See
> [AI assistance](AGENTS.md#ai-assistance) for who did what and how the result is checked.

## What it looks like

Screenshots from a live install. They were taken from an earlier version with Swedish labels. The labels in this repository
are English. The page and card layout is the same.

![Statistics page: power over the day, voltage and current per phase](docs/images/ui-day.png)

The statistics page (`/`). The cards at the top choose the period. The chart shows the power of one day, with the voltage and
the current of each phase below it.

![Power quality page: 24 hours of voltage, current, imbalance and peak power](docs/images/pq-overview.png)

The power quality page (`/power-quality`). It shows the voltage range and the current peaks of each phase over the chosen
window. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#pages) for every card and chart.

## Before you start

You need:

1. **A smart electricity meter, and a HomeWizard P1 meter that fits it.** The HomeWizard P1 meter is a small Wi-Fi device that
   plugs into a port on your electricity meter and reads the data from there. A meter without such a port (an old meter with a
   turning disc, for example) cannot be read. Check these things before you buy:
   - **Your meter needs a P1 or HAN port.** HomeWizard says that all modern meters in Sweden have one of them and work with the
     Wi-Fi P1 meter. The port is a small socket on the meter, often under a cover. If you are not sure, ask your grid operator
     (the company that owns the meter) or look up the model of the meter on HomeWizard's compatibility page (the link below).
   - **A meter with a P1 port (RJ12 connector)** takes the P1 meter directly: it clicks into the port.
   - **A meter with a HAN port (RJ45 connector)** needs HomeWizard's **HAN converter**, an adapter between the port and the P1
     meter, and a **USB-C power supply**, because this kind of port gives no power. HomeWizard says that orders from
     homewizard.se since 9 January 2024 include a free HAN converter that works with both port types. The power supply is not in the
     box for meters with an RJ45 HAN port, and neither for Kamstrup meters in Sweden. Buy one if you need it.
   - **Some grid operators must switch on the port first.** HomeWizard writes that some meters may need the network operator
     to enable the HAN port. Ask yours if the meter gives no data.
   - **Other countries:** HomeWizard lists the Netherlands, Belgium, Luxembourg, Denmark, Hungary, Sweden, Finland, Austria, Norway,
     Lithuania and Switzerland, with different rules for each. In Denmark and Norway a HAN port needs the converter.

   The list of meter models and the rules change over time. Read HomeWizard's own compatibility page before you buy:
   [Which smart meters work with the P1 meter](https://helpdesk.homewizard.com/articles/5935311). Then plug the meter in and
   connect it to your Wi-Fi with the HomeWizard Energy app.
2. **The Local API turned on.** HomeWizard's help article says: open the HomeWizard Energy app, go to **Settings > Meters**, choose
   the meter and turn on **Local API** ([HomeWizard help article](https://helpdesk.homewizard.com/articles/5935977)). The menus
   can change between versions of the app. If you do not find it, search the help pages for "Local API". This program uses
   version 1 of the API, which needs no login and no key.
3. **The address of the meter.** Look in the list of devices in your router. Give the meter a fixed address (an address
   reservation in the router), so that it does not change. Then check it: open `http://<meter-address>/api/v1/data` in a
   browser. A page of numbers inside curly brackets (JSON) means that the meter answers.
4. **A computer that is always on, on the same network:** a Raspberry Pi, a small PC or a virtual machine. The target is one
   processor core, 1 GB of memory and a 20 GB disk. The database grows by about 3 GB a year (see "Data model" in
   [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#data-model-sqlite)), so plan the disk for that.
5. **Optional: Signal messages** for the alarms (see "Signal messages" below). Without Signal the alarms go to the log only.

## Install

There are two ways to install it, and both run the same program with the same settings. **Docker** is the shortest way: one line, and
you update by running the line again. It works on a Raspberry Pi (64-bit), Ubuntu and Debian. The **host install** runs the two
programs directly as systemd services on a Debian machine. Use it when you already have a Debian host and want no Docker.
Pick one of them.

On a Debian host, as root, from the unpacked repository:

```sh
sh install/install.sh
```

Then edit `/opt/homewizard/.env`. Set `HW_METER_URL` to the address of your meter. Set the three `HW_SIGNAL_*` values if you want
Signal messages. Start the services:

```sh
systemctl enable homewizard-collector.service homewizard-api.service
systemctl restart homewizard-collector.service homewizard-api.service
```

The script enables and restarts the services itself when `HW_METER_URL` is already changed. It never replaces an existing `.env`.
Run it again for an update.

## Run with Docker

One line sets it up on a Raspberry Pi (64-bit Raspberry Pi OS), Ubuntu or Debian machine on your own network. You do not
download the repository. The line installs Docker when it is missing (it asks for your password with `sudo`), downloads the
published image, asks for the address of the meter and starts the program:

```sh
curl -fsSL https://github.com/rnauser1986/HomeWizard-P1-Monitor/releases/latest/download/install.sh | sh
```

At the end it shows the address to open. Run the line again to update: it takes the newest image and keeps `.env` and the
database. To remove the program add `--uninstall`: `... | sh -s -- --uninstall`. The database stays in the Docker volume, and
installing again takes it back. `--uninstall --delete-data` deletes it too. Never open the web port to the internet: the page has
no sign-in.

The line works only when a release exists (the Release workflow makes it, see "Release" in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#run-with-docker)) and when the repository and the image package are public.
Read a script before you run it from the internet: [`install/get.sh`](install/get.sh) is short.

If you already have the repository, the setup script does the same and builds the image on your machine. It asks for what the
program needs (the address of the meter, the web port and, if you want, the Signal values), writes `.env` and starts the containers:

```sh
sh install/docker-setup.sh
```

Open `http://<host>:8085/`. To do it by hand, copy `.env.example` to `.env`, set `HW_METER_URL` and run
`docker compose up -d --build`. The database is in the Docker volume `data` and stays when you update. See "Run with Docker" in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#run-with-docker) for the backup, the update and how to move a host install.

## Move an older install

An older install has the meter address and the Signal values inside `collector.py` and `notify_signal.py`. Run this first, then
`install.sh`:

```sh
sh install/migrate-from-old-layout.sh
sh install/install.sh
```

The first script archives the old files, writes `/opt/homewizard/.env` from the old values and prints the command to go back.
See "Move an older install to this layout" in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#operations).

## Check that it works

- Open `http://<host>:8085/` for the statistics.
- Open `http://<host>:8085/power-quality` for the power quality.
- Open `http://<host>:8085/health`. The answer `{"ok": true, "status": "ok", ...}` means new measurements arrive.

The program has no sign-in. Use it on the home network only, or through a VPN. Never open a port to the internet. To limit
which computers on your network can open the page, see "Limit who can reach the page" in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#operations).

**If nothing shows,** open the table "Troubleshooting" in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#operations). It lists
what to check for no new measurements, a page that does not open, no Signal messages, two collectors and a full disk.

**Backups.** On a host install a timer copies the database every night at 05:30 to `/var/backups/homewizard`, as a compressed
file, and keeps 7 days. Docker has its own command. See "Backup" in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#operations) for
how to change the number of days, how to check a backup and how to restore one.


## Signal messages (optional)

The alarms always write a line to the log. To get them as a Signal message on your phone as well, the program talks to
**signal-cli-rest-api**, a separate open source program that you run yourself, next to this one. It is linked to a Signal
number and sends the messages for you. Follow its own documentation to start it and to link or register the number. This
repository does not install it.

When it runs, set three values in `.env` (the third is the number that receives the messages):

```sh
HW_SIGNAL_URL=http://<signal-host>:8080
HW_SIGNAL_FROM=<the number that sends, in international format>
HW_SIGNAL_TO=<the number that receives, in international format>
```

Then restart the collector (host install: `systemctl restart homewizard-collector.service`, Docker: `docker compose up -d`).
The log must not hold the line `[CONFIG] Signal is not set up`. The program never writes the numbers or the address to the log.

To test the messages without waiting for a real alarm, you can try the service by hand. The program sends the same request
(`POST <HW_SIGNAL_URL>/v2/send` with a JSON body):

```sh
curl -X POST -H "Content-Type: application/json" \
  -d '{"message": "test", "number": "<sender>", "recipients": ["<receiver>"]}' http://<signal-host>:8080/v2/send
```

To test the alarm itself, set `HW_ALARM_PHASE_LIMIT_A=1` in `.env`, restart the collector and wait half a minute: any load on a
phase then crosses the limit and a message is sent. Set the value back (the default is 20) and restart again. The alarms are
explained in "Alarms" in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#alarms).

## Electricity price

The pages show a cost next to the energy. The cost is the energy of the period times a price per kWh, and the price comes from one
of two modes:

- **`fixed`** (the default): one price all the time, `HW_DEFAULT_FIXED_SEK` (1.5 SEK per kWh in `.env.example`). Use it for a
  contract with a fixed price. Change the number to your own price.
- **`dynamic`**: the spot price of the current hour or quarter, fetched from `elprisetjustnu.se`. It works for the four
  **Swedish** zones (`SE1` to `SE4`) only. Use it for a contract that follows the market.

Both add a surcharge (`add_sek`) and VAT (`vat_pct`). To switch to `dynamic`, write one row in the database:

```sh
sqlite3 /opt/homewizard/homewizard.db \
  "INSERT OR REPLACE INTO tariff_config (id, start_date, mode, interval_min, area, fixed_sek, add_sek, vat_pct) \
   VALUES (1, '2026-11-01', 'dynamic', 60, 'SE3', 1.50, 0.08, 25.0);"
```

The cost on the pages is the energy of the whole period times the **current** price, so with `dynamic` it is a guide and not an
invoice. If you live outside Sweden, use `fixed`, fill the price table yourself or replace the fetch: see "Price and tariff" in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#price-and-tariff) for every setting, the Docker command and a skeleton for another
price service.

## Files

| File | Job |
| --- | --- |
| `config.py`, `.env.example` | Settings. Copy `.env.example` to `.env`. |
| `collector.py` | Creates the database, polls the meter, runs the alarms. |
| `alarm_*.py`, `notify_signal.py` | The alarms and the Signal messages. |
| `api_server.py`, `ui.html` | The web and API server and the statistics page. |
| `pq_data.py`, `pq_page.py`, `pq_route.py` | Power quality. `pq_route.py` is optional and needs Flask. |
| `tariff_mod.py` | Price per kWh, fixed or spot price (`SE1` to `SE4`). |
| `Dockerfile`, `docker-compose.yml` | The image and the two services for Docker. |
| `.github/workflows/` | The tests (`build.yml`) and the release of the image and the install line (`release.yml`). |
| `install/` | The install script, the one-line Docker install (`get.sh`) and `compose.release.yml`, the Docker setup script, the script that moves an older install, the systemd units and the backup script. |
| `tests/` | The tests. |

## Tests

```sh
python3 -m unittest discover -s tests -t . -v
```

The GitHub workflow also runs the linter (`ruff check .`) and starts the Docker package against a fake meter
(`sh tests/e2e/run.sh`). You can run the second check yourself if you have Docker.

## More

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): how the program is built, every setting, the backup and the known gaps.
- [`AGENTS.md`](AGENTS.md): the rules for a change, for people and for agents.

## License

MIT. Free for everyone to use, change and share, see [`LICENSE`](LICENSE). The software comes without any warranty.
