#!/bin/bash
# 00_discover.sh
# Aşama 1: N2 LB Proxy hedef topolojisi için VM (Ubuntu ARM64) keşif betiği.
# Sistemde herhangi bir değişiklik yapmaz (salt okunur).

echo "========================================"
echo "Aşama 1: Keşif - Sistem ve Kaynak Analizi"
echo "========================================"

echo -e "\n--- 1. VM ve OS Bilgileri ---"
echo "CPU Çekirdek Sayısı (nproc):"
nproc
echo "Bellek Durumu (free -h):"
free -h
echo "Disk Durumu (df -h):"
df -h
echo "İşletim Sistemi (lsb_release -a):"
lsb_release -a 2>/dev/null
echo "Çekirdek Sürümü (uname -r):"
uname -r
echo "Sanallaştırma Ortamı (systemd-detect-virt):"
systemd-detect-virt 2>/dev/null || echo "Bilinmiyor"

echo -e "\n--- 2. Ağ Bilgileri ---"
echo "Arayüzler ve IP Adresleri (ip -br a):"
ip -br a
echo "Yönlendirme Tablosu (ip r):"
ip r
echo "Dinleyen Portlar (ss -tulpn):"
echo "open5gs" | sudo -S ss -tulpn 2>/dev/null || ss -tulpn 2>/dev/null
echo "Yüklü Çekirdek Modülleri (sctp|gtp5g):"
lsmod | grep -E 'sctp|gtp5g'
echo "SCTP Sysctl Parametreleri (sysctl net.sctp):"
sysctl net.sctp 2>/dev/null
echo "SCTP Bağlantıları (/proc/net/sctp/assocs):"
cat /proc/net/sctp/assocs 2>/dev/null || echo "SCTP assocs dosyası bulunamadı."
echo "SCTP Uç Noktaları (/proc/net/sctp/eps):"
cat /proc/net/sctp/eps 2>/dev/null || echo "SCTP eps dosyası bulunamadı."

echo -e "\n--- 3. Yazılım ve Paket Bilgileri ---"
echo "Open5GS Paketleri:"
dpkg -l | grep open5gs || echo "Bulunamadı"
echo "Kamailio Sürümü:"
/usr/local/sbin/kamailio -v 2>/dev/null || kamailio -v 2>/dev/null || echo "Bulunamadı"
echo "MongoDB Sürümü:"
mongod --version 2>/dev/null || echo "Bulunamadı"
echo "UERANSIM Yolu (/opt/UERANSIM):"
ls -la /opt/UERANSIM 2>/dev/null || echo "Bulunamadı"
echo "UERANSIM nr-gnb Sürümü:"
/opt/UERANSIM/build/nr-gnb --version 2>/dev/null || echo "Bulunamadı"
echo "Derleme Araçları (g++, cmake):"
g++ --version | head -n 1
cmake --version | head -n 1
echo "Ağ ve Geliştirme Araçları (libsctp-dev, lksctp-tools, tshark, tcpdump, iproute2):"
dpkg -l | grep -E 'libsctp-dev|lksctp-tools|tshark|tcpdump|iproute2' || echo "Bazı paketler eksik olabilir."

echo -e "\n--- 4. Servis Durumları (systemctl is-active) ---"
echo "Open5GS Servisleri:"
for svc in $(systemctl list-units --type=service --all | grep open5gs | awk '{print $1}'); do
    echo -n "$svc: "
    systemctl is-active $svc 2>/dev/null || echo "unknown/inactive"
done
echo -n "kamailio: "; systemctl is-active kamailio 2>/dev/null || echo "unknown/inactive"
echo -n "pyhss: "; systemctl is-active pyhss 2>/dev/null || echo "unknown/inactive"
echo -n "named: "; systemctl is-active named 2>/dev/null || echo "unknown/inactive"
echo -n "mongod: "; systemctl is-active mongod 2>/dev/null || echo "unknown/inactive"
echo -n "open5gs-nwdafd: "; systemctl is-active open5gs-nwdafd 2>/dev/null || echo "unknown/inactive"

echo -e "\n--- 5. Mevcut Yapılandırma Dosyaları (Özet) ---"
echo "amf.yaml İçeriği (Yalnızca ngap, sbi ve pointer bilgileri):"
echo "open5gs" | sudo -S grep -E 'ngap:|sbi:|amf_name:|pointer|capacity' -A 1 -B 1 /etc/open5gs/amf.yaml 2>/dev/null || echo "Okunamadı"

echo -e "\n========================================"
echo "Keşif Betiği Tamamlandı."
echo "========================================"
