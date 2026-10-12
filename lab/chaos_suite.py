"""N2 LB proxy — assert'li chaos/regresyon seti (Faz A4-A5).

Kullanım (Mac/Jupyter):   python3 chaos_suite.py [baseline concurrent load load_mid load_big load_direct load_sweep weights drain stop kill9 blackhole amf_loss_reset reconnect probe_healthy sigstop sigstop_probe]
Her senaryo PASS / FAIL / XFAIL (bilinen açık, ilgili Faz B maddesi) döndürür.
Proxy kodu tek kaynaktan (../proxy/src/) VM'e senkronlanır. Yerel mantık testi: `make -C ../proxy test`.
"""
import re, sys, time
import n2lab_ops as ops

DETECT_WAIT = 20   # sn: arıza sonrası proxy'nin AMF'i fark etmesi için tanınan süre

AMF_UNIT = "open5gs-amfd@amf2"
AMF2_IP = "127.0.0.105"

# arıza modu -> (enjekte, geri al, XFAIL nedeni | None)
FAULTS = {
    "stop": (f"systemctl stop {AMF_UNIT}",
             f"systemctl start {AMF_UNIT}", None),
    "kill9": (f"kill -9 $(systemctl show -p MainPID --value {AMF_UNIT})",
              f"systemctl restart {AMF_UNIT}", None),
    "blackhole": (f"iptables -I INPUT -p sctp -d {AMF2_IP} -j DROP; iptables -I OUTPUT -p sctp -d {AMF2_IP} -j DROP",
                  f"iptables -D INPUT -p sctp -d {AMF2_IP} -j DROP; iptables -D OUTPUT -p sctp -d {AMF2_IP} -j DROP",
                  None),   # Faz B6: SCTP heartbeat (N2P_HB_MS) + path_max_retrans ile ~birkaç sn'de assoc düşer
    "sigstop": (f"kill -STOP $(systemctl show -p MainPID --value {AMF_UNIT})",
                f"kill -CONT $(pgrep -f 'open5gs-amfd.*amf2' | head -1) 2>/dev/null; systemctl restart {AMF_UNIT}",
                "Kernel SCTP donmuş (SIGSTOP) sürecin yerine heartbeat/ACK verir; tespit için uygulama düzeyi sağlık yoklaması gerekir (Faz C4/yeni madde)"),
}


class Result:
    def __init__(self, name):
        self.name, self.checks = name, []

    def check(self, label, cond, detail=""):
        self.checks.append((label, bool(cond), detail))
        print(f"   {'✔' if cond else '✘'} {label}" + (f"  [{detail}]" if detail and not cond else ""))
        return bool(cond)

    def warn(self, label, cond, detail=""):
        """Bilgilendirici kontrol: başarısızsa ⚠ yazar ama sonucu etkilemez."""
        print(f"   {'✔' if cond else '⚠'} {label}" + (f"  [{detail}]" if detail and not cond else ""))
        return bool(cond)

    @property
    def ok(self):
        return all(c for _, c, _ in self.checks)


def lb_events(log):
    """[LB] YENI UE (GNB_FD=x RAN_ID=y) -> AMF-n  =>  [(gnb_fd, ran_id, amf)]"""
    return [(int(g), int(r), int(a)) for g, r, a in
            re.findall(r"\[LB\] YENI UE \(GNB_FD=(\d+) RAN_ID=(\d+)\) -> AMF-(\d)", log)]


def fresh_env(proxy_env=""):
    ops.kill_ran(show=False)
    ops.ensure_amfs()
    ops.ensure_proxy_profiles()
    assert ops.sync_proxy() and ops.build_proxy() and ops.start_proxy(proxy_env), "proxy hazırlanamadı"


