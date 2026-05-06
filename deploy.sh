#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  InSync — deployment script for Ubuntu/Debian servers
#  Run this on your server after cloning the repo.
# ═══════════════════════════════════════════════════════════════

set -e  # Exit on any error

echo "🚀 Starting InSync deployment..."

# ── 1. System packages ──────────────────────────────────────────
echo "📦 Installing system dependencies..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv postgresql nginx certbot python3-certbot-nginx

# ── 2. PostgreSQL setup ─────────────────────────────────────────
echo "🗄️  Setting up PostgreSQL..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'insync'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE insync;"

sudo -u postgres psql -tc "SELECT 1 FROM pg_user WHERE usename = 'insync'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER insync WITH PASSWORD 'CHANGE_THIS_PASSWORD';"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE insync TO insync;"

# ── 3. Application user ─────────────────────────────────────────
echo "👤 Creating application user..."
if ! id -u insync >/dev/null 2>&1; then
    sudo useradd -r -m -d /opt/insync -s /bin/bash insync
fi

# ── 4. Copy files ───────────────────────────────────────────────
echo "📁 Copying application files..."
sudo mkdir -p /opt/insync
sudo cp -r . /opt/insync/
sudo chown -R insync:insync /opt/insync

# ── 5. Python environment ───────────────────────────────────────
echo "🐍 Setting up Python virtual environment..."
sudo -u insync python3 -m venv /opt/insync/venv
sudo -u insync /opt/insync/venv/bin/pip install --upgrade pip
sudo -u insync /opt/insync/venv/bin/pip install -r /opt/insync/requirements.txt

# ── 6. Environment configuration ────────────────────────────────
echo "⚙️  Configuring environment..."
if [ ! -f /opt/insync/.env ]; then
    echo "⚠️  Creating .env from template..."
    sudo cp /opt/insync/.env.production /opt/insync/.env

    # Generate new AES key
    NEW_AES_KEY=$(sudo -u insync /opt/insync/venv/bin/python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    sudo sed -i "s|REPLACE_WITH_NEW_KEY|$NEW_AES_KEY|g" /opt/insync/.env

    echo ""
    echo "⚠️  IMPORTANT: Edit /opt/insync/.env and set:"
    echo "   - DATABASE_URL password"
    echo "   - APP_URL (your domain)"
    echo "   - SMTP credentials if needed"
    echo ""
    read -p "Press Enter after editing .env file..."
fi

sudo chmod 600 /opt/insync/.env
sudo chown insync:insync /opt/insync/.env

# ── 7. Database migration ───────────────────────────────────────
echo "🔄 Running database migrations..."
sudo -u insync bash -c "cd /opt/insync && /opt/insync/venv/bin/python3 -c '
import asyncio
from database.engine import init_db
asyncio.run(init_db())
print(\"✅ Database initialized\")
'"

# ── 8. Systemd service ──────────────────────────────────────────
echo "🔧 Installing systemd service..."
sudo cp /opt/insync/insync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable insync
sudo systemctl restart insync

# ── 9. Nginx configuration ──────────────────────────────────────
echo "🌐 Configuring nginx..."
sudo cp /opt/insync/insync.nginx.conf /etc/nginx/sites-available/insync
sudo ln -sf /etc/nginx/sites-available/insync /etc/nginx/sites-enabled/insync
sudo rm -f /etc/nginx/sites-enabled/default

# Test nginx config
sudo nginx -t

echo ""
echo "⚠️  Before restarting nginx:"
echo "   1. Edit /etc/nginx/sites-available/insync"
echo "   2. Replace 'your-domain.com' with your actual domain"
echo ""
read -p "Press Enter after editing nginx config..."

sudo systemctl restart nginx

# ── 10. SSL certificate (optional) ──────────────────────────────
echo ""
read -p "Do you want to set up SSL with Let's Encrypt? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "Enter your domain (e.g., example.com): " DOMAIN
    sudo certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN"

    # Uncomment HTTPS block in nginx config
    sudo sed -i 's/# server {/server {/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# listen/listen/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# server_name/server_name/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# ssl_/ssl_/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# include/include/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# location/location/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# proxy_/proxy_/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# client_/client_/g' /etc/nginx/sites-available/insync
    sudo sed -i 's/# }/}/g' /etc/nginx/sites-available/insync

    sudo systemctl reload nginx
fi

# ── 11. Status check ────────────────────────────────────────────
echo ""
echo "✅ Deployment complete!"
echo ""
echo "📊 Service status:"
sudo systemctl status insync --no-pager -l
echo ""
echo "🔗 Your app should be running at:"
echo "   http://your-server-ip"
echo ""
echo "📝 Useful commands:"
echo "   sudo systemctl status insync    # Check service status"
echo "   sudo systemctl restart insync   # Restart application"
echo "   sudo journalctl -u insync -f    # View logs"
echo "   sudo nginx -t                   # Test nginx config"
echo ""
