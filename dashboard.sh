#!/bin/bash
# ML Dashboard Launcher Script
# This script provides easy start/stop/status commands for the dashboard

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="ml-dashboard"
SERVICE_FILE="$SCRIPT_DIR/ml-dashboard.service"

case "$1" in
    start)
        echo "Starting ML Dashboard..."
        if systemctl --user is-active --quiet $SERVICE_NAME; then
            echo "✓ Dashboard is already running at http://localhost:8000"
        else
            systemctl --user start $SERVICE_NAME
            sleep 2
            if systemctl --user is-active --quiet $SERVICE_NAME; then
                echo "✓ Dashboard started successfully!"
                echo "  Access at: http://localhost:8000"
            else
                echo "✗ Failed to start dashboard"
                systemctl --user status $SERVICE_NAME
                exit 1
            fi
        fi
        ;;
    stop)
        echo "Stopping ML Dashboard..."
        systemctl --user stop $SERVICE_NAME
        echo "✓ Dashboard stopped"
        ;;
    restart)
        echo "Restarting ML Dashboard..."
        systemctl --user restart $SERVICE_NAME
        sleep 2
        if systemctl --user is-active --quiet $SERVICE_NAME; then
            echo "✓ Dashboard restarted successfully!"
            echo "  Access at: http://localhost:8000"
        else
            echo "✗ Dashboard failed to restart"
            systemctl --user status $SERVICE_NAME
            exit 1
        fi
        ;;
    status)
        if systemctl --user is-active --quiet $SERVICE_NAME; then
            echo "✓ Dashboard is running"
            echo "  Access at: http://localhost:8000"
            echo ""
            systemctl --user status $SERVICE_NAME
        else
            echo "✗ Dashboard is not running"
            echo ""
            systemctl --user status $SERVICE_NAME
        fi
        ;;
    logs)
        echo "Showing recent dashboard logs (Ctrl+C to exit):"
        journalctl --user -u $SERVICE_NAME -f
        ;;
    install)
        echo "Installing ML Dashboard service..."
        mkdir -p ~/.config/systemd/user/
        cp "$SERVICE_FILE" ~/.config/systemd/user/
        systemctl --user daemon-reload
        systemctl --user enable $SERVICE_NAME
        echo "✓ Service installed and enabled (will start on login)"
        echo ""
        echo "To start now: $0 start"
        ;;
    uninstall)
        echo "Uninstalling ML Dashboard service..."
        systemctl --user stop $SERVICE_NAME 2>/dev/null
        systemctl --user disable $SERVICE_NAME 2>/dev/null
        rm -f ~/.config/systemd/user/$SERVICE_NAME.service
        systemctl --user daemon-reload
        echo "✓ Service uninstalled"
        ;;
    open)
        xdg-open http://localhost:8000 &
        echo "✓ Opening dashboard in browser..."
        ;;
    *)
        echo "ML Workstation Dashboard - Control Script"
        echo ""
        echo "Usage: $0 {install|start|stop|restart|status|logs|open|uninstall}"
        echo ""
        echo "Commands:"
        echo "  install   - Install as systemd service (auto-start on login)"
        echo "  start     - Start the dashboard"
        echo "  stop      - Stop the dashboard"
        echo "  restart   - Restart the dashboard"
        echo "  status    - Check if dashboard is running"
        echo "  logs      - View live dashboard logs"
        echo "  open      - Open dashboard in browser"
        echo "  uninstall - Remove systemd service"
        echo ""
        echo "Quick start:"
        echo "  1. $0 install    # One-time setup"
        echo "  2. $0 start      # Start dashboard"
        echo "  3. $0 open       # Open in browser"
        exit 1
        ;;
esac
