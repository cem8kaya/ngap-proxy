"""N2 LB lab — Mac/Jupyter tarafı VM operasyon yardımcıları (Faz A).

- Her komut uzak komut olarak çalışır (login shell yok -> MOTD gürültüsü yok), `-T` ile pty istenmez.
- Parola VM_PASS ortam değişkeninden okunur (varsayılan: lab parolası).
- Süreç öldürmede `pkill -f` kullanılmaz: kalıp sudo/bash komut satırını da eşleştirip boru hattını öldürür.
  Bunun yerine tam isim (`-x`) veya `^/yol` ile başlayan kalıp kullanılır.
"""
import base64, os, pathlib, shlex, subprocess, time

VM_USER = "localadmin"
VM_HOST = "127.0.0.1"
VM_PORT = os.environ.get("VM_PORT", "2222")
VM_PASS = os.environ.get("VM_PASS", "open5gs")
VM_KEY = os.environ.get("VM_KEY", "")            # doluysa SSH girişi anahtarla (bkz. setup_ssh_key.sh); sudo için VM_PASS yine kullanılır

SSH_OPTS = ["-T", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8",
            "-o", "ControlMaster=auto", "-o", "ControlPersist=120",
            "-o", f"ControlPath=/tmp/ssh-vm-{VM_PORT}"]
_ENV = {**os.environ, "SSHPASS": VM_PASS, "SSH_ASKPASS_REQUIRE": "never"}

REPO_PROXY_DIR = pathlib.Path(__file__).resolve().parent.parent / "proxy"
PROXY_FILES = ["src/n2proxy_master.cpp", "src/ngap.h", "src/lb_core.h"]
VM_HOME = "/home/localadmin"
PROXY_DIR = f"{VM_HOME}/n2proxy_src"           # VM'de kaynak dizini (src/ yapısı korunur)
PROXY_BIN = f"{VM_HOME}/n2proxy_master"
PROXY_LOG = f"{VM_HOME}/proxy_master.log"
CFG = "/opt/UERANSIM/config/n2lab"


def _ssh(remote_cmd, timeout=120, retries=3):
    for _ in range(retries):
        base = ["ssh", "-i", VM_KEY, "-o", "BatchMode=yes"] if VM_KEY else ["sshpass", "-e", "ssh"]
        r = subprocess.run([*base, *SSH_OPTS, "-p", VM_PORT,
                            f"{VM_USER}@{VM_HOST}", remote_cmd],
                           capture_output=True, text=True, env=_ENV, timeout=timeout)
        if r.returncode == 255 and "Permission denied" in r.stderr:
            time.sleep(1.5); continue
        break
    return r


def sh(script, sudo=True, timeout=180, show=True, desc=""):
    """Çok satırlı bash scriptini VM'de çalıştırır. Dönüş: (rc, stdout)."""
    if desc and show:
        print(f"▶ {desc}")
    b64 = base64.b64encode(("set +e\n" + script).encode()).decode()
    run = (f"echo {shlex.quote(VM_PASS)} | sudo -S -p '' bash /tmp/_n2.sh" if sudo else "bash /tmp/_n2.sh")
    r = _ssh(f"echo {b64} | base64 -d > /tmp/_n2.sh; {run}; rc=$?; rm -f /tmp/_n2.sh; exit $rc", timeout)
    out = r.stdout.rstrip()
    if show:
        if out: print(out)
        if r.stderr.strip(): print(f"[STDERR] {r.stderr[:600].rstrip()}")
    return r.returncode, out


def vm_ok():
    rc, _ = sh("true", sudo=False, show=False)
    return rc == 0


# ------------------------------------------------------------------ süreç yönetimi
def kill_ran(show=True):
    """gNB/UE/proxy'yi (tam isim eşleşmesiyle) öldürür."""
    return sh("""
pkill -x nr-ue; sleep 1; pkill -9 -x nr-ue
pkill -x nr-gnb; sleep 2; pkill -9 -x nr-gnb
pkill -9 -x n2proxy_master; pkill -9 -x n2proxy_advanced
sleep 1
echo "gNB=$(pgrep -x nr-gnb | wc -l) UE=$(pgrep -x nr-ue | wc -l) proxy=$(pgrep -x n2proxy_master | wc -l)"
""", show=show, desc="gNB/UE/proxy temizliği" if show else "")


