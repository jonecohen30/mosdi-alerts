#!/usr/bin/env bash
# התקנת מערכת ההזדמנויות על שרת Ubuntu חדש. מריצים כ-root.
set -euo pipefail

REPO="https://github.com/jonecohen30/mosdi-alerts.git"
HOME_DIR=/opt/mosdi

echo "== מתקין תוכנות בסיס =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv sqlite3 >/dev/null

echo "== מוריד את הקוד =="
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

if ! grep -q '^TELEGRAM_TOKEN=' "$HOME_DIR/.env" 2>/dev/null; then
  echo
  echo "== הדבק את הטוקן של בוט הטלגרם ולחץ Enter (הוא לא יוצג על המסך) =="
  read -rs TOKEN < /dev/tty
  echo "TELEGRAM_TOKEN=$TOKEN" > "$HOME_DIR/.env"
  chmod 600 "$HOME_DIR/.env"
fi

echo "== מחבר את הבוט לצ'אט שלך =="
cd "$HOME_DIR/app"
until MOSDI_HOME="$HOME_DIR" "$HOME_DIR/venv/bin/python" -m mosdi.setup_chat; do
  echo "שלח לבוט 'שלום' בטלגרם, ואז לחץ Enter כאן"
  read -r < /dev/tty
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
echo "== סיום. בעוד כמה שניות תגיע הודעה בטלגרם שהמערכת עלתה. =="
