# claude-usage

Total Claude Code token usage across machines, in one report.

Claude Code keeps its usage data only where it ran: every session is a JSONL
transcript under `~/.claude/projects`, and each assistant record in it carries
the model and the API usage counters of that response. `claude-usage` reads
those files on this machine, ships itself over SSH to every other configured
machine to do the same there, and merges the result.

Claude Code also deletes those transcripts after 30 days (`cleanupPeriodDays`,
default 30). So every run folds what it found into a warehouse file, and the
report reads warehouse plus fresh scans. Run it at least once a month, or let
the systemd timer below do it daily, and nothing ages out uncounted.

## Run

```
claude-usage                      # everything, all machines
claude-usage --last 7d            # last 7 days (also 4w, 3m; m = 30 days)
claude-usage --since 2026-09-01 --until 2026-09-15
claude-usage --by day             # add a per-day table (also week, month)
claude-usage --by entrypoint      # T3 (sdk-ts) vs desktop vs terminal (cli)
claude-usage --by project --top 10
claude-usage --by session --top 10
claude-usage --exact              # full token counts instead of 1.2M
claude-usage --json               # machine-readable, same content
claude-usage --no-remote          # this machine only
claude-usage --host box-a --host box-b   # ad-hoc host list instead of the config
claude-usage --no-warehouse       # live scans only, do not read or update the warehouse
claude-usage update               # fetch current model prices from Anthropic
```

Exit code 2 means at least one machine could not be read. The report still
prints, with that machine marked FAILED in the header, so a missing machine
never shows up as a silent zero.

## Config

`~/.config/claude-usage/config.json`

```json
{
  "hosts": [
    "tobi.coder",
    {"ssh": "other-box", "name": "other", "projects_dir": "~/.claude/projects", "python": "python3"}
  ],
  "ssh_options": ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20"],
  "timeout": 120,
  "warehouse": "~/.local/share/claude-usage/rows.jsonl",
  "pricing": {"claude-new-model": [5.0, 25.0, 0.5]}
}
```

A host is an SSH alias from `~/.ssh/config` or a full `user@host`. A remote
machine needs `python3` on the PATH of a non-interactive SSH shell and nothing
else. `pricing` overrides a model's rates; the three numbers are USD per
million tokens for input, output and cache read, and the cache-write rates
are derived from them. It also accepts the five-key shape `claude-usage
update` writes (`input`, `output`, `cache_read`, `cache_write_5m`,
`cache_write_1h`) to set the cache-write rates directly.

## Prices

Cost estimates use Anthropic's published API list prices, kept in a table
built into the script. Run `claude-usage update` to fetch current prices from
Anthropic's pricing page and store them at
`~/.local/share/claude-usage/pricing.json` (or
`$XDG_DATA_HOME/claude-usage/pricing.json`). A report then reads that file
instead of the built-in table.

Precedence at report time: a `pricing` entry in the config file overrides the
fetched file, and the fetched file overrides the built-in table. The report
header states which one is in effect, for example:

```
Prices: fetched 2026-09-16 from platform.claude.com
Prices: built-in table (2026-09); run 'claude-usage update' for current ones
```

The same fact is in the `--json` output under a `pricing` key.

`claude-usage update` prints how many models it parsed and a diff against the
table it fetched last time, or against the built-in table on the first run, so running it twice in a row with
no upstream change reports nothing changed. It refuses to write anything if
the pricing page does not parse into at least five models, and exits with an
error naming what went wrong.

## Warehouse

`~/.local/share/claude-usage/rows.jsonl` (or `$XDG_DATA_HOME/claude-usage/`,
or the `warehouse` config key). One JSON row per API response, about 200
bytes each, so a few megabytes a year. Every run merges the fresh scans into
it with the same one-row-per-response rule and rewrites it atomically. The
report header shows how many rows it holds, how many this run added, and the
oldest day. Rows keep the machine name they were first seen under, so renaming
a host in the config starts a new machine in the report.

Only the machine running the report has the warehouse. Remote machines are
scanned live each time, so a remote machine that is gone for good still
counts as long as this machine saw it before its transcripts expired.

### Daily snapshot with systemd

```
cp systemd/claude-usage.service systemd/claude-usage.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-usage.timer
systemctl --user list-timers claude-usage.timer
journalctl --user -u claude-usage.service -n 20
```

The service runs `~/.local/bin/claude-usage --json` once a day (with a random
delay of up to 30 minutes, catching up after downtime) and discards the
report. The warehouse update is the point. A failed host shows up in the
journal.

### One-off import of Claude's own stats cache

`~/.claude/stats-cache.json` is a snapshot Claude Code computed at some point
and may hold per-day tokens by model from before your oldest transcript.

```
claude-usage --import-stats-cache            # ~/.claude/stats-cache.json, this machine
claude-usage --import-stats-cache PATH --import-machine NAME
```

The cache stores exact per-model totals for its whole period (input, output,
cache write, cache read) and per day only input plus output per model. The
import spreads each model's totals over its days by that day's share, so the
per-model totals are exact and the per-day split is an estimate. The 5 min vs
1 h cache write split is not recorded, so writes are priced at the cheaper
5 min rate. Only days before the oldest real transcript row of that machine
are imported, so nothing is counted twice. Imported rows do not count as API
calls, show up as entrypoint `stats-cache`, and the report header says how
many there are. Re-running the import replaces the earlier import.

## What the numbers mean

**One row per API response.** Claude Code writes one transcript record per
content block of a streamed response, all with the same message id and
request id, and only the last one has the final output count. The tool keeps
the record with the largest output count per (message id, request id). The
same rule folds a response found on two machines into one, and the header
says how many that was.

**Token types.** Input (uncached), cache write with 5 minute TTL, cache write
with 1 hour TTL, cache read, output. Output includes thinking tokens.

**Cost** is what the tokens would cost at Anthropic API list prices, pay as
you go. On a Max or Team subscription this is not the bill; it is the
comparable number. Rates: input and output per model; cache read per model;
cache writes at 1.25x (5 min) and 2x (1 h) of the input rate; fast mode on
Opus 5 and Opus 4.8 at 2x every rate. Server tool calls (web search) are not
priced. A model missing from the table prints a warning and its tokens are
excluded from the cost, never silently priced at zero. See "Prices" above for
where the rates come from and how to update them.

**Model share** is shown both by tokens and by cost. Since cache reads
dominate the token count, the two differ a lot.

**Time** filters and day/week/month buckets use the local timezone of the
machine running the report.

## What it cannot see

Claude Code on the web (claude.ai/code) stores its sessions in the cloud.
Nothing of them is on any of your machines unless you pull a session down, so
they are not in this report.

Records with model `<synthetic>` (rate limit notices, API errors) carry no
usage and are skipped.
