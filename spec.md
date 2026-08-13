# File Monitor Specification

## 1. Purpose

File Monitor is a lightweight Python terminal application for monthly inbound feed visibility. It checks configured folders for required files before downstream ETL jobs begin, records when each feed first becomes ready, and gives operators a continuously refreshed dashboard.

The application provides operational visibility only. It does not schedule, trigger, or orchestrate ETL jobs.

## 2. Runtime Requirements

- Python 3.12 or newer.
- Windows and macOS compatible.
- No database.
- No web framework.
- No required third-party packages.
- Optional Windows toast dependency: `winotify`.
- File detection must use `pathlib.Path`; do not use the standalone `glob` module.

## 3. Project Layout

```text
file-monitor/
├── main.py
├── config.json
├── requirements.txt
├── README.md
├── spec.md
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── dashboard.py
│   ├── monitor.py
│   ├── notifier.py
│   ├── state.py
│   └── utils.py
├── state/
│   └── state_YYYYMM.json
├── logs/
│   └── monitor.log
└── tests/
    ├── test_core.py
    ├── folder_001/
    └── folder_002/
```

## 4. Entry Point

`main.py` imports and calls `core.monitor.run_monitor()`.

Run:

```bash
python3 main.py
```

## 5. Configuration File

The application reads `config.json` from the project root.

The file is JSON with top-level `//` comment-line support. Comments are allowed only as whole lines beginning with `//` after optional whitespace. Inline comments are not supported.

`core.config.load_config()` strips these comment lines before parsing with `json.loads()`.

Required runtime fields:

```json
{
  "check_interval_seconds": 300,
  "aggregation_window_seconds": 3,
  "directory_listing_timeout_seconds": 5,
  "scan_strategy": "GROUPED_PER_FILE",
  "business_date": "AUTO",
  "date_rule": "LAST_DAY_PREVIOUS_MONTH",
  "feeds": [
    {
      "name": "Customer",
      "source": "EDW",
      "path": "\\\\server\\share\\{yyyymm}",
      "filename": "customer_{yyyymmdd}.csv"
    }
  ]
}
```

### 5.1 `check_interval_seconds`

Positive integer.

Controls how long the monitor sleeps between cycles.

Examples:

- `10`: local testing.
- `300`: 5-minute production polling.

### 5.2 `aggregation_window_seconds`

Non-negative integer.

After the first ready feed is detected in a cycle, the monitor waits this many seconds and scans again so feeds that arrive nearly at the same time can be included in the same notification batch.

Recommended default: `3`.

Use `0` to disable the additional aggregation wait.

### 5.3 `directory_listing_timeout_seconds`

Positive integer.

For each resolved folder in one scan pass, the monitor first tries to list files once and match expected filenames in memory. If listing that folder takes longer than this value, the monitor falls back to per-file checks for that folder.

Recommended default: `5`.

This timeout is measured while directory entries are being consumed. A single blocking filesystem call from a network share may still take longer before Python regains control.

### 5.4 `scan_strategy`

String.

Controls how feeds are checked after they are grouped by resolved path.

Supported values:

- `"GROUPED_PER_FILE"`: group feeds by path, check each folder once, then check each expected file with `Path.is_file()`. This is the recommended default because it avoids full directory listing on large or slow network folders.
- `"LIST_DIRECTORY"`: list each folder once with `Path.iterdir()` and match expected filenames using an in-memory set. If listing fails or times out, this strategy does not fall back for that folder.
- `"AUTO"`: try `LIST_DIRECTORY` first. If listing fails, is denied, or exceeds `directory_listing_timeout_seconds`, fall back to `GROUPED_PER_FILE` for that folder.

Default: `"GROUPED_PER_FILE"`.

### 5.5 `business_date`

String.

Supported values:

- `"AUTO"`: calculate from today's date and `date_rule`.
- `"YYYY-MM-DD"`: manual date, for example `"2026-07-31"`.

Manual mode must use hyphen format: `YYYY-MM-DD`.

### 5.6 `date_rule`

String.

Used only when `business_date` is `"AUTO"`.

Supported values:

- `"LAST_DAY_PREVIOUS_MONTH"`: use the final calendar day of the previous month.
- `"SAME_DAY_PREVIOUS_MONTH"`: use the same day number in the previous month; if the previous month is shorter, use that month’s final day.

