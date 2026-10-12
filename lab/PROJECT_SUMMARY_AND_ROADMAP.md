# N2 LB (Load Balancer) Proxy — Proje Özeti ve Yol Haritası

Open5GS + UERANSIM lab VM'inde geliştirilen **N2 (NGAP/SCTP) şeffaf yük dengeleyici**. Kaynak: `../proxy/` (C++), lab operasyonu: `local_vm_daily_ops_v1.ipynb`, otomatik doğrulama: `01_N2_Master_Proxy_Lab_v2.ipynb` + `chaos_suite.py`. Analiz ve gerekçeler: `ANALYSIS_AND_ENHANCEMENT_PLAN_TR.md`; topoloji: `TOPOLOGY.md`.

## Tamamlanan aşamalar

| Aşama | İçerik | Doğrulama |
|---|---|---|
| 1–5 | Keşif, host hazırlığı, 3'lü AMF havuzu, gNB/UE profilleri, baseline (`BASELINE.md`), rollback (`99_rollback.sh`) | VM |
| 6–7 | C++ epoll proxy iskeleti (tek AMF pass-through) | VM |
| **A** | Proxy kaynağı repoya; `n2lab_ops.py`; assert'li `chaos_suite.py` (PASS/FAIL/XFAIL) | VM |
| **B** | `(gNB,RAN_ID)` UE tablosu, drain döngüsü, PPID/stream, non-blocking connect + backoff + NGSetup replay, SCTP heartbeat, setup yanıt birleştirme, gerçek APER ayrıştırma, AMF kaybında UEContextReleaseCommand (UERANSIM NGReset'i işlemiyor) | VM: baseline, concurrent, stop, kill9, blackhole, amf_loss_reset, reconnect |
| **C** | Kapasite-ağırlıklı seçim (smooth WRR), 5G-S-TMSI affinity (GUAMI pointer), drain/undrain/status, opt-in canlılık yoklaması (`SIGSTOP`'lu AMF) | VM: weights, drain, probe_healthy, sigstop_probe |
| **D** | Prometheus metrikleri (`:9190`), bağlantı/NGSetup zaman aşımı, sentetik yük üreteci `load_gen.py` | VM: ≤150 kayıt/sn kayıpsız (p95 20–30 ms); 250–350/sn'de %20–42 UE yanıtsız ama proxy mesajları eksiksiz iletiyor (korunum, ~%1 CPU) — kayıp 5GC SBI hattında (SCP `Connection timer expired` sayısı = yanıtsız sayısı) |
| **E** | Daily-ops notebook'u master proxy'ye bağlandı; dokümanlar/topoloji gerçeğe hizalandı; bayat generator'lar kaldırıldı; SSH anahtarı desteği | — |

Test piramidi: `make -C proxy test` (111 birim + 79 mantık kontrolü, pycrate APER vektörleri, sahte AMF/gNB, TCP taşıma) → VM'de `chaos_suite` (gerçek SCTP/Open5GS/UERANSIM).

## Bilinen sınırlar / sonraki adaylar
- **C3 slice farkındalığı ertelendi:** Requested NSSAI şifreli NAS içinde; InitialUEMessage yalnızca yeniden kayıtta AllowedNSSAI taşır.
- Yazma kuyruğu yok (EAGAIN'de mesaj düşer); tek thread (yük testi sonuçlarına göre shard kararı).
- AMF-UE-NGAP-ID'si bilinmeyen UE için release komutu üretilemez (NG Reset'e düşülür).
- Yoklama opt-in: yeni bir AMF sürümünde boş RANConfigurationUpdate'e yanıt davranışı `probe_healthy` ile yeniden doğrulanmalı.
- Handover/Xn-N2 yolu, Paging'in yalnızca ilgili AMF'e yönlendirilmesi, çoklu proxy örneği (HA çifti) kapsam dışı.
- Güvenlik: lab parolası `VM_PASS`; üretim benzeri kullanım için anahtar tabanlı SSH (`setup_ssh_key.sh`) ve izole yönetim ağı.
