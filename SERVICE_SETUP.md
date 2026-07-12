# ML Dashboard - Background Service Setup

## Quick Setup (Choose One Option)

### Option 1: Desktop Shortcut (Manual Start/Stop)

**One-time setup**:

```bash
# Copy desktop shortcut to applications menu
cp ml-dashboard.desktop ~/.local/share/applications/

# Update desktop database
update-desktop-database ~/.local/share/applications/
```

**Usage**:

- Find "ML Dashboard" in your applications menu
- Right-click for quick actions: Start, Stop, Restart, Status, Open Browser

---

### Option 2: Systemd Service (Auto-start on Login)

**One-time setup**:

```bash
./dashboard.sh install
```

**Usage**:

```bash
./dashboard.sh start      # Start dashboard
./dashboard.sh stop       # Stop dashboard
./dashboard.sh restart    # Restart dashboard
./dashboard.sh status     # Check status
./dashboard.sh logs       # View live logs
./dashboard.sh open       # Open in browser
```

**Benefits**:

- ✅ Auto-starts on system boot/login
- ✅ Auto-restarts if it crashes
- ✅ Runs in background (no terminal needed)
- ✅ Logs to systemd journal

---

## Current Status

The dashboard is currently running in your terminal (PID 46417). To switch to background service:

```bash
# Stop the terminal version
# Press Ctrl+C in the terminal running app.py

# Install and start as service
./dashboard.sh install
./dashboard.sh start
./dashboard.sh open
```

---

## Files Created

1. **dashboard.sh** - Control script for managing the dashboard
2. **ml-dashboard.service** - Systemd service definition
3. **ml-dashboard.desktop** - Desktop application shortcut

---

## Troubleshooting

**Check if running**:

```bash
./dashboard.sh status
```

**View logs**:

```bash
./dashboard.sh logs
# or
journalctl --user -u ml-dashboard -f
```

**Restart if stuck**:

```bash
./dashboard.sh restart
```

**Completely reset**:

```bash
./dashboard.sh stop
pkill -f "python app.py"  # Kill any orphaned processes
./dashboard.sh start
```

---

## Recommended: Systemd Service

I recommend using the systemd service (Option 2) because it:

- Starts automatically when you log in
- Restarts automatically if it crashes
- Properly manages logs
- Frees your terminal for other work

To set it up now:

```bash
# First, stop the current terminal instance (Ctrl+C in the terminal)
# Then run:
cd /home/omar/ai-projects/workstation-dashboard
./dashboard.sh install
./dashboard.sh start
./dashboard.sh open
```
