# crash-guard: Analytics & Metrics

This document describes all structured data streams crash-guard exposes,
how to consume them, and a set of pre-built Grafana dashboard panels /
web UI visualizations for the claude-code-proxy analytics section.

---

## Data Sources

### 1. `crash-guard metrics` — Prometheus text format

Designed for Prometheus scraping. Outputs `# HELP` / `# TYPE` / metric lines
in the standard Prometheus exposition format.

**Usage:**
```bash
crash-guard metrics                          # one-shot
crash-guard metrics --watch 15               # re-emit every 15s (pipe to HTTP)
```

**Metrics exposed:**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `crash_guard_live_sessions` | gauge | `status` (running/stale/crashed/total) | Current sentinel count per status |
| `crash_guard_sessions_by_tool` | gauge | `tool` (claude/pi/hermes/…) | Live sessions grouped by tool key |
| `crash_guard_history_events_total` | counter | `event` (start/stop/restore_ok/…) | Cumulative event counts from history |
| `crash_guard_restore_by_backend` | counter | `event`, `backend` (tmux/ghostty/…) | Restore events broken down by terminal backend |
| `crash_guard_session_duration_seconds` | gauge | `percentile` (p50/p90/p95/p99/max) | Session lifetime percentiles from stop/archive events |
| `crash_guard_system_memory_kb` | gauge | `type` (available/total) | Memory snapshot from last restore event |
| `crash_guard_user_processes` | gauge | — | User process count at last restore |
| `crash_guard_crashed_sentinels_total` | gauge | — | Crashed sentinels from prior boots |

**Prometheus scrape config:**
```yaml
scrape_configs:
  - job_name: 'crash-guard'
    static_configs:
      - targets: ['localhost:9351']  # if served via tiny HTTP wrapper
    metrics_path: /metrics
```

Or use the `textfile` collector with node_exporter:
```bash
crash-guard metrics > /var/lib/node_exporter/textfile_collector/crash-guard.prom
```

### 2. `crash-guard analytics` — Structured JSON

Designed for the claude-code-proxy web UI. Outputs pre-computed statistics
as a single JSON blob.

**Usage:**
```bash
crash-guard analytics                       # compact JSON (for API response)
crash-guard analytics --output pretty        # indented JSON (for debugging)
```

**JSON structure:**

```jsonc
{
  "sessions": {
    "live": { "running": 5, "stale": 0, "crashed": 0, "total": 5 },
    "archived": 9,
    "history_events": 53
  },
  "tools": {
    "claude": { "running": 1, "stale": 0, "crashed": 0, "directories": 2 },
    "pi":     { "running": 2, "stale": 1, "crashed": 0, "directories": 1 }
  },
  "events_per_day": [
    { "date": "2026-06-13", "count": 14 }
  ],
  "event_types": {
    "start": 24, "stop": 18, "restore_ok": 4,
    "restore_fail": 1, "archive": 5
  },
  "events_by_boot": [
    { "boot": "75a46dea", "count": 40 }
  ],
  "restore_by_backend": {
    "tmux": { "attempts": 6, "ok": 5, "fail": 1, "success_rate": 83.3 }
  },
  "session_duration_seconds": {
    "count": 27, "min": 0, "max": 246340,
    "p50": 120, "p90": 54000, "p95": 135000, "p99": 246000
  },
  "last_system_snapshot": {
    "mem_total_kb": 32456789,
    "mem_avail_kb": 5072891,
    "live_sentinels": 5,
    "user_procs": 234
  },
  "ephemeral_ratio": {
    "ephemeral": 9,
    "total_with_duration": 27,
    "ratio": 0.333
  },
  "boot_periods": [
    {
      "boot_id": "75a46dea",
      "start": "2026-06-10 02:14:28 -0700",
      "end": "2026-06-13 12:10:10 -0700",
      "records": 21,
      "running": 5,
      "closed": 14,
      "recoverable": 2
    }
  ],
  "cwd_concentration": [
    { "cwd": "/home/x/code/crash-guard", "sessions": 3 }
  ]
}
```

### 3. `~/.local/share/crash-guard/history.jsonl` — Raw append-only log

The canonical record. Each line is a JSON object with at minimum:
- `event`: start, stop, archive, restore_start, restore_attempt, restore_ok,
  restore_fail, restore_done
- `at`: ISO 8601 timestamp
- `record`: session metadata (inv_id, key, cwd, argv, env, boot_id, …)
- For restore events: `backend`, `system` (memory snapshot), `plan` (summary)

**Raw data access for the web UI:**
```bash
# Tail the last N events
tail -50 ~/.local/share/crash-guard/history.jsonl | jq -s '.'

# Watch for new events
tail -f ~/.local/share/crash-guard/history.jsonl
```

---

## Pre-built Visualizations for the Web UI

Below are 12 visualization use cases that the claude-code-proxy analytics
page can implement. Each includes the data source, query, and rendering
suggestion.

### 1. Session Lifetime Distribution
**Data:** `analytics.session_duration_seconds`  
**Render:** Horizontal bar chart or histogram (p50/p90/p95/p99/max)  
**Insight:** Are most sessions short (<1hr) or long-running (>24hr)?  
**Alert threshold:** If p50 > 24hr → sessions may be leaking.

