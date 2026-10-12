# Aşama 1: Keşif Raporu (DISCOVERY)

## 1. VM ve OS Bilgileri
* **İşletim Sistemi**: Ubuntu 22.04 LTS (Jammy), ARM64
* **Çekirdek**: 5.15.0-131-generic
* **Sanallaştırma**: oracle (VirtualBox)
* **CPU/Bellek**: 4 vCPU, 19 GiB Bellek (Lab için son derece yeterli).

## 2. Ağ ve SCTP Durumu
* **Sysctl SCTP**: `net.sctp` ayarları varsayılan değerlerinde bulunuyor.
* **Mevcut SCTP Bağlantıları**: PyHSS ve Diameter servisleri arasında 4 adet aktif SCTP association bulunuyor (Port 3868).
* **Open5GS Portları**: NRF, AMF vb. servisleri ayakta ve ilgili SCTP/TCP portları dinleniyor.

## 3. Yazılım ve Paket Bilgileri
* **Open5GS**: `2.8.0~jammy5` (Snapshot'ta belirtilen 2.7.6 sürümüne göre daha güncel!)
* **Kamailio**: `5.5.4` (Snapshot'taki 6.2.0-dev0 yerine farklı bir sürüm bulunuyor).
* **MongoDB**: `6.0.29`
* **Geliştirme/Ağ Araçları**: `iproute2`, `libsctp-dev`, `lksctp-tools` ve `tcpdump` yüklü. 
* **Eksik Paketler**: 
  * `tshark` paketi yüklü değil (pcap analizleri için kurulması gerekecek).
  * **Önemli Sapma**: `UERANSIM` kurulumu `/opt/UERANSIM` dizininde bulunamadı! UERANSIM kaynak kodunun indirilip derlenmesi veya doğru dizinin bulunması gerekecek.

## 4. Servis Durumları
* **Aktif (Çalışan) Servisler**: Tüm `open5gs-*` servisleri (amfd, smfd, upfd, nrfd vb.), `kamailio`, `named`, `mongod`.
* **Sorunlu/İnaktif Servisler**: 
  * `pyhss`: Durumu `activating` (sürekli başlıyor veya takılı kalmış olabilir).
  * `open5gs-nwdafd`: Durumu `inactive`.

## 5. Mevcut Open5GS Yapılandırması (AMF)
* `amf.yaml` içerisinde `amf_name: open5gs-amf0` tanımlı.
* Beklendiği gibi herhangi bir **AMF Pointer (amf_id.pointer)** tanımı veya `relative_capacity` tanımı bulunmuyor.

---

## Sonuç, Öneriler ve Snapshot'tan Sapmalar

1. **Sürüm Sapmaları**: Open5GS 2.8.0 ve Kamailio 5.5.4 yüklü (Snapshot notlarıyla farklı).
2. **UERANSIM Eksikliği**: `/opt/UERANSIM` bulunamadı. Kurulum yapılmamış olabilir, 2. Aşama (Host Hazırlığı) veya 4. Aşamada bunu kurmamız gerekebilir.
3. **Eksik Araçlar**: `tshark` eksik.
4. **PyHSS Durumu**: `pyhss` servisi tam ayakta görünmüyor.

**Kaynak Yeterliliği**: 4 vCPU ve 19 GB RAM fazlasıyla yeterli.

Bir sonraki adım olan **Aşama 2 — Host hazırlığı (10_prep_host.sh)** betiğini yazmaya geçebilir miyiz? Bu aşamada `tshark` kurulumu, iptables/sysctl ayarlarının yedeklenerek idempotent hale getirilmesi ve disk/log temizliği önerilerini hazırlayacağım.