Examples:

| Today | Rule | Business Date |
| --- | --- | --- |
| 2026-08-05 | LAST_DAY_PREVIOUS_MONTH | 2026-07-31 |
| 2026-09-03 | LAST_DAY_PREVIOUS_MONTH | 2026-08-31 |
| 2026-03-31 | SAME_DAY_PREVIOUS_MONTH | 2026-02-28 |

### 5.7 `feeds`

Non-empty list.

Dashboard order must follow this list order.

Each feed requires:

- `name`: unique operator-friendly feed name. Used as the state key and notification feed name.
- `source`: source system or folder label shown on the dashboard.
- `path`: folder containing the inbound file. Fixed paths and date-token path templates are supported. Use absolute paths in production. Windows UNC paths such as `"\\\\server\\share\\{yyyymm}"` are supported.
- `filename`: fixed filename or filename template with date tokens.

Feed names must be unique. Duplicate names are invalid because state is keyed by feed name.

Extra fields are ignored by the current parser.

## 6. Date Templates

Feed paths and filenames may be fixed strings or templates containing date tokens.

Supported tokens:

| Token | Example for 2026-07-31 |
| --- | --- |
| `{yyyy}` | `2026` |
| `{yy}` | `26` |
| `{mm}` | `07` |
| `{dd}` | `31` |
| `{yyyymm}` | `202607` |
| `{yyyymmdd}` | `20260731` |
| `{yyyy-mm-dd}` | `2026-07-31` |
| `{yyyy_mm_dd}` | `2026_07_31` |

Path examples when business date is `2026-07-31`:

| Template | Resolved path |
| --- | --- |
| `/feeds/inbound` | `/feeds/inbound` |
| `/feeds/{yyyymm}` | `/feeds/202607` |
| `/feeds/{yyyy}/{mm}` | `/feeds/2026/07` |
| `\\\\server\\share\\{yyyymmdd}` | `\\\\server\\share\\20260731` |

Filename examples when business date is `2026-07-31`:

| Template | Resolved filename |
| --- | --- |
| `payment_recon.txt` | `payment_recon.txt` |
| `customer_{yyyymmdd}.csv` | `customer_20260731.csv` |
| `{yyyy}.{mm}_customer_daily.txt` | `2026.07_customer_daily.txt` |
| `orders_{yyyy-mm-dd}.xlsx` | `orders_2026-07-31.xlsx` |
| `positions_{yyyy_mm_dd}.txt` | `positions_2026_07_31.txt` |

## 7. Monitoring Flow

Startup:

1. Create `logs/` if needed.
2. Configure logging to `logs/monitor.log`.
3. Load `config.json`.
4. Resolve the business date.
5. Compute the monthly state path: `state/state_YYYYMM.json`.
6. Load existing monthly state if present.
7. Initialize missing feed state entries as `Waiting`.
8. Save initialized state immediately so a fresh month has a state file even before files arrive.

Each monitoring cycle:

1. Log `Refresh`.
2. Capture one `ready_time` for this possible batch.
3. Scan only feeds whose state is not `Ready`.
4. If no feeds are ready, skip notification and render the dashboard.
5. If at least one feed is ready, wait `aggregation_window_seconds`.
6. Scan waiting feeds one more time and add newly ready feeds to the same batch.
   Feeds already collected in the first scan must be skipped during this second scan.
7. Sort the batch by matched filename for predictable notification output.
8. Update all feeds in the batch with the same `ready_time`.
9. Mark all feeds in the batch as `notification_sent`.
10. Send one summary notification for the whole batch.
11. Save state if any feed changed.
12. Render the dashboard once with the latest state.
13. If all feeds are ready, print `All feeds are ready.` and exit.
14. Otherwise sleep `check_interval_seconds`.

Scanning functions must not send notifications. Notification dispatch happens once after the batch has been collected and state has been updated.

Keyboard interrupt:

1. Save current state.
2. Log shutdown.
3. Print `Stopped by operator.`

## 8. File Detection

For each waiting feed:

1. Resolve `path` using business date tokens.
2. Resolve `filename` using business date tokens.
3. Build candidate path as `resolved_path / resolved_filename`.
4. Group feeds by resolved path for the current scan pass.
5. Apply the configured `scan_strategy`.

