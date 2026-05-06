#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  InSync — database backup script
#  Add to crontab: 0 3 * * * /opt/insync/backup.sh
# ═══════════════════════════════════════════════════════════════

set -e

BACKUP_DIR="/opt/backups/insync"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/insync_$DATE.sql"
KEEP_DAYS=7

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup database
echo "📦 Creating backup: $BACKUP_FILE"
sudo -u postgres pg_dump insync > "$BACKUP_FILE"

# Compress backup
gzip "$BACKUP_FILE"
echo "✅ Backup created: ${BACKUP_FILE}.gz"

# Remove old backups
echo "🧹 Removing backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -name "insync_*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "✅ Backup complete!"
