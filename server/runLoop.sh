#!/bin/bash
# Wrapper script to run hauntedHouseLoop2026.py with auto-restart on crash

SCRIPT_NAME="hauntedHouseLoop2026.py"

echo "Starting $SCRIPT_NAME with auto-restart..."
echo "Press Ctrl+C to stop completely"
echo ""

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting $SCRIPT_NAME"

    # Run the script
    uv run "$SCRIPT_NAME"

    EXIT_CODE=$?

    # If exit code is 0, it was a clean exit (user stopped it)
    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Clean exit. Stopping."
        break
    fi

    # If exit code is 130 (Ctrl+C), stop the loop
    if [ $EXIT_CODE -eq 130 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Interrupted by user. Stopping."
        break
    fi

    # Otherwise it crashed, restart after a delay
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $SCRIPT_NAME crashed with exit code $EXIT_CODE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restarting in 3 seconds..."
    sleep 3
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Wrapper script stopped."
