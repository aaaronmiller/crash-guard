#!/bin/bash
# Periodic sound monitor — checks ACPI IRQ rate and logs if storm pattern detected
# Runs every 5 minutes via systemd timer
LOG_DIR="$HOME/surface-sound-logs"
mkdir -p "$LOG_DIR"

# Check IRQ9 rate (indicator of ACPI storm that manifests as sound)
C1=$(awk '/acpi/ {s=0; for(i=2;i<=NF-3;i++) s+=$i; print s}' /proc/interrupts 2>/dev/null || echo 0)
sleep 2
C2=$(awk '/acpi/ {s=0; for(i=2;i<=NF-3;i++) s+=$i; print s}' /proc/interrupts 2>/dev/null || echo 0)
RATE=$(( (C2 - C1) / 2 ))

# Also check if audio reset timer ran recently
LAST_RESET=$(journalctl --user -u gnome-audio-reset.service --since "10 minutes ago" --no-pager 2>/dev/null | grep -c "Reset complete" || true)

# Log if IRQ rate is elevated or reset happened
if [ "$RATE" -gt 100 ] || [ "$LAST_RESET" -gt 0 ]; then
    TS=$(date '+%Y-%m-%d_%H%M%S')
    {
        echo "=== Sound Monitor Snapshot ==="
        echo "Time: $(date)"
        echo "IRQ9 rate: ${RATE}/sec"
        echo "Audio resets (10m): $LAST_RESET"
        echo ""
        ps aux --sort=-%cpu | head -10
        echo ""
        pactl list sinks 2>/dev/null | head -20
    } > "$LOG_DIR/sound-monitor-$TS.log"
    # Keep last 50 logs
    ls -t "$LOG_DIR"/sound-monitor-*.log 2>/dev/null | tail -n +51 | xargs rm -f 2>/dev/null || true
fi