def scenario_baseline():
    r = Result("baseline")
    fresh_env()
    for g in ("a", "b", "c"):
        r.check(f"gNB-{g} NG Setup (proxy üzerinden)", ops.start_gnb(g))
    for u in ("s1", "s1sd1", "s2", "s3"):
        ok, out = ops.start_ue(u)
        r.check(f"UE-{u} (gNB-{ops.UE_GNB[u]}) PDU session", ok, out.replace("\n", " | ")[-120:])
    ev = lb_events(ops.proxy_log())
    r.check("4 UE → 4 [LB] olayı", len(ev) == 4, f"{len(ev)} olay: {ev}")
    r.check("3 AMF'in üçü de en az 1 UE aldı", {a for *_, a in ev} == {1, 2, 3}, str(ev))
    # B1 teşhisi: farklı gNB'lerde aynı RAN_ID var mı?
    by_id = {}
    for g, rid, a in ev:
        by_id.setdefault(rid, set()).add(g)
    clash = {k: v for k, v in by_id.items() if len(v) > 1}
    print(f"   ℹ  farklı gNB'lerde aynı RAN_ID (B1 çakışma riski): {clash or 'yok'}")
    return r


def scenario_fault(mode, proxy_env=""):
    inject, restore, xfail = FAULTS[mode]
    r = Result(mode if not proxy_env else f"{mode}+{proxy_env}")
    if proxy_env: xfail = None
    fresh_env(proxy_env)
    r.check("gNB-a NG Setup", ops.start_gnb("a"))
    ok, _ = ops.start_ue("s1")
    r.check("UE-s1 (öncesi) PDU session", ok)
    n_before = len(lb_events(ops.proxy_log()))
    print(f"   ⚡ arıza: {mode}")
    ops.sh(inject, show=False)
    time.sleep(DETECT_WAIT)
    r.check("proxy AMF-2 kaybını algıladı ([HA] pasife)", "[HA] AMF-2 pasife" in ops.proxy_log())
    ok, out = ops.start_ue("s3")
    new = lb_events(ops.proxy_log())[n_before:]
    r.check("arıza sonrası UE-s3 PDU session", ok, out.replace("\n", " | ")[-120:])
    # AMF systemd ile hızla geri geldiyse (kill9) AMF-2'ye atama meşrudur; yalnızca ölüyken atanmamalı.
    log = ops.proxy_log()
    recovered = "[HA] AMF-2 NGSetup tamam" in log.split("[HA] AMF-2 pasife", 1)[-1]
    if recovered:
        print("   ℹ  AMF-2 test sırasında yeniden havuza döndü (systemd Restart + proxy reconnect)")
    r.check("UE-s3 için yeni [LB] olayı var" + ("" if recovered else " ve ölü AMF-2'ye atanmadı"),
            len(new) >= 1 and (recovered or all(a != 2 for *_, a in new)), str(new))
    if mode == "kill9":
        r.check("AMF-2 systemd ile döndü ve proxy havuza aldı (reconnect + NGSetup replay)", recovered)
    ops.sh(restore, show=False)
    ops.ensure_amfs()
    r.xfail = xfail
    return r


def scenario_weights():
    """C1: AMF'lerin RelativeAMFCapacity değerleri NGSetupResponse'tan öğrenilir (TOPOLOGY.md: 100/100/50) ve seçimde ağırlık olur."""
    r = Result("weights")
    fresh_env()
    r.check("gNB-a NG Setup", ops.start_gnb("a"))
    log = ops.proxy_log()
    caps = {int(a): int(c) for a, c in re.findall(r"\[HA\] AMF-(\d) kapasite=(-?\d+)", log)}
    r.check("kapasiteler öğrenildi: AMF-1=100, AMF-2=100, AMF-3=50", caps == {1: 100, 2: 100, 3: 50}, str(caps))
    gu = re.findall(r"AMF-(\d) kapasite=\S+ GUAMI=(\d+) adet(?: \(ilk: set=(\d+) ptr=(\d+)\))?", log)
    print(f"   ℹ  GUAMI: {gu}   (affinity için her AMF'in pointer'ı benzersiz olmalı: 0/1/2)")
    r.check("her AMF'in en az 1 GUAMI'si ve benzersiz pointer'ı var", len(gu) == 3 and all(int(n) >= 1 for _, n, *_ in gu) and len({p for *_, p in gu}) == 3, str(gu))
    res = ops.start_ues_parallel(["s1", "s3"])
    r.check("UE-s1/UE-s3 PDU session", all(res.values()), str(res))
    r.check("seçim ağırlıklı RR olarak loglandı", "(Agirlikli RR)" in ops.proxy_log())
    return r