### 2. Session Status Pie
**Data:** `analytics.sessions.live` (running/stale/crashed) + archived  
**Render:** Donut chart  
**Insight:** Quick visual of how many sessions are alive vs crashed vs archived.

### 3. Crash Timeline
**Data:** `analytics.events_per_day` filtered to event=restore_start  
       + `history.jsonl` events with boot_id change markers  
**Render:** Time-series line chart with crash annotations  
**Insight:** Spikes in crashed sentinels track WSL/desktop crashes.

### 4. Restore Success Rate by Backend
**Data:** `analytics.restore_by_backend`  
**Render:** Stacked bar chart (green=ok, red=fail per backend)  
**Insight:** Which terminal backend is most reliable?  
**Alert threshold:** If any backend success_rate < 80%.

### 5. Tool Popularity
**Data:** `analytics.tools` (running counts per tool)  
**Render:** Horizontal bar chart  
**Insight:** Which AI tools are used most often.

### 6. Memory Pressure Before Crashes
**Data:** `analytics.last_system_snapshot.mem_avail_kb` from
       `history.jsonl` restore_start events where restore_fail exists  
**Render:** Scatter plot (time vs mem_avail_mb, color=fail/success)  
**Insight:** Do crashes happen below a memory threshold?  
**Alert threshold:** If mem_avail < 1GB → warn user.

### 7. Concurrent Session Overlap
**Data:** `history.jsonl` events, compute overlapping time windows
       from start/stop events  
**Render:** Gantt chart or timeline swimlane (one lane per tool)  
**Insight:** How many agents run simultaneously? Peak concurrency?

### 8. Directory Concentration
**Data:** `analytics.cwd_concentration`  
**Render:** Treemap or horizontal bar  
**Insight:** Which project directories spawn the most sessions.

### 9. Boot-to-Crash Pattern
**Data:** `analytics.boot_periods`  
**Render:** Timeline with boot markers (one row per boot_id, colored by
       crashed/recoverable ratio)  
**Insight:** Are crashes clustered in specific boots?

### 10. Ephemeral vs Real Session Ratio
**Data:** `analytics.ephemeral_ratio`  
**Render:** Small stat card (e.g. "33% of sessions were ephemeral")  
**Insight:** How much noise from test launches vs real work sessions.  
**Trend:** A rising ephemeral ratio might indicate broken aliases.

### 11. Restore Plan Size Distribution
**Data:** `history.jsonl` events. For each restore_start event,
       count `plan.length`.  
**Query:** `jq 'select(.event=="restore_start") | .plan | length'`  
**Render:** Histogram of how many sessions get restored at once  
**Insight:** Are most restores single-session or multi-session?

### 12. Session Duration by Tool
**Data:** `history.jsonl` events where event=stop, group by
       `record.key`, plot `duration_secs`  
**Render:** Box plot or violin chart (one distribution per tool)  
**Insight:** Does Claude run longer than Pi? Which tools are
       typically short-lived?

---

## Grafana Dashboard JSON

A complete Grafana dashboard JSON is provided in
`grafana/crash-guard-dashboard.json`. Import it via:
```
Grafana → + → Import → Upload JSON
```

It includes panels for all 12 visualizations above, pre-configured with
the Prometheus data source pointing at the crash-guard metrics endpoint.

### Loki / Promtail integration (optional)

For log-level analytics, ship `history.jsonl` to Loki:

```yaml
# promtail.yaml
scrape_configs:
  - job_name: crash-guard
    static_configs:
      - targets: [localhost]
        labels:
          job: crash-guard
          __path__: /home/misscheta/.local/share/crash-guard/history.jsonl
    pipeline_stages:
      - json: {}
      - labels:
          event: event
```

Then query in Grafana Explore:
```
{job="crash-guard"} |= `restore_fail`
```

---

## Serving the Metrics Endpoint

The claude-code-proxy web UI can serve a `/crash-guard/metrics` endpoint
by subprocessing:

```python
# In the web server (FastAPI/Flask/whatever)
@app.get("/crash-guard/metrics")
async def crash_guard_metrics():
    import subprocess
    result = subprocess.run(
        ["crash-guard", "metrics"],
        capture_output=True, text=True, timeout=10
    )
    return Response(
        content=result.stdout,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Type": "text/plain; version=0.0.4"}
    )
```

And an `/crash-guard/analytics` endpoint:

```python
@app.get("/crash-guard/analytics")
async def crash_guard_analytics():
    import subprocess, json
    result = subprocess.run(
        ["crash-guard", "analytics"],
        capture_output=True, text=True, timeout=10
    )
    return json.loads(result.stdout)
```

---

## File Locations

| Resource | Path |
|----------|------|
| Raw history | `~/.local/share/crash-guard/history.jsonl` |
| Live sentinels | `~/.local/share/crash-guard/live/*.json` |
| Archive | `~/.local/share/crash-guard/archive/*.json` |
| Grafana dashboard | `grafana/crash-guard-dashboard.json` |
| Metrics CLI | `crash-guard metrics` |
| Analytics CLI | `crash-guard analytics --output pretty` |
