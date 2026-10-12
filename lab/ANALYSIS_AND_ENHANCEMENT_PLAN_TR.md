# N2 LB Proxy — Mevcut Durum Analizi ve Enhancement Planı

> Kapsam: `00_N2_Master_Proxy_Lab.ipynb` (proxy mantığı + chaos testi) ve çalıştığı ortam `local_vm_daily_ops_v1.ipynb` (VM operasyon katmanı).
> Yöntem: Notebook kaynak kodu + kayıtlı çıktılar + `BASELINE/DISCOVERY/TOPOLOGY/PROJECT_SUMMARY_AND_ROADMAP.md` + ön rapor birlikte okundu. VM'e canlı erişim yok; "kanıt" dediğim şey notebook çıktılarıdır, kod hatalarının bir kısmı kod okunarak çıkarılmıştır (canlı doğrulama gerektirir, ilgili maddede belirtildi).

---

## 1. Özet (TL;DR)

| Konu | Değerlendirme |
|---|---|
| Ortam (daily ops) | **Olgun.** İdempotent onarım hücreleri, netns+köprü ile UE izolasyonu, slice/SMF uyumu, yedek, korumalı rollback. Direct modda 3 gNB + 4 UE + PDU session çalışıyor (10.45/46/47.x). |
| Master proxy | **Kavram kanıtı (PoC) olarak başarılı:** gNB başına 3 AMF'e SCTP, NGSetup multicast, InitialUEMessage'da RR seçimi, AMF kopunca havuzdan çıkarma gözlemlendi. |
| Proxy'nin üretime/çoklu-gNB'ye hazırlığı | **Hazır değil.** Çoklu gNB'de UE bağlam çakışması, edge-triggered epoll'da tek `recv`, AMF geri dönüşü yok, eski UE'ler için bayat fd, PPID/stream kaybı gibi yapısal sorunlar var. |
| Test kalitesi | **Zayıf.** Chaos testi sonucu yalnızca proxy logundan çıkarılıyor; UE kaydı doğrulanmıyor, 3 UE'nin 1'i logda hiç yok. |
| Dokümantasyon | Roadmap tablosu eskimiş (Faz 8-10'un bir kısmı master'da zaten var); iki notebook birbirine bağlı değil. |

**En önemli 5 aksiyon:** (1) UE tablosunu `(gnb, ran_id)` anahtarına taşı, (2) `recv` döngüsünü EAGAIN'e kadar boşalt + `sctp_recvmsg/sendmsg` ile PPID/stream koru, (3) AMF kopunca bayat UE'leri temizle + NG Reset/UE release ile gNB'ye bildir, (4) AMF yeniden bağlanma (non-blocking connect + NGSetup replay), (5) C++ kaynağını notebook içinden çıkarıp repoya `.cpp` olarak al, assert'li otomatik test ekle.

---

## 2. Master notebook'ta yapılanlar

### 2.1 Mimari (kodun gerçekte yaptığı)

```
gNB ──SCTP──► Proxy 127.0.0.50:38412 ──┬─ SCTP ─► AMF-1 127.0.0.5     (gNB başına ayrı assoc)
 (1 assoc)       epoll, tek thread      ├─ SCTP ─► AMF-2 127.0.0.105
                                        └─ SCTP ─► AMF-3 127.0.0.205
```

- **Şeffaf (transparent) model:** gNB × AMF başına bir arka uç association; NGAP ID yeniden yazma yok. Ön rapordaki "Faz 1" önerisiyle uyumlu.
- **NGSetupRequest (procCode 21):** tüm sağlıklı AMF'lere çoğaltılıyor; yalnızca ilk `SuccessfulOutcome/21` gNB'ye iletiliyor (`ng_setup_resp_sent` bayrağı).
- **InitialUEMessage (procCode 15):** gövdede `00 55 00` (IE id 85 = RAN_UE_NGAP_ID) baytları aranıp ID çıkarılıyor; global RR sayacı ile sağlıklı AMF seçiliyor; `ue_contexts_by_ran[ran_id]` yazılıyor.
- **Sonraki uplink:** aynı sezgiyle RAN ID bulunup kayıtlı AMF'e; bulunamazsa ilk sağlıklı AMF'e (RR).
- **Downlink:** AMF → gNB'ye koşulsuz iletim (NGSetupResponse tekilleştirme hariç).
- **HA:** `recv()<=0` → o AMF fd'si `-1` yapılıyor, havuzdan çıkıyor.

