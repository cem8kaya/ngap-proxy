# N2 LB Proxy (C++)

Şeffaf (ID yeniden yazmayan) NGAP/SCTP yük dengeleyici: gNB başına AMF havuzu, `(gNB, RAN_UE_NGAP_ID)` UE tablosu, round-robin.

| Dosya | Görev |
|---|---|
| `src/n2proxy_master.cpp` | epoll döngüsü, SCTP taşıma (PPID/stream korunur), bağlantı yaşam döngüsü, yönlendirme |
| `src/ngap.h` | APER başlık + IE gezinmesi (RAN/AMF UE ID, 5G-S-TMSI), NG Reset kurucu |
| `src/lb_core.h` | UE tablosu + round-robin (SCTP'siz, birim testli) |
| `tests/` | pycrate vektörleri (`gen_vectors.py`), birim testi, TCP-taşımalı mantık testi |

## Faz B ile gelenler
B1 `(gnb, ran)` UE anahtarı · B2 EAGAIN'e kadar drain (level-triggered) · B3 `sctp_recvmsg/sendmsg` · B4 AMF kaybında UE temizliği + UE bazında UEContextReleaseCommand (varsayılan; `N2P_ON_AMF_LOSS=reset` ile partOf NG Reset) · B5 non-blocking connect + exp. backoff + NGSetup replay · B6 SCTP heartbeat / `path_max_retrans` · B7 NGSetup yanıt birleştirme (ilk başarı iletilir, tümü reddederse tek hata) · B8 gerçek APER IE gezinmesi (eski `00 55 00` taraması kaldırıldı; ID ≥ 256 hatası giderildi).

Ayrıca: eski kodda round-robin sayacı UE'siz yedek yönlendirmelerle de ilerliyordu (baseline'da 4 UE'nin 4'ü AMF-1'e düşmesi); artık sayaç yalnızca gerçek UE seçiminde ilerler ve bilinmeyen UE mesajı rastgele AMF'e gitmek yerine loglanıp düşürülür.

## Faz C ile gelenler
C1 kapasite-ağırlıklı seçim (smooth WRR; ağırlık = NGSetupResponse'taki RelativeAMFCapacity, bilinmiyorsa 100) · C2 5G-S-TMSI affinity (sahibi AMF'i ServedGUAMIList'ten pointer/set ile bulur; sahibi hazır değilse WRR) · C4 drain/undrain/status (komut dosyası `N2P_CTL`; drain'deki AMF yeni UE almaz ama sahibi olduğu dönen UE'yi alır) · C4 uygulama düzeyi canlılık yoklaması (`N2P_PROBE_MS`, opt-in: boş RANConfigurationUpdate; `SIGSTOP`'lu AMF'i yakalar).
Not: yoklama yalnızca VM'de `probe_healthy` PASS olduktan sonra açılmalı; gerçek Open5GS'in boş RANConfigurationUpdate'e yanıtı henüz doğrulanmadı.

## Faz D ile gelenler
Prometheus metinsel metrikleri (`N2P_METRICS=127.0.0.1:9190` → `/metrics`: gNB/UE/AMF gauge'ları, atama sayaçları (wrr/affinity), mesaj, drop, HA olayları; **mesaj korunumu**: `gnb_to_proxy + amf_to_proxy + mcast_extra + injected == proxy_to_gnb + proxy_to_amf + swallowed + Σdropped` — proxy'nin hiçbir mesajı sessizce kaybetmediğinin sayaçlarla kanıtı) · bağlantı/NGSetup zaman aşımı (`N2P_SETUP_TIMEOUT_MS`, donmuş AMF'e sonsuza dek takılmayı önler) · `lab/load_gen.py`: stdlib-only sentetik gNB yük üreteci (gecikme persentilleri, kayıp, proxy CPU/RSS).

## Gözlemlerden gelen düzeltmeler (VM 2026-10-11)
- **Bayat S-TMSI öğrenme:** Kayıp sonrası yeniden kaydolan UE GUTI/S-TMSI ile gelir. Bağlamı olmayan *her* AMF (yeniden başlamış sahibi de, başka bir AMF de — Open5GS N14 bağlam aktarımı yapmıyor) bu ilk denemeyi 1-3 ms içinde `UEContextReleaseCommand` ile bıraktırır; UE ~0,25 sn sonra SUCI ile ikinci denemede kaydolur (VM'de iki kez doğrulandı: sahibi hazırken affinity ile AMF-2'ye, hazır değilken WRR ile AMF-3'e giden UE de aynı yolu izledi). Proxy, affinity ile gönderilen UE'yi AMF 1,5 sn içinde bıraktırırsa o (pointer, 5G-TMSI) çiftini `N2P_STALE_MS` (60 sn) boyunca "bayat" sayar ve aynı S-TMSI ile tekrar denemeyi WRR'ye yönlendirir. `N2P_AFFINITY=off` ile affinity kapatılabilir. Not: UE'nin kayıp→yeniden kayıt süresi ~3 sn (UE zamanlayıcısı), AMF kaybından sonra toparlanma bu sürede olur.
- **Mesaj korunumu:** NGSetup, backend'ler bağlanmadan önce gelirse "saklandı" olarak sayılır (drop değil).

## Çalıştırma ve ortam değişkenleri
```
make                 # VM'de (libsctp-dev gerekir)
make test            # yerel: birim + mantık testi (pip install pycrate)
make syntax-sctp     # libsctp-dev yokken gerçek SCTP yolunun sözdizimi kontrolü
N2P_LISTEN=127.0.0.50:38412  N2P_AMFS=ip:port,...  N2P_ON_AMF_LOSS=release|reset|silent  N2P_HB_MS=1000  N2P_PROBE_MS=0  N2P_AFFINITY=on  N2P_STALE_MS=60000  N2P_SETUP_TIMEOUT_MS=5000  N2P_METRICS=  N2P_CTL=/tmp/n2proxy.ctl
```

## Doğrulama durumu
- ✅ Yerel: 103 birim kontrolü (pycrate APER vektörleri) + 69 mantık kontrolü (sahte AMF/gNB, TCP taşıma, yük üreteci dahil). Mutasyon denemeleriyle testlerin hata yakaladığı görüldü.
- ✅ VM (gerçek SCTP, 2026-10-10/11), Faz B+C: baseline, concurrent, stop, kill9, blackhole, amf_loss_reset, reconnect, weights (kapasite 100/100/50, GUAMI pointer 0/1/2), drain, probe_healthy, sigstop_probe PASS; yoklamasız sigstop beklenen XFAIL.
  - `amf_loss_reset`: gNB `UE Context Release Command` aldı, UE kendiliğinden yeniden kaydoldu; yeniden kayıtta UERANSIM 5G-S-TMSI taşıdı (sahibi AMF düşük olduğundan WRR'ye düşüldü).
  - Gözlem: yoklama açıkken donmuş AMF'e yeniden bağlanınca backend NGSetup yanıtı beklemede kalıyordu → `N2P_SETUP_TIMEOUT_MS` ile giderildi.
- ✅ VM, Faz D (2026-10-11): `load` 3×50 @100/sn → 150/150 yanıt, gecikme p50 20 / p95 30 ms, proxy CPU 0,02 sn (~%1), RSS 1,7 MB, drop 0, dağılım ≈40/40/20, Prometheus sayaçları tutarlı.
  **Hız taraması (`load_sweep`, 3 gNB × 100 UE, 3 AMF, proxy üzerinden):**

  | toplam hız | yanıt | p95 | max gecikme | SCP `Connection timer expired` | proxy korunumu |
  |---|---|---|---|---|---|
  | 150/sn | 300/300 (%100) | 20 ms | 21 ms | 0 | ✔ |
  | 250/sn | 241/300 (%80) | 16 ms | 8,8 sn | 60 | ✔ |
  | 300/sn | 227/300 (%76) | 14 ms | 9,0 sn | 72 | ✔ |
  | 350/sn | 173/300 (%58) | 16 ms | 9,2 sn | 122 | ✔ |

  Yanıtsız UE sayısı ile `open5gs-scpd`'deki `Connection timer expired [METHOD:POST]` sayısı birebir örtüşüyor (59↔60, 73↔72, 127↔122, `load_big`: 244↔229) ve en yüksek gecikme hep ≈ 8,5–9 sn (SBI istemci zaman aşımı + işleme). Yanıtsızlar AMF'lere ve gNB'lere atamayla orantılı dağılıyor. CPU doygun değil (400/sn'de `scpd` 0,19, proxy 0,03 çekirdek). Eşik 150–250 kayıt/sn arasında. **Sonuç:** kayıp 5GC'nin ortak SBI hattında (SCP üzerinden AMF→AUSF→UDM) oluşuyor; proxy her hızda mesajları eksiksiz alıp iletiyor (korunum tam, drop 0, ~%1 CPU, 1,7 MB RSS) ve tek thread epoll yeterli. İlk `load_direct` kontrolü (160/sn, proxy'siz AMF-1: 240/240) adil değildi (5GC'ye giden toplam hız düşüktü); kontrol artık `load_sweep`'in 300/sn satırıyla aynı toplam hızda (3×100@300) koşuyor — proxy'siz de ≈%24 kayıp çıkarsa proxy kesin masumdur. Sahte NAS (kayıtsız SUPI) 5GC'de ek uyarılar üretir (AMF/AUSF/UDM/UDR "Cannot find SUPI"); bu yüzden yük testlerinden sonra daily-ops hata özeti şişer (beklenen).
- ✅ VM, `amf_loss_reset` (2026-10-11 21:34): bayat S-TMSI öğrenme uçtan uca doğrulandı — AMF-2 geri gelince UE'nin S-TMSI'si ile affinity AMF-2'ye gitti, AMF-2 1 ms'de release verdi, proxy `0xc000039f` çiftini 60 sn bayat işaretledi, UE'nin ikinci denemesi WRR ile AMF-3'e gitti ve ~240 ms'de kayıt tamamlandı.
- ✅ VM, daily-ops 6.5: pcap kayıt akışını yakaladı; tshark NGSetup'ın gNB→proxy→3 AMF çoğaltmasını, tek NGSetupResponse'un gNB'ye dönüşünü, ardından InitialUE/UplinkNAS/PDUSessionResourceSetup akışını gösteriyor (SCTP bundling: tek pakette iki UplinkNAS de doğru işlendi).
- ✅ VM, daily-ops: direct modda ve proxy modunda (6.4) 3 gNB + 4 UE PDU session; nr-cli `is-ngap-up: true`, 4 UE `PS-ACTIVE`; data-plane ping 4/4 netns → 8.8.8.8 (3/3); proxy CTL durumu üç AMF READY, `UE=4`; mesaj korunumu 66 == 66. Bölüm 6.5 pcap artık kayıt akışını içerecek şekilde yeniden yazıldı (önceki sürüm kararlı durumda yakalıyordu: yalnızca SCTP heartbeat).
- Bilinen sınırlar: yazma kuyruğu yok (EAGAIN'de mesaj düşer), tek thread, slice farkındalığı yok (C3), AMF'i hiç yanıt vermemiş UE'ler için release komutu üretilemez (NG Reset'e düşülür), yük testi sahte NAS kullanır (AMF tarafı kayıt akışını tamamlamaz).
