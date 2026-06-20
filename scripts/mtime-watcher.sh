#!/bin/bash
# Real-time mtime sync using fatrace (kernel fanotify — no inotify limits)
# Watches ALL write events on the /home mount and touches matching git repo roots
LOG=/tmp/mtime-watcher.log
exec > "$LOG" 2>&1
echo "=== mtime-watcher (fanotify) started at $(date) ==="

# Pre-cache repo root lookups
declare -A REPO_CACHE
while IFS= read -r dir; do
    REPO_CACHE["$dir"]=1
done < <(find /home/misscheta/code -maxdepth 3 -name .git -type d -not -path '*/node_modules/*' -exec dirname {} \; 2>/dev/null)

echo "Cached ${#REPO_CACHE[@]} repos"
echo "Ready."

# Watch for write events via fanotify
fatrace --current-mount --filter=W | while read -r pid comm wpath rest; do
    # wpath is the full path of the written file
    # Walk up to find a matching repo root
    dir="$wpath"
    while [ "$dir" != "/" ]; do
        if [[ -n "${REPO_CACHE[$dir]}" ]]; then
            touch "$dir" 2>/dev/null
            break
        fi
        dir=$(dirname "$dir" 2>/dev/null)
    done
done
