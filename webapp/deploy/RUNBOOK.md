# Webapp production deployment runbook (tailnet-only)

Everything here is run **on the home server**, as the same Windows
account the bot's own scheduled tasks already run as. Nothing here
touches any bot file, bot script, or the live bot DB directly -- the
webapp only ever reads a **snapshot copy** (step 6), and the production
app itself refuses to start if `STOCKSAGE_DB_PATH` resolves inside the
repo's own `db/` directory (enforced in code, not just by convention).

## 0. Merge timing, then wait for auto_sync

Merge the `feature/webapp` PR **outside US market hours** and **outside
the bot's own nightly job windows (01:30 / 02:15 / 02:30 IL)** -- avoids
any chance of contending with a live bot DB write or a running
evaluation job while you're setting things up.

The server already runs `scripts\auto_sync.ps1` every 15 minutes, which
fast-forwards `origin/main` and restarts only the "StockSage Bot" task
-- it has no idea the webapp exists. After merging, **wait** for
`logs\auto_sync.log` (bot-side, at the repo root) to print:

```
[<timestamp>] pulled successfully, now at <hash> -- restarting StockSage Bot task
```

or, if nothing changed since the last cycle, `up to date (<hash>)` once
it has had a chance to run after your merge. **Only after that** create
or touch anything inside the repo checkout on this server (the new
`webapp/deploy/.env`, the venv, etc.) -- otherwise you risk racing
`auto_sync`'s own dirty-tree check against your in-progress setup.

## 1. Check port 8000 is free

```
netstat -ano | findstr :8000
```
Should print nothing. If something is already listening, find out what
before continuing.

## 2. One-time Python environment

```
cd webapp\backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 3. Create the outside-the-repo deploy folder

```
mkdir C:\stocksage-deploy
mkdir C:\stocksage-deploy\dist
mkdir C:\stocksage-deploy\snapshot
mkdir C:\stocksage-deploy\logs
```
`dist\` can be populated by `scp` **at any time**, before or after this
setup -- nothing reads it until the webapp task actually starts (step
9). Every snapshot DB and every new log this round produces lives
somewhere under this folder, never inside the repo.

## 4. Configure env files

Copy `webapp\backend\.env.example` to `webapp\backend\.env` and set:
```
WEBAPP_PRODUCTION=true
STOCKSAGE_DB_PATH=C:\stocksage-deploy\snapshot\bot.db
```
(the **snapshot** destination from step 6/7 below, never the live bot
DB -- the app refuses to start otherwise).

Copy `webapp\deploy\.env.example` to `webapp\deploy\.env` and set:
```
SNAPSHOT_SOURCE_DB_PATH=<path to the live bot DB, e.g. ..\..\db\stocksage.db>
```
(`SNAPSHOT_DEST_DB_PATH` and `SNAPSHOT_LOG_PATH` can be left at their
defaults -- `C:\stocksage-deploy\snapshot\bot.db` and
`C:\stocksage-deploy\logs\snapshot.log` -- which already match step 4's
`STOCKSAGE_DB_PATH` above.)

## 5. Build and copy the frontend

On your own machine (not the server):
```
cd webapp\frontend
npm run build
```
Then `scp` the resulting `dist\` folder to the server:
```
scp -r webapp\frontend\dist\* youraccount@server:C:\stocksage-deploy\dist\
```
Keep the previous `dist\` around somewhere (e.g. rename it before
overwriting) so step 12 (rollback) has something to restore.

## 6. Run the snapshot job once manually, before starting the webapp

```
webapp\deploy\run_snapshot_job.cmd
```
Then check `C:\stocksage-deploy\logs\snapshot.log` for a clean
`snapshot ok -- ...` line. **Do this before registering or starting the
webapp task** -- otherwise the webapp's own startup check (dist/DB
existence, see `app/main.py`'s `_check_production_prereqs`) has nothing
valid to read on its very first launch.

## 7. Register the scheduled tasks

In an **elevated** PowerShell window:
```
webapp\deploy\install_snapshot_task.ps1
webapp\deploy\install_webapp_task.ps1
```
Each prompts once for the Windows account password and verifies its own
registration against `Get-ScheduledTask`.

## 8. Expose it over the tailnet

Check your installed Tailscale version's flags first:
```
tailscale serve --help
```
Then serve the loopback-bound app:
```
tailscale serve https / http://127.0.0.1:8000
```
(adjust to whatever your version's `tailscale serve --help` output
actually shows -- flags have changed across versions). Confirm it's
`serve`, not `funnel`, that's configured:
```
tailscale serve status
tailscale status
```
`funnel` would expose this to the public internet, which is explicitly
not wanted here -- only `serve` (tailnet-only) should ever show up.

## 9. Verify from a phone on the tailnet

- Load the tailnet URL -- the dashboard renders.
- Load a nonsense client-side path -- the SPA shell still renders
  (client-side routing fallback).
- Load `/api/nonsense` -- plain JSON 404, never HTML.
- Load `/docs` -- not the real Swagger UI (falls through to the SPA
  shell, same as any other unknown path -- see `app/main.py`).

Also confirm the app is **not** reachable from the server's LAN IP
(it binds to `127.0.0.1` only, even with `tailscale serve` in front of
it):
```
curl http://<server-lan-ip>:8000/api/health
```
from another device on the LAN (not over the tailnet) -- this should
time out or refuse the connection.

## 10. Restart confidence check

```
schtasks /End /TN "StockSage Webapp"
schtasks /Run /TN "StockSage Webapp"
tailscale serve status
```
Then reload the page -- confirms the task survives a clean restart, not
just a fresh manual start. Optionally do this once as an actual
off-hours server reboot instead, for full at-startup-trigger confidence.

## 11. Rollback

- Stop the task: `schtasks /End /TN "StockSage Webapp"`.
- Restore the previous `dist\` (the one you kept in step 5), or revert
  the `main` merge and wait for/trigger `auto_sync`.
- Restart: `schtasks /Run /TN "StockSage Webapp"`.

## 12. Confirm the bot's auto_sync is still healthy

After all of the above, check `logs\auto_sync.log` (bot-side) keeps
printing `up to date (<hash>)` on its normal 15-minute cadence -- i.e.
nothing about the new webapp files made `git status` dirty, which would
make `auto_sync.ps1` skip every cycle per its own documented safety
rule.

## 13. Runtime-path / gitignore checklist

All already covered by the existing root `.gitignore` -- nothing new to
add:

| Path | Gitignored by | Notes |
|---|---|---|
| `webapp/**/node_modules/` | `webapp/**/node_modules/` | frontend deps |
| `webapp/**/dist/` | `webapp/**/dist/` | built frontend (also copied to `C:\stocksage-deploy\dist`, outside the repo) |
| `webapp/**/.venv/` | `webapp/**/.venv/` | backend venv |
| `webapp/data/` | `webapp/data/` | webapp's own state DB |
| `webapp/**/*.db*` | `webapp/**/*.db*` | any stray local DB file |
| `webapp/backend/.env` | `webapp/**/.env*` (except `.env.example`) | backend config |
| `webapp/deploy/.env` | `webapp/**/.env*` (except `.env.example`) | snapshot job config |

Every snapshot DB, every deploy-script log, and the served `dist/` all
live under `C:\stocksage-deploy\`, entirely outside the repo -- so even
a bug in one of these scripts can't dirty the server's `git status`.