def ensure_amfs():
    return sh("""
pkill -CONT -x open5gs-amfd 2>/dev/null          # SIGSTOP'ta kalmış AMF varsa çöz (systemctl start aktif servise dokunmaz)
iptables -D INPUT -p sctp -d 127.0.0.105 -j DROP 2>/dev/null
iptables -D OUTPUT -p sctp -d 127.0.0.105 -j DROP 2>/dev/null
for a in amf amf2 amf3; do systemctl start open5gs-amfd@$a; done
sleep 2
for a in amf amf2 amf3; do echo "$a: $(systemctl is-active open5gs-amfd@$a)"; done
""", show=False)


# ------------------------------------------------------------------ proxy: sync -> build -> run
def put_file(local, remote):
    """Yerel dosyayı VM'e base64 ile kopyalar (scp/sshpass dosya modu gerekmez); sha256 ile doğrular."""
    import hashlib
    data = pathlib.Path(local).read_bytes()
    b64 = base64.b64encode(data).decode()
    sh(f"mkdir -p $(dirname {remote}); : > {remote}.b64", sudo=False, show=False)
    for i in range(0, len(b64), 60000):
        sh(f"printf %s {b64[i:i + 60000]} >> {remote}.b64", sudo=False, show=False)
    rc, out = sh(f"base64 -d {remote}.b64 > {remote} && rm -f {remote}.b64 && sha256sum {remote} | cut -c1-16", sudo=False, show=False)
    return rc == 0 and out.strip().startswith(hashlib.sha256(data).hexdigest()[:16])


def sync_proxy(base=REPO_PROXY_DIR):
    """Repodaki proxy kaynaklarını (çoklu dosya) VM'e senkronlar."""
    ok = True
    for rel in PROXY_FILES:
        good = put_file(pathlib.Path(base, rel), f"{PROXY_DIR}/{rel}")
        ok &= good
        print(f"{'✅' if good else '❌'} sync {rel}")
    return ok


LOADGEN_DIR = f"{VM_HOME}/loadgen"


def sync_loadgen():
    here = pathlib.Path(__file__).resolve().parent
    ok = all(put_file(here / f, f"{LOADGEN_DIR}/{f}") for f in ("load_gen.py", "load_templates.py"))
    print(f"{'✅' if ok else '❌'} sync load_gen.py + load_templates.py → {LOADGEN_DIR}")
    return ok


def run_loadgen(args, timeout=300):
    """VM'de yük üretecini çalıştırır. Dönüş: (rc, stdout, json_sonuç | None)."""
    import json
    rc, out = sh(f"cd {LOADGEN_DIR} && rm -f /tmp/load.json && python3 load_gen.py --json /tmp/load.json {args}", show=False, timeout=timeout)
    _, js = sh("cat /tmp/load.json 2>/dev/null", sudo=False, show=False)
    try: res = json.loads(js)
    except ValueError: res = None
    return rc, out, res


def cpu_snapshot():
    """VM'de ilgili süreçlerin kümülatif CPU tick'lerini {(ad, pid): tick} döndürür (fark alınarak çekirdek kullanımı hesaplanır)."""
    _, out = sh(r'''for p in /proc/[0-9]*; do c=$(cat $p/comm 2>/dev/null); case "$c" in n2proxy_master|open5gs-*|mongod|nr-gnb|nr-ue)
 f=$(sed 's/.*) //' $p/stat 2>/dev/null); set -- $f; echo "$c ${p#/proc/} $(( ${12} + ${13} ))";; esac; done; echo "HZ $(getconf CLK_TCK) NCPU $(nproc)"''', sudo=False, show=False)
    snap, hz, ncpu = {}, 100, 1
    for l in out.splitlines():
        f = l.split()
        if f[:1] == ["HZ"]: hz, ncpu = int(f[1]), int(f[3])
        elif len(f) == 3: snap[(f[0], f[1])] = int(f[2])
    return snap, hz, ncpu


