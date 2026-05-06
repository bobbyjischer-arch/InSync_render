#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  InSync — quick update script
#  Run this on your server to update the application
# ═══════════════════════════════════════════════════════════════

set -e

echo "🔄 Updating InSync..."

# Stop service
echo "⏸️  Stopping service..."
sudo systemctl stop insync

# Backup current version
echo "💾 Creating backup..."
sudo cp -r /opt/insync /opt/insync.backup.$(date +%Y%m%d_%H%M%S)

# Pull latest code
echo "📥 Pulling latest code..."
cd /opt/insync
sudo -u insync git pull

# Update dependencies
echo "📦 Updating dependencies..."
sudo -u insync /opt/insync/venv/bin/pip install -r requirements.txt --upgrade

# Run migrations if needed
if [ -f "migrate.sql" ]; then
    echo "🔄 Running migrations..."
    sudo -u postgres psql -d insync -f migrate.sql
fi

# Restart service
echo "▶️  Starting service..."
sudo systemctl start insync

# Check status
echo "✅ Update complete!"
sudo systemctl status insync --no-pager -l

echo ""
echo "📊 Service is running. Check logs with:"
echo "   sudo journalctl -u insync -f"
