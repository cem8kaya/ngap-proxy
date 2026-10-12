"""01_N2_Master_Proxy_Lab_v2.ipynb üretici (Faz A). Proxy kaynağı repodaki proxy/src/*.cpp'dir."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb.cells = [
    md("""# N2 Master Proxy Lab v2 (Faz A: stabilizasyon & assert'li test)

`00_N2_Master_Proxy_Lab.ipynb` deneyinin tekrarlanabilir hali:
- Proxy kaynağı **repoda** (`../proxy/src/n2proxy_master.cpp`); notebook yalnızca *sync → build → run → assert* yapar.
- SSH yardımcıları `n2lab_ops.py` (MOTD yok, `pkill -x`, parola `VM_PASS` env).
- Senaryolar `chaos_suite.py`: her biri **PASS / FAIL / XFAIL** (XFAIL = ilgili Faz B maddesiyle bilinen açık).
- UE → gNB eşlemesi doğru: s1,s3→gNB-A · s1sd1→gNB-B · s2→gNB-C (v1'de yalnız gNB-A açılıyordu).
"""),
    md("## 1. Bağlantı ve hazırlık"),
    code("""import importlib, n2lab_ops as ops, chaos_suite as cs
importlib.reload(ops); importlib.reload(cs)
print("VM:", "✅" if ops.vm_ok() else "❌ erişilemiyor")
ops.kill_ran(); print(ops.ensure_amfs()[1])
print(ops.ensure_proxy_profiles())"""),
    md("## 2. Proxy: senkronla → derle → başlat"),
    code("ops.sync_proxy() and ops.build_proxy() and ops.start_proxy()"),
    md("## 3. Senaryolar\n`baseline` (ardışık) ve `concurrent` (eşzamanlı) 3 gNB + 4 UE kaydı; `stop/kill9/blackhole/sigstop` arıza modları; `amf_loss_reset` (NG Reset) ve `reconnect` (yeniden bağlanma + NGSetup replay)."),
    code("cs.main(['baseline', 'concurrent'])"),
    code("cs.main(['stop', 'kill9', 'blackhole'])"),
    code("cs.main(['amf_loss_reset', 'reconnect'])"),
    code("cs.main(['weights', 'drain'])   # Faz C: kapasite-ağırlıklı LB, GUAMI, drain"),
    md("### Faz D: yük testi + metrikler\nSentetik gNB'ler (`load_gen.py`, aynı RAN id'ler) proxy'ye eşzamanlı InitialUE yağdırır; yanıt kaybı, dağılım, gecikme, proxy CPU/RSS ve Prometheus sayaçları raporlanır. `load` = 3×50 @100/sn (yanıt kaybı yok şart), `load_mid` = 3×100 @200/sn, `load_big` = 3×200 @400/sn (yüksek hızda darboğaz genellikle 5GC zinciridir: proxy kontrolleri zorunlu, 5GC yanıt oranı bilgilendirici; CPU dağılımı raporlanır). Proxy mesaj korunumu (`rx == tx + yutulan + düşen`) her koşuda doğrulanır."),
    code("cs.main(['load', 'load_mid', 'load_big'])"),
    md("### Yanıtsızlık teşhisi: kontrol deneyi + hız taraması\\n`load_direct` aynı yükü proxy'siz doğrudan AMF-1'e verir (kayıp orada da varsa proxy masum). `load_sweep` hızı artırarak yanıt oranının düştüğü eşiği ve SCP 'Connection timer expired' sayısını raporlar (uzun sürer: ~8-10 dk)."),
    code("cs.main(['load_direct', 'load_sweep'])"),
    code("cs.main(['sigstop'])   # XFAIL beklenir: kernel SCTP donmuş süreci algılatmaz"),
    md("### Uygulama düzeyi yoklama (opt-in, `N2P_PROBE_MS`)\nÖnce `probe_healthy` PASS olmalı: sağlıklı Open5GS AMF'in boş RANConfigurationUpdate'e yanıt verdiğini kanıtlar. Aksi halde yoklama açmayın."),
    code("cs.main(['probe_healthy'])"),
    code("cs.main(['sigstop_probe'])   # probe_healthy PASS ise: donmuş AMF algılanmalı"),
    md("## 4. Proxy logu"),
    code("print(ops.proxy_log())"),
    md("## 5. Temizlik"),
    code("ops.kill_ran(); print(ops.ensure_amfs()[1])"),
]
nbf.write(nb, "01_N2_Master_Proxy_Lab_v2.ipynb")
