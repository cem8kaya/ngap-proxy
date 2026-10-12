#!/bin/bash
# N2 LB Proxy Lab - Geri Alma Betiği
# Bu betik VM üzerinde root olarak veya sshpass ile çalıştırılmalıdır.

echo "N2 LB Proxy Lab ortamı temizleniyor..."

# 1. AMF-2 ve AMF-3 servislerini durdur
echo "AMF servisleri durduruluyor..."
systemctl stop open5gs-amfd@amf2.service 2>/dev/null || true
systemctl stop open5gs-amfd@amf3.service 2>/dev/null || true
systemctl disable open5gs-amfd@amf2.service 2>/dev/null || true
systemctl disable open5gs-amfd@amf3.service 2>/dev/null || true

# 2. NetNS temizliği
echo "NetNS temizleniyor..."
ip netns del proxy-ns 2>/dev/null || true
ip netns del amf-ns 2>/dev/null || true
ip link del veth-proxy 2>/dev/null || true

# 3. Tmux session temizliği
echo "Tmux session'ları temizleniyor..."
tmux kill-session -t gnbs 2>/dev/null || true
tmux kill-session -t ues 2>/dev/null || true
tmux kill-session -t capture 2>/dev/null || true
pkill -9 -f n2proxy 2>/dev/null || true

echo "Temizlik tamamlandı. Mevcut open5gs-amfd.service (AMF-1) standart çalışmaya devam edecektir."
