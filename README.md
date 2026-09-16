# claude-usage

Total Claude Code token usage across machines, in one report.

Claude Code keeps its usage data only where it ran: every session is a JSONL
transcript under `~/.claude/projects`, and each assistant record in it carries
the model and the API usage counters of that response. `claude-usage` reads
those files on this machine, ships itself over SSH to every other configured
machine to do the same there, and merges the result.

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
  "pricing": {"claude-new-model": [5.0, 25.0, 0.5]}
}
```

A host is an SSH alias from `~/.ssh/config` or a full `user@host`. A remote
machine needs `python3` on the PATH of a non-interactive SSH shell and nothing
else. `pricing` overrides or extends the built-in table; the three numbers are
USD per million tokens for input, output and cache read.

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
excluded from the cost, never silently priced at zero.

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