def scenario_drain():
    """C4: drain'deki AMF yeni UE almaz; undrain sonrası alır."""
    r = Result("drain")
    fresh_env()
    r.check("gNB-a NG Setup", ops.start_gnb("a"))
    ops.proxy_ctl("drain 1", "status")
    r.check("AMF-1 drain'e alındı", "AMF-1 drain moduna alindi" in ops.proxy_log())
    res = ops.start_ues_parallel(["s1", "s3"])
    r.check("UE-s1/UE-s3 PDU session", all(res.values()), str(res))
    ev = lb_events(ops.proxy_log())
    r.check("drain'deki AMF-1'e UE atanmadı", len(ev) == 2 and all(a != 1 for *_, a in ev), str(ev))
    ops.proxy_ctl("undrain 1")
    r.check("AMF-1 drain'den çıkarıldı", "AMF-1 drain'den cikarildi" in ops.proxy_log())
    print("   ℹ  durum çıktısı:\n      " + "\n      ".join(l for l in ops.proxy_log().splitlines() if "[CTL]" in l))
    return r


def scenario_probe_healthy():
    """C4 ön koşulu: yoklama açıkken SAĞLIKLI Open5GS AMF'ler yanıt verir (aksi halde yoklama tüm AMF'leri yanlışlıkla düşürürdü)."""
    r = Result("probe_healthy")
    fresh_env("N2P_PROBE_MS=1500")
    r.check("gNB-a NG Setup", ops.start_gnb("a"))
    time.sleep(15)
    log = ops.proxy_log()
    r.check("15 sn boyunca hiçbir AMF yoklamada düşmedi", "[ALARM]" not in log, "; ".join(l for l in log.splitlines() if "ALARM" in l or "probe" in l)[:200])
    res = ops.start_ues_parallel(["s1", "s3"])
    r.check("yoklama açıkken UE-s1/UE-s3 PDU session", all(res.values()), str(res))
    return r


def conservation(m):
    """Proxy mesaj korunumu: alınan + çoğaltma fazlalığı + proxy'nin ürettikleri == iletilen + yutulan + düşen."""
    g = lambda k: m.get(k, 0)
    rx = g('n2_messages_total{dir="gnb_to_proxy"}') + g('n2_messages_total{dir="amf_to_proxy"}') + g('n2_messages_consumed_total{kind="mcast_extra"}') + g('n2_messages_consumed_total{kind="injected"}')
    out = g('n2_messages_total{dir="proxy_to_gnb"}') + g('n2_messages_total{dir="proxy_to_amf"}') + g('n2_messages_consumed_total{kind="swallowed"}') + \
        sum(v for k, v in m.items() if k.startswith("n2_dropped_total"))
    return rx, out


