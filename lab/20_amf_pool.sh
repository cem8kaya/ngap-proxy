#!/bin/bash
# 20_amf_pool.sh
# Aşama 3: AMF Havuzu Kurulumu
# 1 mevcut AMF'i koruyup kapasite/pointer ekler; 2 yeni AMF örneği (şablon servis ile) başlatır.

set -e

echo "========================================"
echo "Aşama 3: AMF Havuzu ve Şablon Yapılandırması"
echo "========================================"

# Şablon Unit Dosyasının Oluşturulması
echo "[1/3] open5gs-amfd@.service şablonu oluşturuluyor..."
cat <<'EOF' > /tmp/open5gs-amfd@.service
[Unit]
Description=Open5GS AMF Daemon (%i)
After=network.target

[Service]
Type=simple
User=open5gs
ExecStart=/usr/bin/open5gs-amfd -c /etc/open5gs/%i.yaml
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/open5gs-amfd@.service /etc/systemd/system/open5gs-amfd@.service
sudo systemctl daemon-reload

echo "[2/3] AMF yapılandırmaları oluşturuluyor (amf.yaml, amf2.yaml, amf3.yaml)..."

# AMF-1 (Mevcut amf.yaml güncelleniyor)
cat <<'EOF' > /tmp/amf.yaml
amf:
  amf_name: open5gs-amf0
  relative_capacity: 100
  fallback:
    smf: true
  guami:
  - amf_id:
      region: 2
      set: 1
      pointer: 0
    plmn_id:
      mcc: 999
      mnc: 70
  metrics:
    server:
    - address: 127.0.0.5
      port: 9090
  network_name:
    full: Open5GS
    short: Next
  ngap:
    server:
    - address: 127.0.0.5
  plmn_support:
  - plmn_id:
      mcc: 999
      mnc: 70
    s_nssai:
    - sst: 1
    - sst: 2
    - sst: 3
    - sd: '000001'
      sst: 1
  sbi:
    client:
      scp:
      - uri: http://127.0.0.200:7777
    server:
    - address: 127.0.0.5
      port: 7777
  security:
    ciphering_order:
    - NEA2
    - NEA1
    - NEA0
    integrity_order:
    - NIA2
    - NIA1
    - NIA0
  t3502: 720
  t3512: 3240
  tai:
  - plmn_id:
      mcc: 999
      mnc: 70
    tac: 1
  time:
    t3512:
      value: 540
global:
  max:
    ue: 1024
    gnb: 10
logger:
  domain: core,ogs
  file:
    path: /var/log/open5gs/amf.log
  level: info
EOF
sudo mv /tmp/amf.yaml /etc/open5gs/amf.yaml

# AMF-2
cat <<'EOF' > /tmp/amf2.yaml
amf:
  amf_name: open5gs-amf1
  relative_capacity: 100
  fallback:
    smf: true
  guami:
  - amf_id:
      region: 2
      set: 1
      pointer: 1
    plmn_id:
      mcc: 999
      mnc: 70
  metrics:
    server:
    - address: 127.0.0.105
      port: 9090
  network_name:
    full: Open5GS
    short: Next
  ngap:
    server:
    - address: 127.0.0.105
  plmn_support:
  - plmn_id:
      mcc: 999
      mnc: 70
    s_nssai:
    - sst: 1
    - sst: 2
    - sst: 3
    - sd: '000001'
      sst: 1
  sbi:
    client:
      scp:
      - uri: http://127.0.0.200:7777
    server:
    - address: 127.0.0.105
      port: 7777
  security:
    ciphering_order:
    - NEA2
    - NEA1
    - NEA0
    integrity_order:
    - NIA2
    - NIA1
    - NIA0
  t3502: 720
  t3512: 3240
  tai:
  - plmn_id:
      mcc: 999
      mnc: 70
    tac: 1
  time:
    t3512:
      value: 540
global:
  max:
    ue: 1024
    gnb: 10
logger:
  domain: core,ogs
  file:
    path: /var/log/open5gs/amf2.log
  level: info
EOF
sudo mv /tmp/amf2.yaml /etc/open5gs/amf2.yaml

# AMF-3
cat <<'EOF' > /tmp/amf3.yaml
amf:
  amf_name: open5gs-amf2
  relative_capacity: 50
  fallback:
    smf: true
  guami:
  - amf_id:
      region: 2
      set: 1
      pointer: 2
    plmn_id:
      mcc: 999
      mnc: 70
  metrics:
    server:
    - address: 127.0.0.205
      port: 9090
  network_name:
    full: Open5GS
    short: Next
  ngap:
    server:
    - address: 127.0.0.205
  plmn_support:
  - plmn_id:
      mcc: 999
      mnc: 70
    s_nssai:
    - sst: 1
    - sst: 2
    - sst: 3
    - sd: '000001'
      sst: 1
  sbi:
    client:
      scp:
      - uri: http://127.0.0.200:7777
    server:
    - address: 127.0.0.205
      port: 7777
  security:
    ciphering_order:
    - NEA2
    - NEA1
    - NEA0
    integrity_order:
    - NIA2
    - NIA1
    - NIA0
  t3502: 720
  t3512: 3240
  tai:
  - plmn_id:
      mcc: 999
      mnc: 70
    tac: 1
  time:
    t3512:
      value: 540
global:
  max:
    ue: 1024
    gnb: 10
logger:
  domain: core,ogs
  file:
    path: /var/log/open5gs/amf3.log
  level: info
EOF
sudo mv /tmp/amf3.yaml /etc/open5gs/amf3.yaml

sudo chown open5gs:open5gs /etc/open5gs/amf.yaml /etc/open5gs/amf2.yaml /etc/open5gs/amf3.yaml

echo "[3/3] Servisler başlatılıyor..."
sudo systemctl stop open5gs-amfd || true
sudo systemctl disable open5gs-amfd || true
sudo systemctl enable open5gs-amfd@amf.service open5gs-amfd@amf2.service open5gs-amfd@amf3.service
sudo systemctl restart open5gs-amfd@amf.service open5gs-amfd@amf2.service open5gs-amfd@amf3.service

echo "AMF servisleri başlatıldı. 5 saniye bekleniyor..."
sleep 5

echo "========================================"
echo "Doğrulama Adımları:"
echo "1. NRF'te kayıtlı AMF'ler:"
curl -s --http2-prior-knowledge http://127.0.0.10:7777/nnrf-nfm/v1/nf-instances?nf-type=AMF | grep -E '"amfName"|"pointer"' || echo "NRF'ten veri okunamadı."
echo "2. SCTP Dinleyicileri (NGAP - 38412):"
ss -S | grep 38412 || true
echo "3. Metrik Dinleyicileri (9090):"
sudo ss -tlnp | grep 9090 || true
echo "========================================"