### 2.2 Test akışı ve kanıtlar

Akış: temizlik → derle/başlat → gNB-A → UE-1 → `systemctl stop amf2` → UE-2, UE-3.

Kayıtlı proxy logu:
```
AMF-1/2/3 BAĞLANDI
[LB] YENI UE (RAN_ID=1) -> AMF-1
[ALARM] AMF koptu (FD=7)  /  [HA] AMF-2 pasife alindi
[LB] YENI UE (RAN_ID=2) -> AMF-3
```
**Kanıtlanan:** çoklu arka uç bağlantısı, RR seçimi, AMF kopmasının algılanması, kopan AMF'in atlanması.

**Kanıtlanmayan / şüpheli:**
1. **3 UE başlatıldı, logda 2 `[LB]` satırı var.** Daily-ops hücre 13'e göre `ue-s2` → gNB-C (10.201.0.13), `ue-s1` ve `ue-s3` → gNB-A. Test yalnızca gNB-A'yı başlatıyor, dolayısıyla **ns-s2'deki "UE-2" büyük olasılıkla hiç attach olmadı**; logdaki "RAN_ID=2" aslında `ue-s3`. Notebook'taki "UE-2/UE-3 → AMF-1/AMF-3" anlatısı bu nedenle yanlış etiketli. (Canlı log ile doğrulanmalı.)
2. gNB ve UE çıktıları `/dev/null`'a gidiyor → **Registration/PDU Session başarısı hiç kontrol edilmiyor**. Proxy "yönlendirdi" demesi UE'nin kaydolduğunu göstermez.
3. Arıza `systemctl stop` (SIGTERM → düzgün SCTP SHUTDOWN) ile simüle ediliyor. `kill -9`/ABORT ve özellikle **sessiz kopma** (ağ blackhole, AMF donması) test edilmiyor; `recv()<=0` yalnızca ilk iki durumda tetiklenir.
4. Test sonunda `start amf2` yapılıyor ama proxy AMF-2'ye geri bağlanmıyor → sonraki koşu bozuk havuzla başlar (UE'ler yine yalnızca AMF-1/3'e gider).
5. Sabit `sleep`'ler, `cat log` — assertion yok.

---

## 3. Master proxy kodunda tespit edilen sorunlar