def scenario_load(gnbs=3, ues=50, rate=100, wait=15, strict=True):
    """D3: sentetik gNB'ler × UE (aynı RAN id'ler) ile yük.
    Proxy'ye ait kontroller (korunum, drop=0, dağılım) her zaman zorunlu. Yanıt oranı yalnızca strict=True ise zorunlu:
    yüksek hızda yanıt kaybı proxy dışındadır (proxy korunum sayaçlarıyla kanıtlanır); nedeni için gönderim penceresindeki CPU, yanıtsız UE'lerin AMF dağılımı
    ve NF journal hataları raporlanır."""
    r = Result(f"load_{gnbs}x{ues}@{rate}")
    fresh_env("N2P_METRICS=127.0.0.1:9190")
    r.check("yük üreteci VM'e kopyalandı", ops.sync_loadgen())
    _, cfg = ops.sh("grep -vE '^[[:space:]]*(#|$)' /etc/open5gs/scp.yaml 2>/dev/null | head -30", sudo=False, show=False)
    print("   ℹ  /etc/open5gs/scp.yaml (yorumsuz):\n      " + (cfg.replace("\n", "\n      ") or "(okunamadı)"))
    _, t_epoch = ops.sh("date +%s", sudo=False, show=False); t_epoch = int(t_epoch.strip() or time.time())
    rc, out, res = ops.run_loadgen(f"--gnbs {gnbs} --ues {ues} --rate {rate} --wait {wait}", timeout=wait + 240)
    if not res:
        r.check("yük üreteci sonuç üretti", False, out[-300:]); return r
    total = gnbs * ues
    print(f"   ℹ  {res['sent']} gönderildi / {res['answered']} yanıt · gönderim {res['send_rate']}/sn · gecikme ms p50={res['lat_ms']['p50']} p95={res['lat_ms']['p95']} p99={res['lat_ms']['p99']} max={res['lat_ms']['max']}")
    win = res.get("cpu_send_window_cores", {})
    print(f"   ℹ  CPU, GÖNDERİM PENCERESİNDE (çekirdek; {res.get('ncpu')} vCPU): " + ", ".join(f"{k}={v}" for k, v in list(win.items())[:7]) + f"  · proxy RSS={res['proxy_rss_kb']} kB")
    print("   ℹ  toplam CPU saniyesi (tüm koşu): " + ", ".join(f"{k}={v}" for k, v in sorted(res.get("cpu_total_s", {}).items(), key=lambda kv: -kv[1])[:6]))
    cores = win
    m = ops.metrics()
    # --- proxy'ye ait zorunlu kontroller
    got = m.get('n2_messages_total{dir="gnb_to_proxy"}')
    r.check(f"proxy {total} InitialUE'nin hepsini aldı (+{gnbs} NGSetup)", got == total + gnbs, f"{got} != {total + gnbs}")
    rx, o = conservation(m)
    r.check("mesaj korunumu: proxy hiçbir mesajı sessizce kaybetmedi", rx == o, f"{rx} != {o}")
    drops = {k: v for k, v in m.items() if k.startswith("n2_dropped_total") and v}
    r.check("drop sayaçları sıfır", not drops, str(drops))
    ev = lb_events(ops.proxy_log())
    cnt = [sum(1 for *_, a in ev if a == i) for i in (1, 2, 3)]
    exp = [total * 0.4, total * 0.4, total * 0.2]
    r.check("dağılım kapasite ağırlığına uygun (≈40/40/20, ±3)", len(ev) == total and all(abs(c - e) <= 3 for c, e in zip(cnt, exp)), f"{cnt} beklenen≈{[round(e) for e in exp]}")
    r.check("Prometheus: atama sayacı = UE sayısı", sum(v for k, v in m.items() if k.startswith("n2_lb_assignments_total")) == total)
    # --- 5GC yanıt oranı
    ok_resp = res["lost"] == 0
    if strict:
        r.check(f"tüm {total} InitialUE yanıtlandı", ok_resp, f"kayıp={res['lost']}")
    else:
        r.warn(f"5GC zinciri tüm {total} InitialUE'ye {wait} sn içinde yanıt verdi", ok_resp,
               f"{res['answered']}/{total} yanıt, max gecikme {res['lat_ms']['max']} ms — nedeni yukarıdaki CPU/AMF/journal satırlarından okunur; proxy CPU (gönderim penceresi)={cores.get('n2proxy_master', 0)} çekirdek")
    if res["lost"]:
        gnb_fds = []
        for g_, *_ in ev:
            if g_ not in gnb_fds: gnb_fds.append(g_)
        amf_of = {(gnb_fds.index(g_), ran - 0): a for g_, ran, a in ev}
        # load_gen RAN id = BASE+n; proxy logu mutlak RAN id yazar -> BASE farkı
        base = min(ran for _, ran, _ in ev)
        by_amf = {1: 0, 2: 0, 3: 0}; unk = 0
        for pi, n in res["missing"]:
            a = amf_of.get((pi, base + n))
            if a: by_amf[a] += 1
            else: unk += 1
        print(f"   ℹ  yanıtsız UE'lerin AMF dağılımı (proxy atamasına göre): {by_amf}" + (f" (eşleşmeyen {unk})" if unk else "") +
              f" · gNB başına: { {pi: sum(1 for p_, _ in res['missing'] if p_ == pi) for pi in range(gnbs)} }")
    errs = ops.journal_errors(["open5gs-amfd@amf", "open5gs-amfd@amf2", "open5gs-amfd@amf3", "open5gs-scpd", "open5gs-ausfd", "open5gs-udmd", "open5gs-udrd"], t_epoch)
    for u, (n, rows) in errs.items():
        if n: print(f"   ℹ  {u}: {n} hata/uyarı — " + " | ".join(f"{c}× {msg[:90]}" for c, msg in rows[:2]))
    ops.sh("for a in amf amf2 amf3; do systemctl restart open5gs-amfd@$a; done; sleep 3", show=False)   # sahte UE bağlamlarını temizle
    ops.kill_ran(show=False)                  # yük üreteci/gNB bağlantıları kapansın
    ops.wait_quiet()                          # AUSF/UDM/UDR/MongoDB kuyrukları boşalsın: sonraki senaryo kalıntıdan etkilenmesin
    return r


