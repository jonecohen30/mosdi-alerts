#!/usr/bin/env bash
# Install script for the alerts system on a fresh Ubuntu server. Run as root.
set -euo pipefail

REPO="https://github.com/jonecohen30/mosdi-alerts.git"
HOME_DIR=/opt/mosdi

echo ">> Installing base packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv sqlite3 >/dev/null

echo ">> Downloading the code..."
if [ -d "$HOME_DIR/app/.git" ]; then
  git -C "$HOME_DIR/app" pull -q
else
  mkdir -p "$HOME_DIR"
  git clone -q "$REPO" "$HOME_DIR/app"
fi

python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q -r "$HOME_DIR/app/requirements.txt"
mkdir -p "$HOME_DIR/data"

ask_token() {
  echo
  echo ">> Paste the Telegram bot token and press Enter (it will not be shown):"
  read -rs TOKEN < /dev/tty
  TOKEN="$(echo "$TOKEN" | tr -d '[:space:]')"
  echo "TELEGRAM_TOKEN=$TOKEN" > "$HOME_DIR/.env"
  chmod 600 "$HOME_DIR/.env"
  echo ">> Token saved."
}

grep -q '^TELEGRAM_TOKEN=' "$HOME_DIR/.env" 2>/dev/null || ask_token

cd "$HOME_DIR/app"
while true; do
  set +e
  MOSDI_HOME="$HOME_DIR" "$HOME_DIR/venv/bin/python" -m mosdi.setup_chat
  rc=$?
  set -e
  case $rc in
    0) break ;;
    3) echo ">> The token is wrong. Copy it again from BotFather."; ask_token ;;
    2) echo ">> Send a message to the bot in Telegram, then press Enter here."; read -r < /dev/tty ;;
    *) echo ">> Network problem. Press Enter to try again."; read -r < /dev/tty ;;
  esac
done

cat > /etc/systemd/system/mosdi.service <<EOF
[Unit]
Description=Mosdi memecoin alerts
After=network-online.target
Wants=network-online.target

[Service]
Environment=MOSDI_HOME=$HOME_DIR
WorkingDirectory=$HOME_DIR/app
ExecStart=$HOME_DIR/venv/bin/python -m mosdi
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable -q mosdi
systemctl restart mosdi
sleep 3
systemctl --no-pager --lines=5 status mosdi || true
echo
echo ">> DONE. A Telegram message should arrive within a few seconds."
