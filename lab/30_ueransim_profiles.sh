#!/bin/bash
# 30_ueransim_profiles.sh
# Aşama 4: RAN ve UE Profillerinin Hazırlanması
# gNB'ler ve UE'ler için gerekli YAML dosyalarını oluşturur, Aboneleri MongoDB'ye kaydeder.

set -e

echo "========================================"
echo "Aşama 4: RAN ve UE Profillerinin Oluşturulması"
echo "========================================"

CONF_DIR="/opt/UERANSIM/config/n2lab"
sudo mkdir -p $CONF_DIR

echo "[1/4] gNB (RAN) profilleri oluşturuluyor..."
# gNB-A
sudo bash -c "cat > $CONF_DIR/gnb-a.yaml <<EOF
mcc: '999'
mnc: '70'
nci: '0x10'
idLength: 32
tac: 1
linkIp: 127.0.0.11
ngapIp: 127.0.0.11
gtpIp: 127.0.0.11
amfConfigs:
  - address: 127.0.0.5
    port: 38412
slices:
  - sst: 1
  - sst: 1
    sd: 1
  - sst: 2
  - sst: 3
ignoreStreamIds: true
EOF"

# gNB-B
sudo bash -c "cat > $CONF_DIR/gnb-b.yaml <<EOF
mcc: '999'
mnc: '70'
nci: '0x11'
idLength: 32
tac: 1
linkIp: 127.0.0.12
ngapIp: 127.0.0.12
gtpIp: 127.0.0.12
amfConfigs:
  - address: 127.0.0.5
    port: 38412
slices:
  - sst: 1
  - sst: 1
    sd: 1
  - sst: 2
  - sst: 3
ignoreStreamIds: true
EOF"

# gNB-C
sudo bash -c "cat > $CONF_DIR/gnb-c.yaml <<EOF
mcc: '999'
mnc: '70'
nci: '0x12'
idLength: 32
tac: 1
linkIp: 127.0.0.13
ngapIp: 127.0.0.13
gtpIp: 127.0.0.13
amfConfigs:
  - address: 127.0.0.5
    port: 38412
slices:
  - sst: 1
  - sst: 1
    sd: 1
  - sst: 2
  - sst: 3
ignoreStreamIds: true
EOF"

echo "[2/4] UE profilleri oluşturuluyor..."
# UE - SST 1
sudo bash -c "cat > $CONF_DIR/ue-s1.yaml <<EOF
supi: 'imsi-999700000000101'
mcc: '999'
mnc: '70'
key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEBA952E4283B54E88E6183CA'
opType: 'OPC'
amf: '8000'
imei: '356938035643803'
imeiSv: '4370816125816151'
gnbSearchList:
  - 127.0.0.11
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: 1
EOF"

# UE - SST 1, SD 000001
sudo bash -c "cat > $CONF_DIR/ue-s1sd1.yaml <<EOF
supi: 'imsi-999700000000102'
mcc: '999'
mnc: '70'
key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEBA952E4283B54E88E6183CA'
opType: 'OPC'
amf: '8000'
imei: '356938035643804'
imeiSv: '4370816125816152'
gnbSearchList:
  - 127.0.0.12
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: 1
      sd: 1
EOF"

# UE - SST 2
sudo bash -c "cat > $CONF_DIR/ue-s2.yaml <<EOF
supi: 'imsi-999700000000103'
mcc: '999'
mnc: '70'
key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEBA952E4283B54E88E6183CA'
opType: 'OPC'
amf: '8000'
imei: '356938035643805'
imeiSv: '4370816125816153'
gnbSearchList:
  - 127.0.0.13
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'urllc'
    slice:
      sst: 2
EOF"

# UE - SST 3
sudo bash -c "cat > $CONF_DIR/ue-s3.yaml <<EOF
supi: 'imsi-999700000000104'
mcc: '999'
mnc: '70'
key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEBA952E4283B54E88E6183CA'
opType: 'OPC'
amf: '8000'
imei: '356938035643806'
imeiSv: '4370816125816154'
gnbSearchList:
  - 127.0.0.11
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'iot'
    slice:
      sst: 3
EOF"

echo "[3/4] Abonelerin MongoDB'ye eklenmesi..."
cat << 'EOF' > /tmp/add_subscribers.js
const db = connect("mongodb://localhost:27017/open5gs");

function addOrUpdateSub(imsi, sst, sd, apn) {
  const existing = db.subscribers.findOne({ imsi: imsi });
  const sub = {
    imsi: imsi,
    security: {
      k: "465B5CE8B199B49FAA5F0A2EE238A6BC",
      amf: "8000",
      op: null,
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
    },
    ambr: { downlink: { value: 1, unit: 3 }, uplink: { value: 1, unit: 3 } },
    slice: [
      {
        sst: sst,
        sd: sd,
        default_indicator: true,
        session: [
          {
            name: apn,
            type: 1,
            pcc_rule: [],
            ambr: { downlink: { value: 1, unit: 3 }, uplink: { value: 1, unit: 3 } },
            qos: { index: 9, arp: { priority_level: 8, pre_emption_capability: 1, pre_emption_vulnerability: 1 } }
          }
        ]
      }
    ]
  };

  if (existing) {
    db.subscribers.updateOne({ imsi: imsi }, { $set: sub });
    print("Guncellendi: " + imsi);
  } else {
    db.subscribers.insertOne(sub);
    print("Eklendi: " + imsi);
  }
}

addOrUpdateSub("999700000000101", 1, "000000", "internet");
addOrUpdateSub("999700000000102", 1, "000001", "internet");
addOrUpdateSub("999700000000103", 2, "000000", "urllc");
addOrUpdateSub("999700000000104", 3, "000000", "iot");
EOF
mongosh --quiet /tmp/add_subscribers.js

echo "[4/4] NCI ve Profil Çakışma Kontrolü..."
echo "========================================================="
echo "DİKKAT: Eski profillerin mevcut NCI değerleri (Aynı olmamalı):"
grep -E "nci:|gnbId" /opt/UERANSIM/config/*-gnb.yaml 2>/dev/null || echo "Eski profil bulunamadı."
echo "========================================================="
echo ""
echo "!!! BİLGİLENDİRME (K5 ve K7 Zafiyetleri) !!!"
echo "NSSF yapılandırması şu anda yalnızca SST1'i desteklemektedir (K5 Açığı)."
echo "SMF yapılandırmasında 'urllc' ve 'iot' DNN'leri tanımlı değildir (K7 Açığı)."
echo "Bu nedenle, SST2 ve SST3 (urllc, iot) test UE'leri PDU oturumu kuramayacak,"
echo "fakat Registration (Kayıt) aşamasını tamamlayabileceklerdir."
echo "========================================================="
