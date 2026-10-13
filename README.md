# NXTVOLT Procurement (Odoo 20)

Internal procurement system for NXTVOLT, built as a custom Odoo module.
NXTVOLT receives purchase orders (POs) from clients, asks its vendors for
quotes, chooses a vendor per item, buys the items and reports progress to the
client.

| Component  | Version                         |
|------------|---------------------------------|
| Odoo       | Community **20.0** (image `odoo:20.0`) |
| PostgreSQL | **16** (image `postgres:16`)    |
| Python     | 3.12 (inside the Odoo image)    |

## Layout

```
addons/                 our Odoo modules (mounted at /mnt/extra-addons)
config/odoo.conf        Odoo server configuration (development only)
docker/odoo/            dev image: official odoo:20.0 + debugpy + reload fix
docker-compose.yml      the two services: db and odoo
.vscode/                editor settings and the debugger attach configuration
```

The module is `addons/procurement_custom`:

```
models/      sale.order / sale.order.line extensions, milestones, delivery
             groups, vendor quotes, purchases
wizard/      the "Request Quotes" dialog
views/       forms, lists, the quote comparison screen, menus
report/      QWeb PDF reports (client status, internal, PO reproduction,
             vendor quote request)
security/    ir.access.csv: who may read / create / update / delete what
data/        unit price precision (6 decimals)
```

Odoo's own source code is **not** in this repository and is never edited.
All customisation lives in `addons/` and uses Odoo's inheritance mechanisms.

## Prerequisites

- Docker Desktop (running)
- Git
- VS Code with the *Python* and *Python Debugger* extensions
- A shallow clone of the Odoo source for editor autocomplete, outside this
  folder (about 1 GB):

  ```powershell
  git clone --depth 1 --branch 20.0 --single-branch https://github.com/odoo/odoo.git D:\odoo-src\20.0
  ```

  If you put it somewhere else, update the path in `.vscode/settings.json` and
  `.vscode/launch.json`.

## Start and stop

Run these from the project folder.

```powershell
docker compose up -d --build     # start (first run builds the dev image)
docker compose logs -f odoo      # follow the Odoo log (Ctrl+C stops following)
docker compose stop              # stop, keep everything
docker compose down              # stop and remove containers, KEEP the data
docker compose down -v           # also DELETE the database and attachments
```

Odoo: <http://localhost:8069>

## Create a database (first time only)

1. Open <http://localhost:8069>. With no database yet, Odoo shows the
   *Create Database* form (also reachable at `/web/database/manager`).
2. Fill it in:
   - **Master Password**: `admin` (the `admin_passwd` value in `config/odoo.conf`)
   - **Database Name**: `nxtvolt`
   - **Email**: your login, e.g. `admin`
   - **Password**: your login password
   - **Language** / **Country**: English / Egypt
   - **Demo data**: tick it. The module's sample client PO is demo data, and
     demo data is only loaded into databases created with this option.
3. Click **Create database**. After a minute or two you are logged in.

The commands below assume the database is called `nxtvolt`.

## Install or upgrade the module

Odoo keeps models, views, menus and access rules in the database. After
changing a module you must tell Odoo to re-read its files: that is an
*upgrade* (`-u`). The first time, you *install* it (`-i`).

```powershell
# install (first time)
docker compose exec odoo odoo -d nxtvolt -i procurement_custom --stop-after-init --no-http

# upgrade (after changing models, views, security or data files)
docker compose exec odoo odoo -d nxtvolt -u procurement_custom --stop-after-init --no-http
```

`--stop-after-init --no-http` runs a short-lived second Odoo process that
updates the database and exits; the server you use in the browser keeps
running and picks up the change. You can also install from the browser:
**Apps**, remove the *Apps* filter, search for the module, **Activate**.

What needs what during development:

| You changed                              | What to do                         |
|------------------------------------------|------------------------------------|
| Python method bodies                     | nothing: the server auto-restarts  |
| View / report XML of an installed module | refresh the browser (`--dev=xml`)  |
| Fields, models, new files, security, data, manifest | upgrade the module (`-u`) |

## Demo data

In a database created with *Demo data* ticked, the module loads the real
client PO No. 65282 from Etisalat Misr (`demo/demo_po_65282.xml`): all header
fields, 14 lines, payment milestones, delivery groups and the page-2 terms.
Find it under **Procurement > Client POs**.

The three vendors, their quotes and the purchases in that file are invented so
the screens have data; so is the advance payment date. The delivery group of
each line is an assumption that must be confirmed against Annex-I.

## Run the tests

```powershell
docker compose exec odoo odoo -d nxtvolt -u procurement_custom --test-tags /procurement_custom --stop-after-init --http-port=8070
```

- `--test-tags /procurement_custom` runs only this module's tests.
- `--http-port=8070`: in test mode Odoo always starts an HTTP server, and 8069
  is already used by the running dev server.
- The last log line reports the result, e.g.
  `0 failed, 0 error(s) of 17 tests`. The exit code is non-zero on failure.
- One test checks the demo PO (No. 65282) and is skipped in a database
  created without demo data.

Tests run inside a transaction that is rolled back, so they do not change
your data.

## Debug with VS Code

The Odoo process always runs under [debugpy](https://github.com/microsoft/debugpy),
listening on `localhost:5678`. It does not wait for a debugger.

1. Start the stack (`docker compose up -d`).
2. In VS Code: **Run and Debug** > **Attach to Odoo (Docker)** > F5.
3. Put a breakpoint in a file under `addons/` and trigger that code from the
   browser. Execution pauses in VS Code.

Notes:

- Breakpoints also work in the Odoo framework (`D:\odoo-src\20.0\odoo\...`).
  The local clone may be a few commits away from the code in the image, so a
  line can occasionally be off by a little there.
- After an automatic reload the debugger re-attaches to the new server process
  by itself (`"subProcess": true` in `launch.json`).

## Useful commands

```powershell
# Odoo interactive shell: `env` is available, e.g. env['sale.order'].search([])
docker compose exec odoo odoo shell -d nxtvolt --no-http

# SQL prompt on the database
docker compose exec db psql -U odoo -d nxtvolt
```

PostgreSQL is also reachable from Windows tools on `localhost:5433`
(user `odoo`, password `odoo`).

## Things that are specific to this setup

- **Odoo 20 listens on 127.0.0.1 by default.** In a container that is
  unreachable from outside, so `odoo.conf` sets `http_interface = 0.0.0.0`.
  The ports are still published on this machine's `127.0.0.1` only.
- **Auto-reload on a Windows drive.** Linux file-change notifications
  (inotify) do not work on folders shared from Windows into a container.
  `docker/odoo/nxtvolt_dev_watch.py` makes Odoo's watcher poll `./addons`
  instead. The explanation is in that file.
- **Passwords in this repository are for local development only.**
