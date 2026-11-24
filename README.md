# HomeWizard P1 Logger & Dashboard

HomeWizard P1 reads a local HomeWizard P1 meter (Local API v1) every 10 seconds. It stores each reading in SQLite. It watches
the phases and the voltage, sends alarms through Signal, and shows statistics and power quality in a web page.

It uses the Python 3 standard library only. Nothing to install with `pip`.

> **Built with AI assistance.** Google Gemini helped to create the first version of this program. Claude (Anthropic) helped to
> clean it up, test it, fix problems that a review found, package it for Docker and write the documentation. The owner decides what
> the program does, adjusts the installation and runs it on their own network. See
> [AI assistance](AGENTS.md#ai-assistance) for who did what and how the result is checked.

## Install

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

No install on the host. You need Docker with the Compose plugin.

```sh
cp .env.example .env     # set HW_METER_URL, and the HW_SIGNAL_* values for Signal
docker compose up -d --build
```

Open `http://<host>:8085/`. The database is in the Docker volume `data` and stays when you update. See "Run with Docker" in
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

The program has no sign-in. Use it on the home network only, or through a VPN. Never open a port to the internet.

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
| `install/` | The install script, the script that moves an older install, the systemd units and the backup script. |
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