def cpu_cores(before, after, wall):
    """İki snapshot arasında süreç adı başına ortalama çekirdek kullanımı (1.0 = 1 tam çekirdek)."""
    (b, hz, ncpu), (a, _, _) = before, after
    use = {}
    for k, v in a.items():
        use[k[0]] = use.get(k[0], 0) + (v - b.get(k, v)) / hz / wall
    return {k: round(v, 2) for k, v in sorted(use.items(), key=lambda kv: -kv[1])}, ncpu


def journal_errors(units, since_epoch, top=3):
    """Verilen systemd birimlerinde since_epoch'tan beri hata/uyarı sayısı ve en sık mesajlar ({birim: (sayı, [(adet, mesaj)])})."""
    res = {}
    for u in units:
        _, out = sh(f"journalctl -u {u} --since @{int(since_epoch)} --no-pager -o cat 2>/dev/null | grep -iE 'error|fail|warn|overload|reject|timeout' "
                    r"| sed -E 's/\x1b\[[0-9;]*m//g; s/^[0-9\/]+ [0-9:.]+: //; s/[0-9a-f]{8}-[0-9a-f-]{27}/<uuid>/g; s/[0-9]+/N/g' | cut -c1-150 | sort | uniq -c | sort -rn | head -" + str(top),
                    sudo=False, show=False)
        rows = []
        for l in out.splitlines():
            l = l.strip()
            if l and l.split()[0].isdigit(): rows.append((int(l.split()[0]), l.split(None, 1)[1] if len(l.split(None, 1)) > 1 else ""))
        res[u] = (sum(n for n, _ in rows), rows)
    return res


def wait_quiet(names=("open5gs-ausfd", "open5gs-udmd", "open5gs-udrd", "open5gs-amfd", "mongod"), max_s=90, thresh=0.05, show=True):
    """Yük testinden sonra 5GC zincirinin sakinleşmesini bekler (her süreç < thresh çekirdek, 3 sn üst üste); sonraki senaryo kalıntıdan etkilenmesin."""
    quiet = 0; t0 = time.time()
    prev = cpu_snapshot(); tp = time.time()
    while time.time() - t0 < max_s:
        time.sleep(3)
        cur = cpu_snapshot(); now = time.time()
        cores, _ = cpu_cores(prev, cur, now - tp); prev, tp = cur, now
        busy = {k: v for k, v in cores.items() if k in names and v >= thresh}
        quiet = quiet + 1 if not busy else 0
        if quiet >= 3:
            if show: print(f"   ℹ  5GC sakinleşti ({round(time.time() - t0)} sn)")
            return True
    if show: print(f"   ⚠  5GC {max_s} sn içinde sakinleşmedi: {busy}")
    return False


def metrics(port=9190):
    """Proxy'nin Prometheus çıktısını {metrik: değer} olarak döndürür (VM üzerinden)."""
    _, body = sh(f"python3 -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:{port}/metrics',timeout=3).read().decode())\"", sudo=False, show=False)
    m = {}
    for l in body.splitlines():
        if l and not l.startswith("#") and " " in l:
            k, v = l.rsplit(" ", 1)
            try: m[k] = float(v)
            except ValueError: pass
    return m


def build_proxy():
    rc, out = sh(f"cd {PROXY_DIR} && g++ -std=c++17 -O2 -Wall -Wextra -o {PROXY_BIN} src/n2proxy_master.cpp -lsctp 2>&1 && echo BUILD_OK",
                 sudo=False, show=False)
    ok = "BUILD_OK" in out
    print("✅ derlendi" if ok else f"❌ derleme hatası:\n{out}")
    return ok


def start_proxy(env=""):
    """env: örn. 'N2P_ON_AMF_LOSS=silent N2P_HB_MS=500'"""
    rc, out = sh(f"""
pkill -9 -x n2proxy_master; sleep 1
ip addr show lo | grep -q 127.0.0.50 || ip addr add 127.0.0.50/8 dev lo
: > {PROXY_LOG}
nohup env {env} {PROXY_BIN} > {PROXY_LOG} 2>&1 < /dev/null &
sleep 2
pgrep -x n2proxy_master >/dev/null && echo PROXY_UP
""", show=False)
    ok = "PROXY_UP" in out
    print("✅ proxy çalışıyor" if ok else "❌ proxy başlamadı")
    return ok