def scenario_load_direct(gnbs=3, ues=100, rate=300, wait=45):
    """KONTROL DENEYİ: aynı yük üreteci proxy'siz, doğrudan AMF-1'e (127.0.0.5), `load_sweep`'in 300/sn satırıyla AYNI toplam hızda.
    Darboğaz 5GC'nin ortak SBI hattında (SCP/AUSF/UDM) olduğundan karşılaştırma AMF başına değil TOPLAM kayıt hızı üzerinden yapılmalıdır
    (ilk sürümdeki 160/sn kontrolü adil değildi: 5GC'ye gelen toplam hız proxy yolundakinin yarısından azdı).
    Kayıp proxy'siz de görülürse (~%24) proxy masumdur."""
    r = Result(f"load_direct_{gnbs}x{ues}@{rate}")
    fresh_env()
    r.check("yük üreteci VM'e kopyalandı", ops.sync_loadgen())
    _, t_epoch = ops.sh("date +%s", sudo=False, show=False); t_epoch = int(t_epoch.strip() or time.time())
    rc, out, res = ops.run_loadgen(f"--proxy 127.0.0.5:38412 --gnbs {gnbs} --ues {ues} --rate {rate} --wait {wait}", timeout=wait + 240)
    if not res:
        r.check("yük üreteci sonuç üretti", False, out[-300:]); return r
    total = gnbs * ues
    print(f"   ℹ  DOĞRUDAN AMF-1: {res['sent']} gönderildi / {res['answered']} yanıt ({round(100 * res['answered'] / total)}%) · gönderim {res['send_rate']}/sn · gecikme ms p50={res['lat_ms']['p50']} p95={res['lat_ms']['p95']} max={res['lat_ms']['max']}")
    print("   ℹ  CPU (gönderim penceresi): " + ", ".join(f"{k}={v}" for k, v in list(res.get('cpu_send_window_cores', {}).items())[:6]))
    errs = ops.journal_errors(["open5gs-scpd", "open5gs-amfd@amf"], t_epoch)
    for u, (n, rows) in errs.items():
        if n: print(f"   ℹ  {u}: {n} hata/uyarı — " + " | ".join(f"{c}× {msg[:90]}" for c, msg in rows[:2]))
    r.warn(f"proxy'siz doğrudan AMF-1'e {total} UE'nin hepsi yanıtlandı", res["lost"] == 0, f"{res['answered']}/{total} — kayıp proxy'siz de var ⇒ proxy masum, darboğaz AMF/SCP/5GC")
    ops.sh("for a in amf amf2 amf3; do systemctl restart open5gs-amfd@$a; done; sleep 3", show=False)
    ops.kill_ran(show=False); ops.wait_quiet()
    return r