`GROUPED_PER_FILE`:

1. For each resolved folder, check folder readiness once.
2. For each expected file in that folder, check the candidate with `Path.is_file()`.
3. Run file stability only for candidate files that exist.

`LIST_DIRECTORY`:

1. For each resolved folder, list files once with `Path.iterdir()`.
2. Match expected filenames using an in-memory `set[str]`.
3. Run file stability only for filenames found in the set.
4. If listing fails, is denied, or times out, log the issue and do not mark files in that folder ready during that scan pass.

`AUTO`:

1. Try `LIST_DIRECTORY`.
2. If listing succeeds, use in-memory filename matching.
3. If listing fails, is denied, or exceeds `directory_listing_timeout_seconds`, fall back to `GROUPED_PER_FILE` for that folder.

Within one scan pass, grouped per-file checks should cache directory readiness by resolved path. If many feeds share the same directory, the monitor should check that directory once and reuse the result for the rest of that scan pass.

Only `pathlib.Path` APIs should be used for filesystem detection, including:

- `Path.exists()`
- `Path.is_dir()`
- `Path.is_file()`
- `Path.iterdir()`
- `Path.stat()`

Missing directories, permission errors, and OS errors are logged as warnings and do not stop the application.

## 9. File Stability

Before marking a file ready:

1. Read `candidate.stat().st_size`.
2. Wait 2 seconds.
3. Read `candidate.stat().st_size` again.
4. If sizes match, the file is ready.
5. If sizes differ, keep the feed waiting and log that the file is still changing.

This avoids marking partially copied files as ready.

## 10. State File

State is stored as one JSON file per business month:

```text
state/state_YYYYMM.json
```

Example for business date `2026-07-31`:

```text
state/state_202607.json
```

State shape:

```json
{
  "Customer": {
    "status": "Ready",
    "matched_file": "customer_20260731.csv",
    "ready_time": "2026/08/04 08:15:22",
    "notification_sent": true
  }
}
```

Default feed state:

```json
{
  "status": "Waiting",
  "matched_file": null,
  "ready_time": null,
  "notification_sent": false
}
```

Rules:

- State keys are feed names.
- Missing feed states are added on startup.
- Existing ready state is reused on restart.
- Ready time is written once only and must not be overwritten.
- Completed feeds are skipped in future scans.
- New business month creates a new state file.
- State writes are atomic: write to `.tmp`, then replace the target state file.

## 11. Dashboard

The dashboard redraws in place instead of printing a new dashboard below the previous one.

Rendering uses ANSI clear-screen/home sequence:

```text
\033[2J\033[H
```

The dashboard is rendered once per monitoring cycle after scanning, so it always shows the latest state for that cycle.

Header layout:

```text
╔════════════════════════════════════════════════════════════════════════════╗
║                            Monthly Feed Monitor                           ║
╠════════════════════════════════════════════════════════════════════════════╣
║Business Date : 2026/07/31        Refresh : 2026/08/04 09:35:00           ║
║Interval      : 5 minutes                                  State   : Monitoring║
║Progress      : ███████████░░░░░░░░░░░ 1 / 2 (50%)                         ║
╚════════════════════════════════════════════════════════════════════════════╝
```

Current implementation constants:

- Box width: `78`.
- Inner width: `76`.
- Progress bar width: `22`.
- Ready icon: `✔`.
- Waiting icon: `x`.

Sections:

- `READY`
- `WAITING`

Ready rows show:

- `✔ Feed Name`
- Source
- Actual matched filename
- Ready time

Waiting rows show:

- `x Feed Name`
- Source
- Expected resolved filename

Footer shows:

```text
Ready: N | Waiting: M | Completion: P%
```

## 12. Notifications

Notifications are sent once per ready batch, not once per file.

Batch notification functions:

- `scan_ready_files(...) -> list[ReadyFeed]`: scans waiting feeds only and does not mutate state or notify.
- `collect_ready_batch(...) -> list[ReadyFeed]`: handles the aggregation window and returns one sorted batch.
- `mark_ready_batch(...) -> bool`: applies one shared ready time to the whole batch and marks notification state.
- `build_ready_notification(feed_names, ready_time) -> tuple[str, str]`: builds testable notification text.
- `notify_ready(feed_names, ready_time) -> None`: sends or logs the notification only.

