# Fluxcord

Discord notification bot for Flux node operators. Monitors your wallet, nodes, and the Flux network — sends rich embeds to a Discord channel via webhook.

---

## Features

### Every 5 minutes
| Check | Description |
|---|---|
| **Wallet activity** | Detects new transactions, shows amount and running balance |
| **DOS alert** | Pings you if any of your nodes appears on the DOS list |
| **Node IP changes** | Alerts on nodes added or removed from your wallet |
| **New app deployed** | Notifies when a new app is installed on one of your nodes |

### Daily (configurable UTC hour)
| Report | Description |
|---|---|
| **Node overview grid** | Compact table: IP, Tier (C/N/S), Rank, Next reward, CPU, RAM, SSD, EPS(Δ), DWS(Δ) |
| **Apps on your nodes** | Table of all apps currently installed across your nodes with resource usage |
| **Network node counts** | Total nodes, per-tier counts with day-over-day change indicators |
| **New global apps** | Apps registered on-chain in the last ~3 days |
| **Arcane OS adoption** | % of network nodes running Arcane OS |
| **Network utilization** | Total vs used CPU/RAM/SSD across all network nodes |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_WEBHOOK` | **Yes** | — | Discord webhook URL |
| `DISCORD_USER` | No | — | Your Discord user ID (enables `@mention` on alerts) |
| `WALLET` | If notifications enabled | — | Your Flux wallet address (used for both wallet and node monitoring) |
| `WALLET_NOTIFICATIONS` | No | `true` | Enable wallet transaction checks |
| `NODE_WALLET_NOTIFICATIONS` | No | `true` | Enable node monitoring checks |
| `NETWORK_NOTIFICATIONS` | No | `true` | Enable daily network reports |
| `DB_PATH` | No | `/data/fluxcord.db` | SQLite state file path |
| `POLL_INTERVAL_MINUTES` | No | `5` | How often to run 5-min checks |
| `DAILY_HOUR_UTC` | No | `8` | UTC hour for daily reports (0–23) |
| `NODE_REQUEST_TIMEOUT` | No | `10` | Per-node HTTP timeout (seconds) |
| `API_REQUEST_TIMEOUT` | No | `30` | Flux API HTTP timeout (seconds) |
| `NEW_APPS_BLOCK_WINDOW` | No | `2880` | Block window for new-app scan (~3 days) |

To get your Discord user ID: enable Developer Mode in Discord settings, right-click your username → Copy User ID.

---

## Running with Docker

### Quick start

```bash
docker run -d \
  --name fluxcord \
  --restart unless-stopped \
  -v fluxcord_data:/data \
  -e DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." \
  -e DISCORD_USER="123456789012345678" \
  -e WALLET="t1YourWalletAddress" \
  ghcr.io/yourname/fluxcord:latest
```

### Build locally

```bash
git clone https://github.com/yourname/fluxcord.git
cd fluxcord

docker build -t fluxcord .

docker run -d \
  --name fluxcord \
  --restart unless-stopped \
  -v fluxcord_data:/data \
  -e DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." \
  -e WALLET="t1YourWalletAddress" \
  fluxcord
```

### View logs

```bash
docker logs -f fluxcord
```

---

## Running locally

### Prerequisites

- Python 3.11+

### Setup

```bash
git clone https://github.com/yourname/fluxcord.git
cd fluxcord
pip install -r requirements.txt
```

### With a `.env` file

Create `.env` in the project root:

```env
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
DISCORD_USER=123456789012345678

WALLET=t1YourWalletAddress

DB_PATH=./fluxcord.db
DAILY_HOUR_UTC=8
```

Load it and run:

```bash
# Linux / macOS
export $(grep -v '^#' .env | xargs)
python -m src.main

# Windows PowerShell
Get-Content .env | Where-Object { $_ -notmatch '^#' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    [System.Environment]::SetEnvironmentVariable($k, $v)
}
python -m src.main
```

---

## Testing

### Unit tests (97 tests, no network required)

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

### One-shot test runner (`run_now.py`)

Runs checks immediately without the scheduler loop. Useful for verifying output in Discord before deploying.

```bash
# Set vars first (PowerShell example)
$env:DISCORD_WEBHOOK = "https://discord.com/api/webhooks/..."
$env:WALLET = "t1YourAddress"
$env:DB_PATH = "./test.db"

# Run everything
python run_now.py

# Run individual checks
python run_now.py --wallet        # wallet check only
python run_now.py --node          # 5-min node check only
python run_now.py --node-daily    # daily node overview only
python run_now.py --network       # daily network check only
python run_now.py --test-dos      # simulate a DOS alert on your first node
```

### Testing delta values (day-over-day changes)

EPS and DWS deltas appear on the second run because the first run stores the baseline. To see `EPS(+50)` style output, run `--node-daily` twice:

```bash
python run_now.py --node-daily   # first run: stores baseline, no deltas shown
python run_now.py --node-daily   # second run: deltas appear in brackets
```

The SQLite database (`DB_PATH`) persists state between runs. Delete it to reset all baselines.

---

## Node Grid Example

```
IP              T Rank   Next   CPU RAM   SSD   EPS          DWS
----------------------------------------------------------------------
65.108.76.194   S 196    3d12h  16  61.0G 880G  6296(+42)    1136.4(-5.1)
167.235.7.162   S 436    3d12h  16  61.0G 880G  6302         1141.2
167.235.7.161   S 441    3d14h  16  61.0G 880G  6288(-18)    1128.9(+3.0)
```

- **T**: Tier — C=Cumulus, N=Nimbus, S=Stratus
- **Next**: Estimated time until next reward
- **EPS**: Elliptic curve operations per second (brackets = change since yesterday)
- **DWS**: Disk write speed in MB/s (brackets = change since yesterday)