Önem: 🔴 yüksek (çoklu-gNB/HA'yı bozar) · 🟠 orta · 🟡 düşük. Hepsi kod okumasından; 🔴 olanlar canlı repro ile teyit edilmeli.

| # | Sev. | Sorun | Etki / Neden | Düzeltme |
|---|---|---|---|---|
| 1 | 🔴 | `ue_contexts_by_ran` **global** ve anahtarı yalnızca `RAN_UE_NGAP_ID`. UERANSIM her gNB'de ID'leri 1'den başlatır. | gNB-A ve gNB-B'de RAN_ID=1 çakışır; bir UE'nin kaydı diğerini ezer → yanlış AMF'e yönlendirme. Test yalnızca tek gNB'yle yapıldığı için görünmedi. | Anahtar `(gnb_fd, ran_id)`; ters indeks `(amf_fd, amf_ue_id)`. |
| 2 | 🔴 | `EPOLLET` + olay başına **tek** `recv()`. | Tek olayda birden çok mesaj/sıralı mesaj geldiyse kalanı bir sonraki olaya kadar okunmaz (ET yeniden tetiklemez) → takılma/gecikme. Yük altında görünür. | `while(recv>0)` EAGAIN'e kadar; veya LT moda dön. |
| 3 | 🔴 | AMF koptuğunda o AMF'e ait UE kayıtları silinmiyor; `amf_fd` kapatılmış fd'yi tutuyor. | OS fd numarasını yeni bir soket için yeniden kullanabilir → **UE trafiği yanlış sokete gider** (fd reuse). gNB'ye hiçbir bildirim yok, UE sessizce takılır. | AMF kopunca ilgili UE'leri listele; gNB'ye `NG RESET` (AMF-initiated) veya UE başına `UEContextReleaseCommand` gönder; tabloyu temizle. |
| 4 | 🔴 | AMF geri geldiğinde **yeniden bağlanma yok**; `-1` kalıcı. | `start amf2` sonrası havuz asla toparlanmaz; kapasite sürekli erir. | Non-blocking `connect` + zamanlayıcı (exp. backoff), bağlanınca o gNB için NGSetup'ı **tekrar oynat** (saklanan ilk NGSetupRequest) ve cevabını yut. |
| 5 | 🔴 | `connect()` accept yolunda **bloklayıcı**, tek thread. | Bir AMF yanıt vermezse (blackhole) tüm event loop (diğer gNB'ler dahil) kilitlenir. | Non-blocking connect + `EPOLLOUT` ile tamamlama. |
| 6 | 🟠 | `send()`/`recv()` kullanılıyor; **PPID (60) ve stream id kayboluyor**. | Open5GS tolere ediyor, ama standart gNB/AMF'ler PPID=60 ve UE-başı stream (≥1) bekleyebilir. Tek stream'e indirgeme HOL blocking'i artırır. | `sctp_recvmsg/sctp_sendmsg`, `sinfo_ppid`/`sinfo_stream` aynen taşı. |
| 7 | 🟠 | Parser sezgisel: ilk 60 baytta `00 55 00` arama; `ran_id > 0` şartı. | NGAP APER'de ID uzunluğu/ofseti değişken; yanlış pozitif ihtimali (NAS-PDU içinde rastlantı) ve **RAN_UE_NGAP_ID=0 geçerli bir değer** ama reddediliyor. AMF_UE_NGAP_ID (IE 10) hiç izlenmiyor. | Faz 8: gerçek APER decode (asn1c veya Open5GS `lib/asn1c/ngap`); IE 10/85/121(5G-S-TMSI)/ 171 çıkar. |
| 8 | 🟠 | LB yalnızca **sade RR**; `relative_capacity` (AMF-3 = 50) yok sayılıyor. RR sayacı sağlıklı sayısı değişince kayıyor ve `ran_id==0` iken de artıyor. | AMF-3'e %33 yük gider (hedef %20). Dağılım dengesiz/öngörülemez. | Ağırlıklı (smooth WRR) veya `capacity` NGSetupResponse'taki RelativeAMFCapacity'den alınmalı. |
| 9 | 🟠 | **5G-S-TMSI / GUAMI affinity yok.** Mevcut UE yeniden kaydolurken InitialUEMessage RR ile başka AMF'e gider. | GUTI sahibi AMF'te bağlam varken başka AMF'e düşmek → Identity Request, bağlam kaybı. Production LB'nin en kritik kuralı. | InitialUEMessage'dan 5G-S-TMSI (AMF Set ID + Pointer) → pointer eşlemesi; bilinmiyorsa RR. |
| 10 | 🟠 | `ue_contexts_by_ran` hiç silinmiyor (gNB kopması, `UEContextRelease` procCode 41 dahil). | Bellek sızıntısı + eski kayıtların yeni UE'lerle çakışması. | Release tamamlanınca ve gNB kopunca sil. |
| 11 | 🟠 | Yalnızca `pdu_type==1 && code==21` AMF cevabı tekilleştiriliyor; **NGSetupFailure (pdu 2)** ve `AMFConfigurationUpdate`, `NG Reset`, `ErrorIndication` her AMF'ten gNB'ye geçiyor. | Bir AMF failure dönerse gNB yanlış sonuç görür; çoklu AMF'ten gelen duplicate config update gNB'yi şaşırtır. | Setup sonucunu birleştir: ≥1 başarı → başarı; hepsi hata → tek hata. Yönetim mesajlarını tekilleştir/birleştir. |
| 12 | 🟡 | `epoll_ctl(DEL)` `close()`'dan **sonra**; gNB kopunca `ue_contexts` ve AMF `amf_to_gnb` tam temizlenmiyor. | Teknik olarak close zaten siler ama bayat durum kalıyor. | Sıra: DEL → close → map temizliği. |
| 13 | 🟡 | `std::endl` her logda flush; log seviyesi/zaman damgası yok. | Yük altında I/O maliyeti, korelasyon zor. | `\n` + seviye + `clock_gettime` zaman damgası. |
| 14 | 🟡 | Sabit `MAX_EVENTS 20`, `buffer[4096]` tek `recv`. | NGAP mesajı > 4 KB ise bölünür (nadir ama olası). | Büyük tampon + SCTP `MSG_EOR` kontrolü. |
| 15 | 🟡 | Hard-coded IP/port; konfig dosyası/CLI yok. | Her değişiklikte notebook'ta heredoc düzenlemek gerekiyor. | YAML/INI konfig. |

> İlgili olarak ön raporun (§1.1) uyardığı **AMF-UE-NGAP-ID çakışması** bu şeffaf modelde proxy'nin sorunu değil (gNB başına ayrı assoc) — ancak #1 çoklu gNB'de aynı problemin RAN tarafı versiyonudur.

---

## 4. Ortam (`local_vm_daily_ops_v1`) analizi

### 4.1 Güçlü yönler
- **Bootstrap:** `ControlMaster` ile tek SSH bağlantısı, auth retry, tek `ssh_script()` yardımcısı.
- **İdempotent onarımlar:** UE profil eksikleri (5.0), slice/SMF/abone uyumu (5.0b, `cp -n` yedekli), UE başına netns + `br-ue` köprüsü (5.0c — UPF hairpin/yerel adres sorununun doğru çözümü).
- **Güvenlik ağları:** `CONFIRM`/`RUN` bayrakları, `99_rollback.sh`, 11. bölümde config+mongo+iptables yedeği, 10.1 otomatik teşhis.
- **Kanıt:** direct modda 3 gNB NG Setup başarılı, 4 UE PDU session (`10.45.0.2/.3`, `10.46.0.2`, `10.47.0.2`).

### 4.2 Eksikler ve tutarsızlıklar

| # | Sev. | Bulgu |
|---|---|---|
| E1 | 🔴 | **Master proxy daily-ops'a entegre değil.** Bölüm 6 hâlâ `proxy_advanced.cpp` (Faz 7, yalnız AMF-1) derliyor/çalıştırıyor; gerçek LB `n2proxy_master`, ayrı notebook'ta. `MODE="proxy"` yalnız gNB-A için hazır profil var (gnb-b/c-proxy.yaml yok). |
| E2 | 🟠 | **Bölüm 4.2 ölçümü anlamsız:** gNB'ler çalışmıyorken `ss -Sn \| grep -c 38412` = 0 yazdı ("kalan NGAP assoc: 0"); baseline'daki 9→6 karşılaştırması üretilemiyor. `kill -9` sonrası systemd `Restart=` AMF'i 3 sn içinde geri getirdiği için "active" görünüyor. Test, gNB'ler bağlıyken ve restart gecikmesi bilinerek yapılmalı. |
| E3 | 🟠 | **5.3 (nr-cli/ping) hücresi 5.2 (durdur) sonrasında çalıştırılınca tamamı ❌** — çıktıda gNB/UE listesi boş. Hücre sırası bağımlılığı yanıltıcı sonuç üretiyor; ön koşul kontrolü (`pgrep nr-ue`) yok. |
| E4 | 🟠 | **Master notebook ham `sshpass` heredoc'u kullanıyor**, daily-ops'taki `ssh_script` yardımcısını değil. Sonuç: her hücrede ~25 satır MOTD gürültüsü ("Welcome to Ubuntu…"), parola kaynakta, `-t` uyarıları. |
| E5 | 🟠 | **`pkill -f` tuzağı:** master çıktısında `Killed | sudo -S pkill -9 -f ...` mesajları var — `-f` kalıbı `sudo`/`bash` komut satırının kendisini de eşleştiriyor, boru hattı kendini öldürüyor. Çalışması şansa bağlı. Daily-ops bunu `pkill -x` / `^/home/...` ile doğru yapmış; master'a da taşınmalı. |
| E6 | 🟡 | Yedek (11) `proxy_advanced.cpp/proxy.cpp` alıyor, **`n2proxy_master.cpp`'yi almıyor**; yedek rotasyonu yok, mongodump sessizce yutuluyor. |
| E7 | 🟡 | Sürüm drift'i: notebook başlığı Open5GS 2.7.6, DISCOVERY 2.8.0; ön rapor GCP/x86/kernel 6.8, VM gerçekte **aarch64 / 5.15 / VirtualBox**. ARM64'te `char` işaretsizdir — NGAP bayt işleme kodu (`buffer[1]`) x86'ya taşınırsa farklı davranabilir (açık `uint8_t` kullanılmalı). |
| E8 | 🟡 | Hata özeti (7): `seppd` 48, `hssd` 8, `pcfd/pcrfd/udrd` 6, `amfd@amf2` 1. SEPP2 yokluğu zararsız denmiş ama diğerleri (`hssd`, `pcfd`) triyaj edilmemiş; test sinyalini kirletir. |
| E9 | 🟡 | `tshark` kurulu değil → 6.4 pcap yalnızca paket sayısı veriyor; Faz 8 için NGAP decode girdisi yok. |
| E10 | 🟡 | Roadmap tablosu (12) eskimiş: master'da Faz 8 (sezgisel parse), 9 (RR), 10 (AMF failover, kısmen) zaten var. `TOPOLOGY.md` hâlâ `10.200.0.x`/netns tasarımını, gerçek yapı `127.0.0.50` + `br-ue`'yi gösteriyor. `amf_name` ↔ AMF-1/2/3 isim kayması da belgeli ama çözülmemiş. |
| E11 | 🟡 | Güvenlik hijyeni: parola `open5gs` kod içinde, `StrictHostKeyChecking=no`, `echo pw \| sudo -S`. Lab için kabul edilebilir ama anahtar tabanlı SSH + `NOPASSWD` sudoers (yalnız gerekli komutlar) ile kolayca iyileşir. |
| E12 | 🟡 | C++ kaynağı yalnızca notebook heredoc'unda (+ generator script'te) yaşıyor: diff/CI/unit test/IDE yok; generator ile commit'li `.ipynb` ayrışabilir. |

---

## 5. Hedef durum (öneri)

```
repo: N2_LB_Proxy/
  proxy/                       ← YENİ: gerçek kaynak
    src/{main,epoll_loop,sctp_io,ngap_peek,amf_pool,ue_table,lb,metrics}.cpp
    config/n2proxy.yaml        (listen, amf[]{ip,weight,pointer}, timeouts)
    tests/{ngap_peek_test,lb_test}.cpp   (pcap'ten çıkarılan örnek PDU'larla)
    Makefile / CMakeLists.txt
  lab/
    local_vm_daily_ops_v2.ipynb  ← proxy bölümü master'ı kullanır (tek notebook)
    tests/chaos_suite.py         ← assert'li senaryolar
```

Prensipler: **tek proxy kaynağı**, notebook yalnızca "sync → build → run → assert" yapar; her senaryo PASS/FAIL döner; çıktıda MOTD yok.

---

## 6. Enhancement planı

### Faz A — Stabilizasyon & güvenilir test (1–2 gün) · *önce bunu yap, sonrası ona dayanır*
| Görev | Kabul ölçütü |
|---|---|
| A1. Master `.cpp`'yi repoya al (`proxy/`), notebook'u `scp` + `make` ile besle | `git diff` ile proxy değişikliği izlenebilir |
| A2. `ssh_script` yardımcısını master notebook'a taşı; `LogLevel=ERROR`, `-T`; MOTD kapalı | Hücre çıktısında MOTD yok |
| A3. `pkill -f` → `pkill -x` / `pgrep -f '^/path'` | "Killed" mesajı yok, tekrar çalıştırılabilir |
| A4. Chaos testini yeniden yaz: gNB-A **ve** gNB-C başlat, UE'leri doğru gNB ile eşle, `grep "PDU Session establishment is successful"` ile **assert** et | 3 UE → 3 `[LB]` satırı + 3 PDU session; aksi halde FAIL |
| A5. Arıza modları: `systemctl stop` / `kill -9` / `iptables DROP` (blackhole) / SIGSTOP | Her mod için beklenen davranış tablosu üretilir (blackhole'un tespit edilmediği ilk koşuda görülmeli → B6'yı tetikler) |
| A6. 4.2 hücresini düzelt: gNB'ler bağlıyken ölç, `systemd Restart` gecikmesini hesaba kat | `9 → 6` baseline değeriyle uyumlu |
| A7. 5.3'e ön koşul kontrolü | Süreç yoksa "önce 5.1'i çalıştır" uyarısı, ❌ değil |

### Faz B — Proxy doğruluğu (3–5 gün)
| Görev | Referans | Kabul ölçütü |
|---|---|---|
| B1. UE tablosu `(gnb_fd, ran_id)` + ters indeks | #1 | gNB-A/B/C'den eşzamanlı aynı RAN_ID'li 6 UE doğru AMF'e gider |
| B2. Drain döngüsü (EAGAIN'e kadar) | #2 | 50 UE aynı anda başlayınca mesaj kaybı/takılma yok |
| B3. `sctp_recvmsg/sendmsg` (PPID=60, stream korunur) | #6 | pcap'te tüm DATA chunk'ları PPID 60 |
| B4. AMF kopunca UE temizliği + gNB'ye `NG RESET`/`UEContextRelease` | #3, #10 | Etkilenen UE yeniden kaydolup sağlıklı AMF'e düşer; bayat fd yok |
| B5. Non-blocking connect + reconnect (backoff) + **NGSetup replay** | #4, #5 | `start amf2` sonrası ≤10 sn içinde AMF-2 yeniden havuzda; yeni UE'ler ona da gider |
| B6. SCTP heartbeat / `SCTP_PEER_ADDR_PARAMS`, `SCTP_RTOINFO`, `path_max_retrans` ayarı | A5 blackhole | Blackhole ≤ N sn içinde algılanır |
| B7. Setup/management mesajı birleştirme (NGSetupFailure, ConfigUpdate, Reset) | #11 | Bir AMF reddettiğinde gNB doğru sonucu görür |
| B8. `ngap_peek` modülü: gerçek APER ile IE 10/85/121 çıkarımı; birim testleri pcap'ten | #7 | `ran_id=0` ve NAS içi sahte `00 55 00` testleri geçer |

### Faz C — Akıllı yük dengeleme (3–4 gün)
| Görev | Kabul ölçütü |
|---|---|
| C1. Ağırlıklı seçim (smooth WRR): 100/100/50 | 100 UE'de dağılım ≈ 40/40/20 (±5) |
| C2. 5G-S-TMSI/GUAMI affinity: pointer → AMF | Aynı UE yeniden kayıtta aynı AMF'e döner |
| C3. Slice-farkındalıklı seçim (NSSAI görünürse) | SST2/3 UE'leri destekleyen AMF'e gider (şifreli NAS içindeyse RR'ye düş — ön rapor §3) |
| C4. Drain/maintenance modu (AMF'e yeni UE yok, mevcutlar sürer) | Rolling-restart senaryosunda 0 hata |
| C5. Tek-gNB başına dar/ geniş havuz seçeneği | Konfig ile belirlenir |

### Faz D — Gözlemlenebilirlik & ölçek (2–3 gün)
| Görev | Kabul ölçütü |
|---|---|
| D1. Prometheus `:9190` — `n2_gnb_up`, `n2_amf_up{amf}`, `n2_ue_total{amf}`, `n2_msgs_total{dir,proc}`, `n2_failover_total`, setup latency histogramı | Dashboard hücresinde (daily-ops §2) canlı gösterilir |
| D2. Yapılandırılmış log (seviye, zaman, gnb/ue/amf alanları) | `jq` ile filtrelenebilir |
| D3. Yük testi: 3 gNB × ≥50 UE, UERANSIM `nr-ue -n` | CPU/RAM/gecikme baseline'ı `BASELINE.md`'ye eklenir |
| D4. Çok iş parçacığı gerekmeden önce tek-thread epoll sınırı ölçülür | Karar noktası: ne zaman shard/multi-thread |

### Faz E — Ortam & dokümantasyon hijyeni (paralel, 1 gün)
- Daily-ops bölüm 6'yı master proxy'ye çevir; `gnb-b-proxy.yaml`, `gnb-c-proxy.yaml` üret (`30_ueransim_profiles.sh` / 5.0c'ye ekle); `MODE="proxy"` tüm gNB'lerde çalışsın.
- Roadmap tablosunu güncelle; `TOPOLOGY.md`'yi gerçek yapıya (`127.0.0.50`, `br-ue`) hizala; AMF isimlendirme kaymasını (`amf0`↔`AMF-1`) tek bir tablo ile sabitle.
- `tshark` kur (veya Mac tarafında `pyshark/scapy`); 6.4'ü NGAP decode çıktısı verecek şekilde genişlet.
- `n2proxy_master.cpp` + yeni dizini yedek listesine ekle; yedek rotasyonu (son N).
- SSH anahtarı + sınırlı `sudoers`; paroller `VM_PASS` env'e.
- `hssd/pcfd/udrd` hata triyajı; bilinen-zararsız listesi (`seppd`) ile "yeni hata" farkı raporla.
- Generator script'leri (`generate_*.py`) ile commit'li notebook'u tek kaynağa indir (ya generator'ı sil ya da CI'de üretilen notebook'la karşılaştır).

### Önerilen sıra
`A → B1-B5 → (A5 sonuçlarına göre B6) → C1-C2 → D1 → E` · **Faz B tamamlanmadan C/D'ye geçilmemeli**; çünkü yük dengeleme ölçümleri B1/B2/B4 hataları yüzünden güvenilir olmaz.

---

## 7. Kabul test matrisi (özet)

| Senaryo | Beklenen |
|---|---|
| 3 gNB, her biri 2 UE (aynı RAN_ID'ler) | 6 kayıt başarılı, doğru AMF'ler, çakışma yok |
| AMF-2 `kill -9` (gNB'ler bağlı, UE'ler aktif) | Etkilenen UE'ler release → yeniden kayıt sağlıklı AMF'e; diğerleri etkilenmez |
| AMF-2 blackhole (iptables DROP) | ≤ N sn'de havuzdan çıkar, yeni UE'ler sağlıklı AMF'lere |
| AMF-2 geri gelir | ≤ 10 sn'de havuza döner, NGSetup replay başarılı |
| 100 UE ardışık | Dağılım ≈ 40/40/20 |
| Aynı UE yeniden kayıt | Aynı AMF (GUTI affinity) |
| 3 gNB × 50 UE eşzamanlı | Kayıp/takılma yok, metrikler tutarlı |
| Proxy restart | gNB'ler yeniden NGSetup yapar, bağlam temiz |

---

## 8. Riskler / bilinmeyenler
- UERANSIM'in RAN_UE_NGAP_ID atama ve yeniden kullanım davranışı canlı pcap ile doğrulanmalı (#1'in şiddeti buna bağlı).
- Open5GS AMF'in PPID/stream'e gerçek toleransı; B3 sonrası regresyon testi şart.
- AMF çöktüğünde UE bağlamları kurtarılamaz (UDSF yok) — proxy yalnızca hasarı sınırlar (ön rapor §1.4).
- Şifreli NAS nedeniyle slice-bazlı seçim (C3) her zaman mümkün olmayabilir.
- Tek VM, 4 vCPU: yük testi sonuçları proxy'yi değil VM'i ölçebilir (UERANSIM+AMF aynı host).


---

## 9. Uygulama durumu (güncelleme)

| Faz | Durum | Not |
|---|---|---|
| A — Stabilizasyon & test | ✅ | proxy repoda, `n2lab_ops.py`, `chaos_suite.py`; MOTD/`pkill -x` düzeltmeleri |
| B — Doğruluk (B1–B8) | ✅ VM'de doğrulandı | B4: NG Reset yerine UE bazında UEContextReleaseCommand (UERANSIM NGReset'i işlemiyor); B6: heartbeat blackhole'u yakaladı |
| C — Akıllı LB | ✅ C1, C2, C4 (VM'de doğrulandı) · ⏸ C3 | C3: şifreli NAS nedeniyle ertelendi; `SIGSTOP` için opt-in uygulama yoklaması eklendi |
| D — Gözlemlenebilirlik | ✅ VM'de doğrulandı | metrikler + mesaj korunumu + yük üreteci; ≤150 kayıt/sn kayıpsız, 250–350/sn'de kayıp 5GC SBI hattında (SCP zaman aşımı sayısı = yanıtsız sayısı), proxy ~%1 CPU → çok-thread gerekmiyor. Yapılandırılmış (JSON) log açık |
| E — Ortam & dokümantasyon | ✅ | daily-ops proxy'ye bağlandı; yedek rotasyonu; `TOPOLOGY.md`/özet güncel; bayat generator'lar silindi. Açık: `tshark` kurulumu (VM'de manuel), `hssd/pcfd/udrd` hata triyajı |

Bölüm 3'teki #1–#8, #10–#12 giderildi; #9 (GUTI affinity) uygulandı; #14 (büyük mesaj) `MSG_EOR` kontrolüyle loglanıp düşürülüyor; #15 (konfig) ortam değişkenleriyle çözüldü.
