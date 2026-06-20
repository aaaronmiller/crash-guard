#!/bin/bash
# Syncs git repo root mtimes to the newest file in each repo.
# Runs every 2 minutes. Lightweight because find is fast.
for repo in ~/code/*/; do
  [ -d "$repo/.git" ] || continue
  # Find the newest file, touch the repo root to match
  newest=$(find "$repo" -type f -not -path '*/.git/*' -not -path '*/node_modules/*' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
  if [ -n "$newest" ]; then
    touch -d "@${newest%.*}" "$repo" 2>/dev/null
  fi
done