def proxy_ctl(*cmds):
    """Çalışan proxy'ye komut gönderir (drain N | undrain N | status). Proxy komut dosyasını ~200 ms'de bir okur."""
    sh("\n".join(f"echo {shlex.quote(c)} >> /tmp/n2proxy.ctl" for c in cmds), show=False)
    time.sleep(0.6)


def proxy_log():
    return sh(f"cat {PROXY_LOG}", sudo=False, show=False)[1]


# ------------------------------------------------------------------ UERANSIM profilleri (proxy modu)
def ensure_proxy_profiles(gnbs=("a", "b", "c")):
    """gnb-X.yaml -> gnb-X-proxy.yaml (amfConfigs yalnızca proxy). İdempotent."""
    return sh(f"""
python3 - <<'P'
import yaml
for g in {list(gnbs)!r}:
    c = yaml.safe_load(open("{CFG}/gnb-%s.yaml" % g))
    c["amfConfigs"] = [{{"address": "127.0.0.50", "port": 38412}}]
    yaml.safe_dump(c, open("{CFG}/gnb-%s-proxy.yaml" % g, "w"), sort_keys=False)
    print("gnb-%s-proxy.yaml ok" % g)
P
""", show=False)[1]


# UE profili -> bağlandığı gNB (daily-ops hücre 5.0'daki eşleme)
UE_GNB = {"s1": "a", "s3": "a", "s1sd1": "b", "s2": "c"}


def start_gnb(g, proxy=True):
    cfg = f"gnb-{g}-proxy.yaml" if proxy else f"gnb-{g}.yaml"
    rc, out = sh(f"""
nohup /opt/UERANSIM/build/nr-gnb -c {CFG}/{cfg} > /tmp/gnb-{g}.log 2>&1 < /dev/null &
for i in $(seq 1 15); do grep -qE "NG Setup (procedure is successful|failure)" /tmp/gnb-{g}.log && break; sleep 1; done
grep -E "NG Setup (procedure is successful|failure)" /tmp/gnb-{g}.log | tail -1
""", show=False)
    return "successful" in out


def start_ues_parallel(ues, wait=25):
    """UE'leri aynı anda başlatır (RAN_UE_NGAP_ID yarışı/eşzamanlılık). Dönüş: {ue: PDU_OK mı}."""
    launch = "\n".join(f"nohup ip netns exec ns-{u} /opt/UERANSIM/build/nr-ue -c {CFG}/ue-{u}.yaml > /tmp/ue-{u}.log 2>&1 < /dev/null &" for u in ues)
    chk = " ".join(ues)
    rc, out = sh(f"""
{launch}
for i in $(seq 1 {wait}); do
  n=0; for u in {chk}; do grep -q "PDU Session establishment is successful" /tmp/ue-$u.log 2>/dev/null && n=$((n+1)); done
  [ $n -eq {len(ues)} ] && break; sleep 1
done
for u in {chk}; do grep -q "PDU Session establishment is successful" /tmp/ue-$u.log 2>/dev/null && echo "$u OK" || echo "$u FAIL"; done
""", show=False, timeout=wait + 60)
    return {l.split()[0]: l.split()[1] == "OK" for l in out.splitlines() if l.endswith(("OK", "FAIL"))}


def start_ue(u, wait=15):
    rc, out = sh(f"""
nohup ip netns exec ns-{u} /opt/UERANSIM/build/nr-ue -c {CFG}/ue-{u}.yaml > /tmp/ue-{u}.log 2>&1 < /dev/null &
for i in $(seq 1 {wait}); do grep -q "PDU Session establishment is successful" /tmp/ue-{u}.log && break; sleep 1; done
grep -q "PDU Session establishment is successful" /tmp/ue-{u}.log && echo PDU_OK || {{ echo PDU_FAIL; tail -3 /tmp/ue-{u}.log; }}
""", show=False, timeout=wait + 60)
    return "PDU_OK" in out, out
