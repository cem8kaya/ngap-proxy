# N2 LB Proxy — Gerçek Topoloji (Faz E sonrası)

> Önceki sürümdeki `10.200.0.x` / `proxy-ns` tasarımı **kullanılmadı**. Proxy, `lo` üzerindeki `127.0.0.50` alias'ında çalışır; UE'ler `ns-<profil>` + `br-ue` ile izole edilir.

```text
 UE ns-s1,ns-s3 ─┐                         ┌─ gNB-A 127.0.0.11 ─┐                         ┌─ AMF-1 127.0.0.5   (amf0, ptr 0, cap 100)
 UE ns-s1sd1    ─┼─ br-ue 10.201.0.0/24 ──┼─ gNB-B 127.0.0.12 ─┼─ SCTP ─► N2 PROXY ──────┼─ AMF-2 127.0.0.105 (amf1, ptr 1, cap 100)
 UE ns-s2       ─┘  (radio-link, linkIp)  └─ gNB-C 127.0.0.13 ─┘        127.0.0.50:38412  └─ AMF-3 127.0.0.205 (amf2, ptr 2, cap 50)
                                                                        metrik 127.0.0.1:9190 · komut /tmp/n2proxy.ctl
```
Proxy her gNB için ayrı 3 SCTP association açar (şeffaf mod; NGAP ID yeniden yazılmaz). gNB'ler yalnızca proxy'yi görür (`gnb-X-proxy.yaml`: `amfConfigs` = 127.0.0.50).

## AMF isimlendirme eşlemesi
| Proxy / dokümanlardaki ad | systemd servisi | config | `amf_name` | NGAP adresi | pointer | `relative_capacity` |
|---|---|---|---|---|---|---|
| AMF-1 | `open5gs-amfd@amf` | `/etc/open5gs/amf.yaml` | `open5gs-amf0` | 127.0.0.5 | 0 | 100 |
| AMF-2 | `open5gs-amfd@amf2` | `amf2.yaml` | `open5gs-amf1` | 127.0.0.105 | 1 | 100 |
| AMF-3 | `open5gs-amfd@amf3` | `amf3.yaml` | `open5gs-amf2` | 127.0.0.205 | 2 | 50 |

GUAMI: PLMN 999/70, region 2, set 1. Pointer'lar benzersiz olmalı (S-TMSI affinity buna dayanır; `weights` senaryosu doğrular).

## gNB / UE
| gNB | IP | nci | linkIp (br-ue) | UE profilleri |
|---|---|---|---|---|
| gNB-A | 127.0.0.11 | 0x10 | 10.201.0.11 | `ue-s1` (SST1), `ue-s3` (SST3) |
| gNB-B | 127.0.0.12 | 0x11 | 10.201.0.12 | `ue-s1sd1` (SST1/SD000001) |
| gNB-C | 127.0.0.13 | 0x12 | 10.201.0.13 | `ue-s2` (SST2) |

PDU session havuzları: 10.45.0.0/16 (internet), 10.46.0.0/16 (urllc), 10.47.0.0/16 (iot) — `ogstun`, NAT 10.201.0.0/24 ve UE havuzları için kurulur (daily-ops bölüm 10.2).

## Dosya / süreç yerleşimi (VM)
`~/n2proxy_src/` (repodan senkronlanır) → `~/n2proxy_master` · `~/proxy_master.log` · `~/loadgen/` · `~/n2lab_pcaps/` · `~/ops_backup_*` (son 5) · `~/99_rollback.sh`.
