#!/bin/bash
# 10_prep_host.sh
# Aşama 2: Host hazırlığı. Eksik paketleri kurar, iptables/sysctl ayarlarını düzenler, yedek alır.
# Idempotent olarak tasarlanmıştır.

set -e

BACKUP_DIR="/home/localadmin/n2lab_backup_$(date +%Y%m%d_%H%M%S)"

echo "========================================"
echo "Aşama 2: Host Hazırlığı"
echo "========================================"

echo "[1/6] Yedekleme işlemi başlatılıyor ($BACKUP_DIR)..."
mkdir -p "$BACKUP_DIR"
sudo cp -r /etc/open5gs "$BACKUP_DIR/" 2>/dev/null || true
sudo cp -r /etc/kamailio* "$BACKUP_DIR/" 2>/dev/null || true
sudo cp -r /etc/systemd/system "$BACKUP_DIR/" 2>/dev/null || true
sudo cp -r /home/localadmin/UERANSIM/config "$BACKUP_DIR/ueransim_config" 2>/dev/null || true
sudo iptables-save > "$BACKUP_DIR/iptables.rules"
sysctl -a > "$BACKUP_DIR/sysctl.conf.bak" 2>/dev/null || true
echo "Yedekler $BACKUP_DIR dizinine kaydedildi."

# Geri alma betiğini oluştur
cat <<EOF > /home/localadmin/99_rollback.sh
#!/bin/bash
echo "Sistemi $BACKUP_DIR yedeğine döndürüyor..."
sudo rm -rf /etc/open5gs
sudo cp -r $BACKUP_DIR/open5gs /etc/
sudo iptables-restore < $BACKUP_DIR/iptables.rules
sudo systemctl daemon-reload
sudo rm -f /etc/sysctl.d/99-n2lab.conf
sudo sysctl -p
echo "Geri alma tamamlandı."
EOF
chmod +x /home/localadmin/99_rollback.sh
echo "Geri alma betiği (/home/localadmin/99_rollback.sh) oluşturuldu."

echo "[2/6] Paket kurulumları (tshark, tcpdump, lksctp-tools, netcat vb.)..."
sudo apt-get update
# tshark için pcap izin onayını otomatik atlamak için (non-interactive):
echo "wireshark-common wireshark-common/install-setuid boolean true" | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y tshark tcpdump lksctp-tools netcat-openbsd iperf3 iproute2

echo "[3/6] UERANSIM dizin bağlantısı (Sembolik Link)..."
if [ ! -d "/opt/UERANSIM" ] && [ -d "/home/localadmin/UERANSIM" ]; then
    echo "UERANSIM /home/localadmin/UERANSIM dizininde bulundu, /opt/UERANSIM'e linkleniyor."
    sudo ln -s /home/localadmin/UERANSIM /opt/UERANSIM
else
    echo "UERANSIM dizini zaten /opt altında veya asıl dizin bulunamadı."
fi

echo "[4/6] Sysctl: ip_forward ve rp_filter çelişkisi çözümü..."
# Yönlendirme ve ters yol filtresi için tek kalıcı yapılandırma
sudo bash -c 'cat <<EOF > /etc/sysctl.d/99-n2lab.conf
net.ipv4.ip_forward = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
EOF'
sudo sysctl -p /etc/sysctl.d/99-n2lab.conf

echo "[5/6] İptables: Yinelenen MASQUERADE kurallarını temizleme..."
echo "Yinelenen MASQUERADE kuralları temizleniyor (Sadece Open5GS UPF IP bloklarına MASQUERADE bırakılacak)..."
sudo iptables -t nat -F POSTROUTING
sudo iptables -t nat -A POSTROUTING -s 10.45.0.0/16 ! -o ogstun -j MASQUERADE
sudo iptables -t nat -A POSTROUTING -s 10.46.0.0/16 ! -o ogstun -j MASQUERADE

echo "[6/6] Disk ve Log Temizliği..."
echo "Mevcut journal boyutu:"
journalctl --disk-usage
sudo journalctl --vacuum-size=200M

echo "========================================"
echo "Host hazırlığı tamamlandı."
echo "Not: Kullanılmayan 4G EPC servislerini (mme, sgwc, sgwu, hss, pcrf) kapatmak için aşağıdaki komutları kullanabilirsiniz:"
echo "sudo systemctl stop open5gs-mmed open5gs-sgwcd open5gs-sgwud open5gs-hssd open5gs-pcrfd"
echo "sudo systemctl disable open5gs-mmed open5gs-sgwcd open5gs-sgwud open5gs-hssd open5gs-pcrfd"
echo "========================================"
