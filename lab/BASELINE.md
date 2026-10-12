# N2 LB Proxy - Baseline Ölçümleri (Tam Örgü / Full-Mesh)

## SCTP Bağlantı Sayıları (Aktif Durum)

|  | AMF-1 (127.0.0.5) | AMF-2 (127.0.0.105) | AMF-3 (127.0.0.205) |
|---|---|---|---|
| **gNB-A (127.0.0.11)** | Bağlı (ESTAB) | Bağlı (ESTAB) | Bağlı (ESTAB) |
| **gNB-B (127.0.0.12)** | Bağlı (ESTAB) | Bağlı (ESTAB) | Bağlı (ESTAB) |
| **gNB-C (127.0.0.13)** | Bağlı (ESTAB) | Bağlı (ESTAB) | Bağlı (ESTAB) |

*Toplam aktif SCTP assoc sayısı = 9*

## Arıza Simülasyonu (AMF-2 kill -9)
- Kesinti anında `ss -Sn` ile ölçülen aktif bağlantı: **6**
- Geri kalan bağlantılar kopmadan çalışmaya devam etmiştir.
- Bu durum proxy'nin iyileştireceği/merkezileştireceği temel davranışı tanımlamaktadır.

## Kaynak Tüketimi (Baseline)
- AMF süreçleri (3 adet): Ortalama %0.4 - %1.2 CPU, ~15MB RAM kullanımı/süreç.
- gNB süreçleri (3 adet): Ortalama %0.3 - %0.8 CPU, ~10MB RAM kullanımı/süreç.
- Sistem bu topolojiyi loopback arayüzü üzerinde rahatlıkla taşıyabilmektedir.

*(Not: Tshark Jupyter uyumsuzluğu nedeniyle pcap dosyaları kaydedilemediğinden NGAP-ID çakışma raporu, proxy geliştirme fazına ertelenmiştir.)*
