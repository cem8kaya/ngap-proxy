#!/usr/bin/env bash
# Mac tarafında bir kez: VM'e anahtar tabanlı SSH kurar (parola yalnızca sudo için kalır).
# Kullanım: VM_PASS=open5gs ./setup_ssh_key.sh   →  sonra notebook'larda: export VM_KEY=~/.ssh/n2lab_ed25519
set -euo pipefail
KEY="${VM_KEY:-$HOME/.ssh/n2lab_ed25519}"; PORT="${VM_PORT:-2222}"; USER_HOST="localadmin@127.0.0.1"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -f "$KEY"
SSHPASS="${VM_PASS:?VM_PASS ortam değişkenini verin}" sshpass -e ssh-copy-id -i "$KEY.pub" -p "$PORT" \
  -o StrictHostKeyChecking=accept-new "$USER_HOST"
ssh -i "$KEY" -o BatchMode=yes -p "$PORT" "$USER_HOST" 'echo "anahtar girişi çalışıyor: $(hostname)"'
echo "Kullanım: export VM_KEY=$KEY   (n2lab_ops.py anahtarı otomatik kullanır; sudo için VM_PASS hâlâ gerekir)"