def scenario_load_sweep(rates=(150, 250, 300, 350), ues=100, wait=30):
    """Darboğaz eşiği: artan hızda aynı yük (3 gNB × ues). Yanıt oranının düştüğü 'diz' noktası ve SCP zaman aşımı sayısı raporlanır."""
    r = Result("load_sweep")
    fresh_env("N2P_METRICS=127.0.0.1:9190")
    r.check("yük üreteci VM'e kopyalandı", ops.sync_loadgen())
    print(f"   {'hız/sn':>7} {'gönderim':>9} {'yanıt':>6} {'oran':>6} {'p95 ms':>8} {'max ms':>8} {'scp zaman aşımı':>16}")
    for rate in rates:
        _, t_epoch = ops.sh("date +%s", sudo=False, show=False); t_epoch = int(t_epoch.strip() or time.time())
        rc, out, res = ops.run_loadgen(f"--gnbs 3 --ues {ues} --rate {rate} --wait {wait}", timeout=wait + 240)
        if not res:
            r.check(f"{rate}/sn koşusu sonuç üretti", False, out[-200:]); break
        tot = 3 * ues
        _, tm = ops.sh(f"journalctl -u open5gs-scpd --since @{t_epoch} --no-pager -o cat 2>/dev/null | grep -c 'Connection timer expired'", sudo=False, show=False)
        print(f"   {rate:>7} {res['sent']:>9} {res['answered']:>6} {round(100 * res['answered'] / tot):>5}% {res['lat_ms']['p95']:>8} {res['lat_ms']['max']:>8} {tm.strip():>16}")
        rx, o = conservation(ops.metrics())
        r.check(f"{rate}/sn: proxy mesaj korunumu", rx == o, f"{rx} != {o}")
        ops.sh("for a in amf amf2 amf3; do systemctl restart open5gs-amfd@$a; done; sleep 3", show=False)
        ops.kill_ran(show=False); ops.wait_quiet(show=False)
        fresh_env("N2P_METRICS=127.0.0.1:9190")                    # proxy sayaçları/UE tablosu her hızda temiz
    return r


def scenario_concurrent():
    """B1/B2: 3 gNB, 4 UE AYNI ANDA (farklı gNB'lerde aynı RAN_UE_NGAP_ID)."""
    r = Result("concurrent")
    fresh_env()
    for g in ("a", "b", "c"):
        r.check(f"gNB-{g} NG Setup", ops.start_gnb(g))
    res = ops.start_ues_parallel(["s1", "s1sd1", "s2", "s3"])
    for u, ok in res.items():
        r.check(f"UE-{u} (gNB-{ops.UE_GNB[u]}) PDU session", ok)
    ev = lb_events(ops.proxy_log())
    r.check("4 UE → 4 [LB] olayı", len(ev) == 4, str(ev))
    log = ops.proxy_log()
    r.check("bilinmeyen UE / yanlış AMF uyarısı yok", "bilinmeyen UE" not in log and "baska AMF'e ait" not in log,
            "; ".join(l for l in log.splitlines() if "[WARN]" in l)[:200])
    return r


def scenario_amf_loss_reset():
    """B4: UE'si olan AMF düşer → proxy etkilenen UE'leri siler ve gNB'ye NG Reset yollar."""
    r = Result("amf_loss_reset")
    fresh_env()
    r.check("gNB-a NG Setup", ops.start_gnb("a"))
    res = ops.start_ues_parallel(["s1", "s3"])      # RR: AMF-1, AMF-2
    r.check("UE-s1/UE-s3 PDU session", all(res.values()), str(res))
    ev = lb_events(ops.proxy_log())
    r.check("UE'lerden biri AMF-2'de", any(a == 2 for *_, a in ev), str(ev))
    ops.sh(FAULTS["kill9"][0], show=False)
    time.sleep(8)
    log = ops.proxy_log()
    after = log.split("[HA] AMF-2 pasife", 1)[-1]
    print("   ℹ  kayıp sonrası proxy olayları ([LB]/[HA]/[WARN]; S-TMSI affinity görünüyor mu? release neden iki kez?):\n      " + ("\n      ".join(l for l in after.splitlines() if any(t in l for t in ("[LB]", "[HA]", "[WARN]"))) or "(yok)"))
    r.check("AMF-2 kaybı + etkilenen UE=1", "[HA] AMF-2 pasife" in log and "etkilenen UE=1" in log)
    r.check("etkilenen UE için UEContextReleaseCommand gönderildi", "UEContextReleaseCommand gonderildi" in log)
    time.sleep(3)
    r.check("gNB release'i tamamladı (ReleaseComplete)", "release tamamlandi" in ops.proxy_log())
    _, g = ops.sh("grep -iE 'release|reset|unhandled|error' /tmp/gnb-a.log | tail -6", sudo=False, show=False)
    print("   ℹ  gNB-a log (release/reset/unhandled/error):\n      " + (g.replace("\n", "\n      ") or "(yok)"))
    for u in ("s1", "s3"):
        _, ul = ops.sh(f"grep -iE 'release|idle|reject|deregist|registration|service request|PDU Session establishment is' /tmp/ue-{u}.log | tail -5", sudo=False, show=False)
        print(f"   ℹ  UE-{u} log:\n      " + (ul.replace("\n", "\n      ") or "(yok)"))
    counts, ok = [], False
    for _ in range(15):
        _, c = ops.sh("for u in s1 s3; do grep -c 'Initial Registration is successful' /tmp/ue-$u.log; done", sudo=False, show=False)
        counts = [int(x) for x in c.split() if x.isdigit()]
        if any(x >= 2 for x in counts): ok = True; break
        time.sleep(1)
    r.check("etkilenen UE ≤15 sn'de yeniden kaydoldu (Initial Registration ×2)", ok, f"kayıt sayıları s1/s3={counts}")
    ops.ensure_amfs()
    return r