`Ready at` is the time when the system detected the batch as ready. It is not the exact file arrival time or the exact time the file finished writing.

Notification titles must always include the file count:

| Count | Title |
| --- | --- |
| 1 | `1 inbound feed ready` |
| 2-4 | `N inbound feeds ready` |
| 5+ | `N inbound feeds ready` |

Notification messages:

For 1 file:

```text
customer.csv

Ready at 10:30:15
```

For 2 to 4 files, show sorted filenames separated by newlines:

```text
customer.csv
orders.csv
payments.csv

Ready at 10:30:15
```

For 5 or more files, show the actual file count:

```text
8 files are ready

Ready at 10:30:15
```

Behavior:

- Empty feed-name lists must not send notifications.
- On non-Windows systems, notifications are logged only.
- On Windows, `winotify` is imported lazily.
- If `winotify` is missing, the app logs a warning and continues.
- If toast creation fails, the app logs the exception and continues.

`requirements.txt` must not require `winotify`; it is optional.

## 13. Logging

Log path:

```text
logs/monitor.log
```

Logged events:

- Startup
- Refresh
- Ready
- Missing directory warnings
- Permission denied warnings
- OS/file access warnings
- File still changing
- Directory listing performance: `Listed N files from PATH in Ns`
- Directory listing timeout fallback: `Directory listing timed out after Ns for PATH; scanned N entries; falling back to per-file checks`
- Notification skipped or failed
- Startup errors
- Shutdown after all feeds ready
- Shutdown by keyboard interrupt

## 14. Error Handling

Startup errors are fatal:

- Missing config file.
- Invalid config syntax after stripping comment lines.
- Invalid config shape.
- Invalid business date.
- Invalid state JSON.

Runtime scan errors are non-fatal:

- Missing feed directory.
- Permission denied.
- File disappears or changes while checking.
- Other `OSError` from filesystem checks.
- Notification errors.

Keyboard interrupt is handled cleanly and saves current state.

## 15. Test Fixtures

The local test config currently monitors 12 files across two test folders:

`tests/folder_001`:

- `2026.07_customer_daily.txt`
- `account_snapshot_20260731.txt`
- `payment_recon.txt`
- `risk_flags.txt`
- `merchant_notes.txt`
- `customer_extract.xlsx`
- `monthly_summary.xlsx`

`tests/folder_002`:

- `transactions.csv`
- `refunds.csv`
- `audit_log.txt`
- `control_total.txt`
- `load_notes.txt`

If `business_date` is `AUTO` and today's date resolves to a different month, tokenized fixture filenames must be regenerated or `business_date` should be set manually to `2026-07-31` for fixture testing.

## 16. Validation Commands

Run unit tests:

```bash
python3 -m unittest discover -s tests
```

Compile all Python files:

```bash
python3 -m py_compile main.py core/config.py core/dashboard.py core/monitor.py core/notifier.py core/state.py core/utils.py tests/test_core.py
```

Confirm the real config loads:

```bash
python3 -c "from pathlib import Path; from core.config import load_config; c=load_config(Path('config.json')); print(len(c.feeds), c.check_interval_seconds, c.aggregation_window_seconds, c.directory_listing_timeout_seconds, c.scan_strategy, c.business_date, c.date_rule)"
```

Expected output for the current config:

```text
12 10 3 5 GROUPED_PER_FILE AUTO LAST_DAY_PREVIOUS_MONTH
```

Do not use `python3 -m json.tool config.json` for this project config because the file intentionally supports top-level `//` comment lines.

## 17. Success Criteria

- Adding feeds requires only `config.json` changes.
- First ready time is never overwritten after being set.
- Notification is sent once per ready batch.
- All feeds in the same ready batch use the exact same `ready_time`.
- Feeds detected during the aggregation window are included in the same notification batch.
- Completed feeds are skipped on later cycles and after restart.
- Dashboard renders once per cycle and redraws in place.
- Dashboard remains readable with 50 or more feeds.
- Monthly state file is created on startup.
- New business month starts a separate state file.
- Runtime scan errors do not stop monitoring.
- The app remains dependency-light and maintainable.
