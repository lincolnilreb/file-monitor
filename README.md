# File Monitor

Lightweight Python 3.12+ monitor for monthly inbound files. It checks configured folders, records the first ready time in monthly JSON state, and renders a terminal dashboard.

## Run

```bash
python3 main.py
```

Edit `config.json` to add feeds or change the check interval.

## Configuration

```json
{
  "check_interval_seconds": 300,
  "business_date": "AUTO",
  "date_rule": "LAST_DAY_PREVIOUS_MONTH",
  "feeds": [
    {
      "name": "Customer",
      "source": "EDW",
      "path": "./inbound",
      "filename": "customer_{yyyymmdd}.csv"
    }
  ]
}
```

Use `"business_date": "YYYY-MM-DD"` for manual mode.

Supported date rules:

- `LAST_DAY_PREVIOUS_MONTH`
- `SAME_DAY_PREVIOUS_MONTH`

Supported filename tokens:

- `{yyyy}`, `{yy}`, `{mm}`, `{dd}`
- `{yyyymm}`, `{yyyymmdd}`
- `{yyyy-mm-dd}`, `{yyyy_mm_dd}`

## State

State is written to `state/state_YYYYMM.json`. Ready time is preserved across restarts and completed feeds are skipped in future scans.

## Notifications

On Windows, install `winotify` to enable toast notifications:

```bash
pip install winotify
```

Without it, the monitor still runs and logs notification events.