def scenario_reconnect():
    """B5: AMF-2 düşer, geri gelir → ≤20 sn'de havuza döner (NGSetup replay) ve yeni UE alır."""
    r = Result("reconnect")
    fresh_env()
    r.check("gNB-a NG Setup", ops.start_gnb("a"))
    ops.sh(FAULTS["stop"][0], show=False)
    time.sleep(4)
    r.check("AMF-2 kaybı algılandı", "[HA] AMF-2 pasife" in ops.proxy_log())
    ops.sh(FAULTS["stop"][1], show=False)
    ok = False
    for _ in range(20):
        if "[HA] AMF-2 NGSetup tamam" in ops.proxy_log().split("[HA] AMF-2 pasife", 1)[-1]: ok = True; break
        time.sleep(1)
    r.check("AMF-2 ≤20 sn'de yeniden bağlandı ve NGSetup replay tamam (READY)", ok)
    res = ops.start_ues_parallel(["s1", "s3"])
    r.check("UE-s1/UE-s3 PDU session", all(res.values()), str(res))
    ev = lb_events(ops.proxy_log())
    r.check("AMF-2 yeni UE aldı", any(a == 2 for *_, a in ev), str(ev))
    return r


SCENARIOS = {"load": scenario_load, "load_big": lambda: scenario_load(3, 200, 400, wait=45, strict=False), "load_direct": scenario_load_direct, "load_sweep": scenario_load_sweep, "load_mid": lambda: scenario_load(3, 100, 200, wait=30, strict=False), "baseline": scenario_baseline, "concurrent": scenario_concurrent,
             "weights": scenario_weights, "drain": scenario_drain, "probe_healthy": scenario_probe_healthy,
             "sigstop_probe": lambda: scenario_fault("sigstop", "N2P_PROBE_MS=1500"),
             "amf_loss_reset": scenario_amf_loss_reset, "reconnect": scenario_reconnect, **{m: (lambda m=m: scenario_fault(m)) for m in FAULTS}}


def main(names):
    names = names or list(SCENARIOS)
    if not ops.vm_ok():
        print("❌ VM'e bağlanılamadı"); return 2
    rows = []
    for n in names:
        print(f"\n━━ {n} ━━")
        try:
            r = SCENARIOS[n]()
        except AssertionError as e:
            r = Result(n); r.check(str(e), False)
        status = "PASS" if r.ok else ("XFAIL" if getattr(r, "xfail", None) else "FAIL")
        rows.append((n, status, getattr(r, "xfail", None) if status == "XFAIL" else ""))
    ops.kill_ran(show=False); ops.ensure_amfs()
    print("\n══ SONUÇ ══")
    for n, s, note in rows:
        print(f"  {n:10s} {s:6s} {note or ''}")
    return 1 if any(s == "FAIL" for _, s, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
