#!/usr/bin/env python3
"""Generates local_vm_daily_ops_v1.ipynb (Mac -> local Ubuntu VM, N2 LB Proxy lab)."""
import json, os

cells = []
def md(s): cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)})
def code(s): cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                           "source": s.strip("\n").splitlines(True)})

md("""
# 5G AI/ML Lab — Local VM Daily Operations (N2 LB Proxy)
Platform: macOS Jupyter → `sshpass`/SSH (`localadmin@127.0.0.1:2222`) → local Ubuntu VM
Stack: Open5GS v2.7.6 (5GC + EPC) · UERANSIM · N2 LB Proxy (C++/epoll, `127.0.0.50:38412`, kaynak `../proxy/`)
PLMN: 999/70 · AMF Pool: 3 (127.0.0.5 / .105 / .205) · gNB: 3 (127.0.0.11 / .12 / .13)

> Bu notebook `5g_ai_lab_daily_ops_v4` (GCP/Colab/NWDAF) temel alınarak yerel VM'e uyarlanmıştır.
> GCP auth, Colab, NWDAF ve ML bölümleri çıkarılmış; AMF havuzu, N2 proxy ve çoklu gNB operasyonları eklenmiştir.
> Durum/yol haritası: `PROJECT_SUMMARY_AND_ROADMAP.md` (Faz A–E tamam: proxy `../proxy/`, assert'li senaryolar `01_N2_Master_Proxy_Lab_v2.ipynb`).
> Bu notebook günlük operasyon içindir; proxy regresyon/chaos testleri için `01_N2_Master_Proxy_Lab_v2.ipynb` + `chaos_suite.py` kullanın.

**İçindekiler:** 1 Bootstrap · 2 Dashboard · 3 Servis kontrol · 4 AMF havuzu · 5 UERANSIM · 6 N2 Proxy · 7 Loglar · 8 Aboneler · 9 Config · 10 Teşhis & Onarım · 11 Yedek · 12 Roadmap · 13 Referans
""")

# ---------------------------------------------------------------- 1
md("# 1. Bootstrap")
code(r'''
import subprocess, os, time, shlex

VM_USER = "localadmin"
VM_HOST = "127.0.0.1"
VM_PORT = "2222"          # QEMU/UTM port-forward -> VM:22
VM_PASS = os.environ.get("VM_PASS", "open5gs")   # sudo parolası da aynı

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8"]
_ENV = {**os.environ, "SSHPASS": VM_PASS, "SSH_ASKPASS_REQUIRE": "never"}

SSH_OPTS += ["-o", "ControlMaster=auto", "-o", "ControlPersist=120",
             "-o", f"ControlPath=/tmp/ssh-vm-{VM_PORT}"]   # tek bağlantıyı yeniden kullan (auth hatalarını önler)

def _ssh(remote_cmd, timeout=120, retries=3):
    for i in range(retries):
        r = subprocess.run(["sshpass", "-e", "ssh", *SSH_OPTS, "-p", VM_PORT,
                            f"{VM_USER}@{VM_HOST}", remote_cmd],
                           capture_output=True, text=True, env=_ENV, timeout=timeout)
        if r.returncode == 255 and "Permission denied" in r.stderr:   # geçici auth hatası
            time.sleep(1.5); continue
        break
    return r

def ssh_run(cmd, sudo=False, timeout=120, show=True):
    """Tek satır komut. sudo=True ise sudo -S ile çalışır."""
    if sudo:
        cmd = f"echo {shlex.quote(VM_PASS)} | sudo -S -p '' bash -c {shlex.quote(cmd)}"
    r = _ssh(cmd, timeout)
    if show:
        if r.stdout: print(r.stdout.rstrip())
        if r.returncode != 0 and r.stderr: print(f"[STDERR] {r.stderr[:600].rstrip()}")
    return r

def ssh_script(script, description="", timeout=180, sudo=True):
    """Çok satırlı bash scripti VM'e stdin ile verir (SCP gerekmez, $ escape gerekmez)."""
    if description: print(f"▶ {description}")
    runner = f"echo {shlex.quote(VM_PASS)} | sudo -S -p '' bash -s" if sudo else "bash -s"
    # sudo -S parolayı stdin'den okur; script'i base64 ile geçirerek stdin çakışmasını önle
    import base64
    b64 = base64.b64encode(("set +e\n" + script).encode()).decode()
    cmd = f"echo {b64} | base64 -d > /tmp/_ops.sh; " + \
          (f"echo {shlex.quote(VM_PASS)} | sudo -S -p '' bash /tmp/_ops.sh" if sudo else "bash /tmp/_ops.sh") + \
          "; rm -f /tmp/_ops.sh"
    r = _ssh(cmd, timeout)
    if r.stdout: print(r.stdout.rstrip())
    if r.stderr.strip(): print(f"[STDERR] {r.stderr[:800].rstrip()}")
    return r

# --- VM çalışıyor mu? ---
r = ssh_run("hostname; uptime -p; ip -br a show enp0s1 | awk '{print $1,$3}'", show=False)
print(("✅ VM erişilebilir:\n" + r.stdout) if r.returncode == 0 else
      f"❌ VM'e bağlanılamadı (port {VM_PORT}). VM açık mı / port-forward var mı?\n{r.stderr}")
''')

md("> **VM yeniden başlatıldıysa:** `br-ue` köprüsü, `ns-*` namespace'leri ve `lo` alias'ı (127.0.0.50) kalıcı değildir. Sırayla 5.0 → 5.0b → 5.0c (idempotent) ve bölüm 6.2'yi çalıştırın; dashboard'daki `köprü br-ue: ❌` bunun işaretidir.")

# ---------------------------------------------------------------- 2
md("# 2. Dashboard\nTüm NF'ler, AMF havuzu, SCTP dinleyicileri, UERANSIM, proxy, PDU session ve kaynaklar.")
code(r'''
DASHBOARD = r"""
line(){ echo "────────── $1 ──────────"; }
echo "════ N2 LB PROXY LAB — HEALTH DASHBOARD  $(date '+%F %T') ════"

line "5GC çekirdek NF"
for s in nrfd scpd seppd ausfd udmd udrd pcfd nssfd bsfd smfd upfd; do
  st=$(systemctl is-active open5gs-$s); [ "$st" = active ] && i=✅ || i=❌
  printf "  %s %-8s %s\n" $i $s $st
done
line "EPC (4G) NF"
for s in mmed sgwcd sgwud hssd pcrfd; do
  st=$(systemctl is-active open5gs-$s); [ "$st" = active ] && i=✅ || i=❌
  printf "  %s %-8s %s\n" $i $s $st
done
line "AMF havuzu  (template: open5gs-amfd@<ad>)"
declare -A IP=( [amf]=127.0.0.5 [amf2]=127.0.0.105 [amf3]=127.0.0.205 )
declare -A CF=( [amf]=amf.yaml [amf2]=amf2.yaml [amf3]=amf3.yaml )
for a in amf amf2 amf3; do
  st=$(systemctl is-active open5gs-amfd@$a); [ "$st" = active ] && i=✅ || i=❌
  cap=$(grep -m1 relative_capacity /etc/open5gs/${CF[$a]} | awk '{print $2}')
  ptr=$(grep -m1 pointer /etc/open5gs/${CF[$a]} | awk '{print $2}')
  ng=$(ss -Snl | grep -c "${IP[$a]}:38412")
  printf "  %s %-5s %-14s cap=%-4s ptr=%-2s NGAP-listen=%s\n" $i $a ${IP[$a]} "$cap" "$ptr" $ng
done
line "UERANSIM"
echo "  📡 gNB: $(pgrep -x nr-gnb | wc -l)    📱 UE: $(pgrep -x nr-ue | wc -l)"
pgrep -a nr-gnb | sed 's/^/    /;s#/opt/UERANSIM/build/##'
line "N2 Proxy (127.0.0.50:38412)"
P=$(pgrep -x n2proxy_master | head -1)
[ -n "$P" ] && echo "  ✅ n2proxy_master PID=$P" || echo "  ⚪ proxy çalışmıyor"
ss -Snl | grep 127.0.0.50 | sed 's/^/    /'
line "SCTP assosiasyonları (NGAP :38412)"
ss -Sn | grep 38412 | awk '$5 !~ /^0|%lo/ {print "    "$4" <-> "$5}' | sort | head -12
line "PDU sessions (UE netns)"
n=0
ip link show br-ue >/dev/null 2>&1 && echo "  köprü br-ue: ✅" || echo "  köprü br-ue: ❌ (bölüm 5.0c'yi çalıştırın)"
for ns in $(ip netns list | awk '{print $1}' | grep '^ns-'); do
  IP=$(ip -n $ns -4 -br a show uesimtun0 2>/dev/null | awk '{print $3}')
  [ -n "$IP" ] && { echo "  ✅ $ns uesimtun0 $IP"; n=$((n+1)); }
done
[ $n -eq 0 ] && echo "  ⚠️  aktif PDU session yok"
line "Kaynaklar"
echo "  CPU load: $(cut -d' ' -f1-3 /proc/loadavg)   RAM: $(free -h | awk '/^Mem/{print $3"/"$2}')   Disk: $(df -h / | awk 'NR==2{print $3"/"$2" ("$5")"}')"
"""
_ = ssh_script(DASHBOARD, "Dashboard")
''')

# ---------------------------------------------------------------- 3
md("# 3. Servis Kontrol\n`GROUP`: `core` · `amf` · `epc` · `all`  —  `ACTION`: start/stop/restart/status")
code(r'''
ACTION = "status"     # start / stop / restart / status
GROUP  = "amf"        # core / amf / epc / all

GROUPS = {
    "core": "nrfd scpd seppd ausfd udmd udrd pcfd nssfd bsfd smfd upfd",
    "amf":  "amfd@amf amfd@amf2 amfd@amf3",
    "epc":  "mmed sgwcd sgwud hssd pcrfd",
}
GROUPS["all"] = " ".join(GROUPS[k] for k in ("core", "amf", "epc"))
svcs = " ".join("open5gs-" + s for s in GROUPS[GROUP].split())

_ = ssh_script(f"""
for s in {svcs}; do
  [ "{ACTION}" != status ] && systemctl {ACTION} $s
  printf "  %-28s %s\\n" $s $(systemctl is-active $s)
done
""", f"{ACTION} → {GROUP}")
''')

# ---------------------------------------------------------------- 4
md("""
# 4. AMF Havuzu
## 4.1 Konfig özeti (name / capacity / pointer / NGAP adresi)
""")
code(r'''
_ = ssh_script(r"""
for f in amf amf2 amf3; do
  echo "--- /etc/open5gs/$f.yaml ---"
  grep -E "amf_name|relative_capacity|pointer|region|set:|^\s+- addr: 127\.0\.0\.(5|105|205)|port: 38412" /etc/open5gs/$f.yaml | head -12
done
""", "AMF konfigleri")
''')
md("""
## 4.2 AMF çökme testi (kill -9 + otomatik toparlanma)
Hedef AMF'i öldürür ve **gNB'ler bağlıyken** SCTP assoc sayısını önce/sonra ölçer (direct modda baseline: 9 → 6; proxy modunda gNB başına AMF'lere 3 assoc + gNB→proxy 1). gNB yoksa uyarır.
`systemd Restart=` AMF'i birkaç sn içinde geri getirir; ölçüm kill'den hemen sonra (`SETTLE` sn) alınır. Proxy'li otomatik doğrulama için `01_N2_Master_Proxy_Lab_v2.ipynb`.
""")
code(r'''
TARGET = "amf2"     # amf / amf2 / amf3
RESTORE = True
SETTLE = 1          # kill sonrası ölçüm gecikmesi (sn) — systemd restart öncesi

_ = ssh_script(f"""
NG() {{ ss -Sn 2>/dev/null | grep -c ':38412'; }}
if [ "$(pgrep -x nr-gnb | wc -l)" -lt 3 ]; then
  echo "⚠️  gNB'ler çalışmıyor — 5.1 hücresini çalıştırın, aksi halde ölçüm anlamsız."; exit 0
fi
echo "[1] önce: servis=$(systemctl is-active open5gs-amfd@{TARGET})  NGAP assoc=$(NG)"
PID=$(systemctl show -p MainPID --value open5gs-amfd@{TARGET}); echo "    PID=$PID"
kill -9 $PID; sleep {SETTLE}
echo "[2] kill sonrası: servis=$(systemctl is-active open5gs-amfd@{TARGET})  NGAP assoc=$(NG)"
sleep 6
echo "[3] systemd toparlama sonrası: servis=$(systemctl is-active open5gs-amfd@{TARGET})"
[ "{RESTORE}" = "True" ] && systemctl restart open5gs-amfd@{TARGET} && sleep 2 && echo "[4] restart: $(systemctl is-active open5gs-amfd@{TARGET})"
""", f"{TARGET} kill -9 testi")
''')

# ---------------------------------------------------------------- 5
md("""
# 5. UERANSIM (gNB & UE)
Profiller: `/opt/UERANSIM/config/n2lab/` — gnb-a/b/c (127.0.0.11/.12/.13), ue-s1 (gNB-A), ue-s1sd1 (gNB-B), ue-s2 (gNB-C), ue-s3 (gNB-A)
""")
md("## 5.0 UE profil onarımı (bir kez çalıştırın, idempotent)\nEksik `integrity/ciphering/nssai` alanlarını `open5gs-ue.yaml` şablonundan tamamlar.")
code(r'''
FIX_UE = r"""
import yaml, os, shutil
D="/opt/UERANSIM/config/n2lab"
tpl=yaml.safe_load(open("/opt/UERANSIM/config/open5gs-ue.yaml"))
M={"ue-s1":(1,None,"internet","10.201.0.11"),"ue-s1sd1":(1,"000001","internet","10.201.0.12"),"ue-s2":(2,None,"urllc","10.201.0.13"),"ue-s3":(3,None,"iot","10.201.0.11")}
os.makedirs(D+"/orig",exist_ok=True)
for n,(sst,sd,apn,gnb) in M.items():
    p=f"{D}/{n}.yaml"; cur=yaml.safe_load(open(p))
    if not os.path.exists(f"{D}/orig/{n}.yaml"): shutil.copy(p,f"{D}/orig/{n}.yaml")
    c=dict(tpl)
    for k in ("supi","mcc","mnc","key","op","opType","amf","imei","imeiSv"): c[k]=cur[k]
    c["gnbSearchList"]=[gnb]
    sl={"sst":sst}
    if sd: sl["sd"]=sd
    c["configured-nssai"]=[dict(sl)]; c["default-nssai"]=[dict(sl)]; c["requestedNSSAI"]=[dict(sl)]; c.pop("defaultNSSAI",None)
    c["sessions"]=[{"type":"IPv4","apn":apn,"slice":dict(sl)}]
    yaml.safe_dump(c,open(p,"w"),sort_keys=False); print("fixed",n,sl)
"""
import base64
_ = ssh_script("echo %s | base64 -d | python3" % base64.b64encode(FIX_UE.encode()).decode(), "UE profilleri onarılıyor")
''')
md("## 5.0b Slice uyumu onarımı (bir kez, idempotent)\nSorun: abone DB'de `sd=000000` iken AMF slice'ı SD'siz (0xffffff) tanımlı → `Cannot find Requested NSSAI` ile Registration Reject; SMF yalnızca SST1 SD'siz ve `internet/ims` DNN'lerini tanıyordu → PDU session açılmıyordu (urllc/iot için SMF `session` havuzları da eklenir). Düzeltme: abonelerden `sd` kaldırılır, SMF `info.s_nssai`'e sst1/sd000001, sst2, sst3 eklenir (yedek: `smf.yaml.pre_slicefix`).")
code(r'''
FIX_SLICE = r"""
cp -n /etc/open5gs/smf.yaml /etc/open5gs/smf.yaml.pre_slicefix
python3 - <<'P'
import yaml
p="/etc/open5gs/smf.yaml"; c=yaml.safe_load(open(p))
changed=False
L=c["smf"]["info"][0]["s_nssai"]
for sst,sd,dnn in [(1,"000001",["internet","ims"]),(2,None,["urllc"]),(3,None,["iot"])]:
    e=next((x for x in L if x["sst"]==sst and x.get("sd")==sd),None)
    if e is None:
        e={"sst":sst}; 
        if sd: e["sd"]=sd
        L.append(e)
    if e.get("dnn")!=dnn: e["dnn"]=dnn; changed=True
sess=c["smf"]["session"]
for dnn,gw,sub in [("urllc","10.46.0.1","10.46.0.0/16"),("iot","10.47.0.1","10.47.0.0/16")]:
    if not any(x.get("dnn")==dnn for x in sess):
        sess.append({"dnn":dnn,"gateway":gw,"subnet":sub}); changed=True
if changed: yaml.safe_dump(c,open(p,"w"),sort_keys=False)
open("/tmp/smf_changed","w").write("1" if changed else "0")
print("smf changed:",changed)
P
mongosh --quiet open5gs --eval 'db.subscribers.updateMany({imsi:{$in:["999700000000101","999700000000103","999700000000104"]}},{$unset:{"slice.0.sd":""}}); db.subscribers.find({},{imsi:1,slice:1}).forEach(s=>print(s.imsi,JSON.stringify(s.slice.map(x=>[x.sst,x.sd]))))'
[ "$(cat /tmp/smf_changed)" = 1 ] && { systemctl restart open5gs-upfd; sleep 2; systemctl restart open5gs-smfd; sleep 3; }; echo smfd: $(systemctl is-active open5gs-smfd)
"""
_ = ssh_script(FIX_SLICE, "Slice / SMF / abone onarımı")
''')
md("## 5.0c UE ağ izolasyonu (netns + köprü) — bir kez, idempotent\nUE'ler UPF ile aynı host'ta çalışınca `uesimtun` IP'si 'yerel adres' sayılıp dönüş paketi düşüyordu. Çözüm: her UE kendi netns'inde (`ns-<profil>`), `br-ue` köprüsü (10.201.0.0/24) üzerinden gNB radio-link IP'lerine (10.201.0.11/.12/.13) bağlanır. gNB NGAP/GTP IP'leri (127.0.0.1x) değişmez; yalnızca `linkIp` köprüye taşınır (orijinaller `n2lab/orig/`).")
code(r'''
NETNS_SCRIPT = r"""
BR=br-ue
ip link show $BR >/dev/null 2>&1 || { ip link add $BR type bridge; }
ip link set $BR up
for a in 11 12 13; do ip addr show dev $BR | grep -q "10.201.0.$a/" || ip addr add 10.201.0.$a/24 dev $BR; done
i=21
for p in s1 s1sd1 s2 s3; do
  NS=ns-$p; VH=vh-$p; VN=vn-$p
  ip netns list | grep -qw $NS || ip netns add $NS
  if ! ip link show $VH >/dev/null 2>&1; then
    ip link add $VH type veth peer name $VN
    ip link set $VN netns $NS
    ip link set $VH master $BR
  fi
  ip link set $VH up
  ip -n $NS addr show dev $VN | grep -q "10.201.0.$i/" || ip -n $NS addr add 10.201.0.$i/24 dev $VN
  ip -n $NS link set $VN up; ip -n $NS link set lo up
  i=$((i+1))
done
mkdir -p /opt/UERANSIM/config/n2lab/orig
for g in a b c a-proxy; do
  f=/opt/UERANSIM/config/n2lab/gnb-$g.yaml; [ -f $f ] || continue
  [ -f /opt/UERANSIM/config/n2lab/orig/gnb-$g.yaml ] || cp $f /opt/UERANSIM/config/n2lab/orig/gnb-$g.yaml
  case $g in a|a-proxy) L=10.201.0.11;; b) L=10.201.0.12;; c) L=10.201.0.13;; esac
  sed -i "s/^linkIp:.*/linkIp: $L/" $f
done
sysctl -w net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0 net.ipv4.conf.ogstun.rp_filter=0 >/dev/null
[ -f /etc/sysctl.d/99-n2lab.conf ] && sed -i '/rp_filter/d' /etc/sysctl.d/99-n2lab.conf; echo "net.ipv4.conf.all.rp_filter = 0" >> /etc/sysctl.d/99-n2lab.conf; echo "net.ipv4.conf.default.rp_filter = 0" >> /etc/sysctl.d/99-n2lab.conf
echo "köprü:"; ip -br a show $BR | cut -c1-90
for p in s1 s1sd1 s2 s3; do echo "ns-$p: $(ip -n ns-$p -br a show vn-$p | awk '{print $3}')  → gNB link test: $(ip netns exec ns-$p ping -c1 -W1 10.201.0.11 >/dev/null 2>&1 && echo OK || echo FAIL)"; done
grep -H linkIp /opt/UERANSIM/config/n2lab/gnb-*.yaml
"""
_ = ssh_script(NETNS_SCRIPT, "UE netns + köprü kurulumu")
''')
md("## 5.1 gNB/UE başlat (MODE: `direct` = üç AMF'e doğrudan, `proxy` = N2 proxy üzerinden — önce bölüm 6.1/6.2 ile proxy'yi başlatın; bölüm 6.4 aynı işlevi `proxy` modunda çağırır)")
code(r'''
def start_ran(MODE="direct", GNBS=("a", "b", "c"), UES=("s1", "s1sd1", "s2", "s3"), CLEAN_FIRST=True):
    """gNB'leri ve UE'leri başlatır. MODE: direct | proxy (proxy'de gnb-X-proxy.yaml otomatik üretilir, idempotent)."""
    if MODE == "proxy":
        import n2lab_ops as ops; ops.ensure_proxy_profiles(tuple(GNBS))
    gnb_cfg = lambda g: f"gnb-{g}-proxy.yaml" if MODE == "proxy" else f"gnb-{g}.yaml"
    gnb_cmds = "\n".join(
        f"nohup /opt/UERANSIM/build/nr-gnb -c /opt/UERANSIM/config/n2lab/{gnb_cfg(g)} > /tmp/gnb-{g}.log 2>&1 < /dev/null &\nsleep 2"
        for g in GNBS)
    ue_cmds = "\n".join(
        f"nohup ip netns exec ns-{u} /opt/UERANSIM/build/nr-ue -c /opt/UERANSIM/config/n2lab/ue-{u}.yaml > /tmp/ue-{u}.log 2>&1 < /dev/null &\nsleep 3"
        for u in UES)
    return ssh_script(f"""
{"pkill -x nr-ue; sleep 1; pkill -9 -x nr-ue; pkill -x nr-gnb; sleep 8; pkill -9 -x nr-gnb" if CLEAN_FIRST else ""}
{gnb_cmds}
echo "--- NG Setup sonuçları ---"
for g in {" ".join(GNBS)}; do echo -n "gNB-$g: "; grep -E "NG Setup (procedure is successful|failure)" /tmp/gnb-$g.log | tail -1 || echo "?"; done
{ue_cmds}
sleep 6
echo "--- UE kayıt sonuçları ---"
for u in {" ".join(UES)}; do echo -n "UE-$u: "; (grep -E "ERROR|Registration|PDU Session establishment is successful|TUN interface" /tmp/ue-$u.log | tail -2 || tail -2 /tmp/ue-$u.log) | tr '\\n' ' '; echo; done
echo; echo "gNB=$(pgrep -x nr-gnb | wc -l) UE=$(pgrep -x nr-ue | wc -l)"; for u in {" ".join(UES)}; do echo "ns-$u: $(ip -n ns-$u -4 -br a show uesimtun0 2>/dev/null | awk '{{print $1,$3}}')"; done
""", f"UERANSIM başlatılıyor (mode={MODE})", timeout=240)

MODE = "direct"                    # direct | proxy
_ = start_ran(MODE)
''')
md("## 5.2 nr-cli — UE/gNB durumu ve ping (UE'ler çalışırken; sonra 5.3 ile durdurun)")
code(r'''
_ = ssh_script(r"""
if [ "$(pgrep -x nr-gnb | wc -l)" -eq 0 ] && [ "$(pgrep -x nr-ue | wc -l)" -eq 0 ]; then
  echo "ℹ️  gNB/UE çalışmıyor (5.2 durdurmuş olabilir). Önce 5.1 hücresini çalıştırın."; exit 0
fi
cd /opt/UERANSIM/build
echo "=== gNB listesi ==="; ./nr-cli --dump
echo; echo "=== gNB durumları ==="
for n in $(./nr-cli --dump | grep -i gnb); do timeout 5 ./nr-cli $n --exec status 2>/dev/null | head -8; done
echo "=== UE'ler (her biri kendi netns'inde) ==="
for p in s1 s1sd1 s2 s3; do
  echo "-- ns-$p"
  ip netns exec ns-$p ./nr-cli --dump 2>/dev/null | grep imsi | while read n; do ip netns exec ns-$p timeout 5 ./nr-cli $n --exec ps-list 2>/dev/null | grep -E "state|apn|sst|sd" | tr -s ' ' | paste -sd' '; done
done
echo; echo "=== Data-plane testi: UE netns'inden ping (UPF → NAT → internet) ==="
for p in s1 s1sd1 s2 s3; do
  IP=$(ip -n ns-$p -4 -br a show uesimtun0 2>/dev/null | awk '{print $3}')
  [ -z "$IP" ] && { echo "  ❌ ns-$p: uesimtun0 yok (PDU session kurulmamış)"; continue; }
  R=$(ip netns exec ns-$p ping -c3 -W2 -I uesimtun0 8.8.8.8 2>/dev/null | awk '/received/{print $4}')
  [ "${R:-0}" -ge 1 ] && echo "  ✅ ns-$p $IP → 8.8.8.8  ($R/3)" || echo "  ❌ ns-$p $IP → 8.8.8.8 başarısız"
done
""", "nr-cli")
''')

md("## 5.3 Durdur")
code(r'''
_ = ssh_script("pkill -9 -x nr-ue; pkill -9 -x nr-gnb; sleep 1; echo \"gNB=$(pgrep -x nr-gnb|wc -l) UE=$(pgrep -x nr-ue|wc -l)\"", "gNB + UE durduruluyor")
''')
# ---------------------------------------------------------------- 6
md("""
# 6. N2 LB Proxy (Faz A–E)
Kaynak: **repoda** `../proxy/src/` (`n2proxy_master.cpp`, `ngap.h`, `lb_core.h`) → VM'de `~/n2proxy_src/` → binary `~/n2proxy_master` · log `~/proxy_master.log` · dinleme `127.0.0.50:38412` (lo alias)
Özellikler: gNB başına 3 AMF'e SCTP, `(gNB,RAN_UE_NGAP_ID)` UE tablosu, kapasite-ağırlıklı RR, 5G-S-TMSI affinity, AMF kaybında UE release, yeniden bağlanma + NGSetup replay, SCTP heartbeat, drain, opsiyonel canlılık yoklaması, Prometheus metrikleri.
Ayrıntı: `../proxy/README.md`. Yerel test: `make -C ../proxy test`.
## 6.1 Senkronla + derle
""")
code(r'''
import importlib, n2lab_ops as ops; importlib.reload(ops)
ops.sync_proxy() and ops.build_proxy()
''')
md("## 6.2 Başlat / Durdur / Yeniden başlat\n`PROXY_ENV`: `N2P_ON_AMF_LOSS=release|reset|silent` · `N2P_PROBE_MS=1500` (canlılık yoklaması; önce `probe_healthy` senaryosu PASS olmalı) · `N2P_METRICS=127.0.0.1:9190` · `N2P_HB_MS` · `N2P_SETUP_TIMEOUT_MS`")
code(r'''
PROXY_ACTION = "restart"    # start | stop | restart | status
PROXY_ENV = "N2P_METRICS=127.0.0.1:9190"

if PROXY_ACTION in ("stop", "restart"):
    _ = ssh_script("pkill -9 -x n2proxy_master; sleep 1; echo durduruldu", "proxy durdur")
if PROXY_ACTION in ("start", "restart"):
    ops.start_proxy(PROXY_ENV)
_ = ssh_script("""
P=$(pgrep -x n2proxy_master | head -1)
[ -n "$P" ] && echo "✅ çalışıyor PID=$P  RSS=$(awk '/VmRSS/{print $2" kB"}' /proc/$P/status)" || echo "⚪ çalışmıyor"
ss -Snl | grep 127.0.0.50 || true
""", "proxy durumu")
''')
md("## 6.3 Durum, drain / undrain, metrikler\nKomutlar proxy'nin `/tmp/n2proxy.ctl` dosyasından okunur (≈200 ms). `drain N`: AMF-N yeni UE almaz (bakım/rolling restart); sahibi olduğu dönen UE'leri (S-TMSI) almaya devam eder.")
code(r'''
CTL = ["status"]            # örn. ["drain 2", "status"]  ·  ["undrain 2"]

ops.proxy_ctl(*CTL)
_ = ssh_script("grep '\\[CTL\\]' /home/localadmin/proxy_master.log | tail -8", "proxy durumu (CTL)", sudo=False)
m = ops.metrics()
for k in sorted(m):
    if k.startswith(("n2_ue_contexts", "n2_gnb_connections", "n2_lb_assignments_total", "n2_dropped_total", "n2_ha_events_total")) and m[k]:
        print(f"  {k} = {m[k]:g}")
''')
md("## 6.4 Proxy modunda uçtan uca (3 gNB + 4 UE proxy üzerinden)\nBölüm 6.2'de proxy çalışıyor olmalı. gNB'ler yalnızca `127.0.0.50`'ye bağlanır; proxy olayları, durum ve metrikler gösterilir.")
code(r'''
_ = start_ran("proxy")
_ = ssh_script("grep -E '\\[LB\\]|\\[HA\\]|\\[ALARM\\]|\\[WARN\\]' /home/localadmin/proxy_master.log | tail -20", "proxy olayları", sudo=False)
ops.proxy_ctl("status")
_ = ssh_script("grep '\\[CTL\\]' /home/localadmin/proxy_master.log | tail -5", "proxy durumu (CTL)", sudo=False)
m = ops.metrics()
print("mesaj korunumu:", sum(v for k, v in m.items() if k.startswith(("n2_messages_total{dir=\"gnb", "n2_messages_total{dir=\"amf", "n2_messages_consumed_total{kind=\"mcast", "n2_messages_consumed_total{kind=\"inj"))), "==",
      sum(v for k, v in m.items() if k.startswith(("n2_messages_total{dir=\"proxy", "n2_messages_consumed_total{kind=\"swal", "n2_dropped_total"))))
''')
md("## 6.5 SCTP/NGAP pcap — kayıt akışı dahil\nYakalama arka planda başlar, ardından gNB/UE'ler proxy modunda (5.1'deki `start_ran`) başlatılır; böylece NGSetup/InitialUE/NAS akışı pcap'e girer (yalnızca kararlı durumda yakalanırsa SCTP heartbeat'lerinden başka bir şey görünmez). Bölüm 6.2'de proxy çalışıyor olmalı; NGAP çözümü için `tshark` (6.5b).")
code(r'''
DURATION = 60    # sn yakalama penceresi (start_ran ≈ 25-30 sn sürer)
PCAP = "/home/localadmin/n2lab_pcaps/n2_latest.pcap"
_ = ssh_script(f"""
mkdir -p /home/localadmin/n2lab_pcaps; rm -f {PCAP}
nohup timeout {DURATION} tcpdump -i lo -U -w {PCAP} sctp > /dev/null 2>&1 < /dev/null &
sleep 1; echo "tcpdump başladı ({DURATION} sn)"
""", "pcap başlat")
_ = start_ran("proxy")
_ = ssh_script(f"""
sleep 2; pkill -x tcpdump; sleep 1
ls -la {PCAP}
if which tshark >/dev/null 2>&1; then
  echo "=== NGAP prosedür sayıları (kaynak → hedef, procedureCode: 21=NGSetup 15=InitialUE 46=UplinkNAS 4=DownlinkNAS 14=InitialContextSetup 29=PDUSessionResourceSetup 41=UEContextRelease) ==="
  tshark -r {PCAP} -Y ngap -T fields -e ip.src -e ip.dst -e ngap.procedureCode 2>/dev/null | sort | uniq -c | sort -rn | head -25
  echo "=== ilk 25 NGAP mesajı ==="
  tshark -r {PCAP} -Y ngap -t r 2>/dev/null | cut -c1-150 | head -25
else echo "(tshark yok — 6.5b ile kurun)"; tcpdump -nn -r {PCAP} 2>/dev/null | head -20; fi
""", "pcap çözümü", timeout=120)
''')

md("### 6.5b tshark kurulumu (opsiyonel, bir kez)\nNGAP'ı mesaj düzeyinde görmek için. VM'in internet erişimi gerekir; `INSTALL = True` yapın.")
code(r'''
INSTALL = False
if INSTALL:
    _ = ssh_script("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tshark >/dev/null 2>&1; which tshark && tshark --version | head -1 || echo 'kurulamadı (ağ erişimi?)'", "tshark kurulumu", timeout=300)
else:
    print("INSTALL=True yaparak kurun (6.5 hücresi tshark varsa NGAP çözümü gösterir).")
''')

# ---------------------------------------------------------------- 7
md("# 7. Log İnceleme")
code(r'''
NF   = "amf"      # amf | amf2 | amf3 | smf | upf | nrf | ... (open5gs-<nf>d)
LINES = 30
unit = f"open5gs-amfd@{NF}" if NF.startswith("amf") else f"open5gs-{NF}d"
_ = ssh_script(f"""
echo "=== {unit} (son {LINES}) ==="; journalctl -u {unit} -n {LINES} --no-pager -o cat
echo; echo "=== proxy_master.log ==="; tail -n 15 /home/localadmin/proxy_master.log
echo; echo "=== gNB logları ==="; for g in a b c a-proxy; do [ -f /tmp/gnb-$g.log ] && {{ echo "-- gnb-$g"; tail -n 4 /tmp/gnb-$g.log; }}; done
""", "Loglar")
''')
code(r'''
# Son 1 saatlik hata özeti (tüm NF)
_ = ssh_script(r"""
for u in $(systemctl list-units 'open5gs-*' --no-legend --plain | awk '{print $1}'); do
  EX='sepp2\.localdomain|Connection refused.*7777'
  case $u in *seppd*) EX="$EX|ogs_sbi_client_handler\(\) failed";; esac
  L=$(journalctl -u $u --since '1 hour ago' --no-pager -o cat 2>/dev/null | grep -iE 'error|fail|ERR\]|FATAL')
  n=$(printf '%s\n' "$L" | grep -vE "$EX" | grep -c .)
  k=$(printf '%s\n' "$L" | grep -cE "$EX")
  [ "$k" -gt 0 ] && printf "  ℹ️  %-34s %s (bilinen zararsız: SEPP2 yok)\n" $u $k
  [ "$n" -gt 0 ] && printf "  ⚠️  %-34s %s hata/uyarı\n" $u $n
done; echo "(liste sonu)"
""", "Hata özeti (1 saat)")
''')

# ---------------------------------------------------------------- 8
md("# 8. Abone Yönetimi (MongoDB)")
code(r'''
_ = ssh_script(r"""
mongosh --quiet open5gs --eval '
db.subscribers.find({}, {imsi:1, "slice.sst":1, "slice.sd":1, _id:0}).forEach(s =>
  print(s.imsi, JSON.stringify(s.slice.map(x => ({sst:x.sst, sd:x.sd})))));
print("Toplam:", db.subscribers.countDocuments())'
""", "Abone listesi", sudo=False)
''')
md("Open5GS WebUI: VM'de `open5gs-webui` (port 9999). Mac'ten erişim için: `ssh -L 9999:127.0.0.1:9999 -p 2222 localadmin@127.0.0.1` → http://localhost:9999")

# ---------------------------------------------------------------- 9
md("# 9. Konfigürasyon (gNB / UE / NRF kaydı)")
code(r'''
_ = ssh_script(r"""
echo "=== gNB profilleri ==="
for f in /opt/UERANSIM/config/n2lab/gnb-*.yaml; do echo "--- $(basename $f)"; grep -E "nci|linkIp|address|port|sst" $f | tr -s ' ' | paste -sd' '; done
echo; echo "=== UE profilleri ==="
for f in /opt/UERANSIM/config/n2lab/ue-*.yaml; do echo "--- $(basename $f): $(grep -E 'supi' $f) | gnb: $(grep -A1 gnbSearchList $f | tail -1 | tr -d ' -')"; done
echo; echo "=== NRF'e kayıtlı NF'ler ==="
for id in $(curl -s --http2-prior-knowledge http://127.0.0.10:7777/nnrf-nfm/v1/nf-instances | python3 -c "
import sys,json
print(' '.join(l['href'].split('/')[-1] for l in json.load(sys.stdin).get('_links',{}).get('item',[])))"); do
  curl -s --http2-prior-knowledge http://127.0.0.10:7777/nnrf-nfm/v1/nf-instances/$id | python3 -c "
import sys,json
d=json.load(sys.stdin); print('  %-8s %-11s %s' % (d.get('nfType'), d.get('nfStatus'), d.get('nfInstanceId')[:8]))"
done | sort
""", "Konfig & NRF", sudo=False)
''')

# ---------------------------------------------------------------- 10
md("# 10. Teşhis & Onarım")
md("## 10.1 Hızlı otomatik teşhis")
code(r'''
_ = ssh_script(r"""
ok(){ echo "  ✅ $1"; }; bad(){ echo "  ❌ $1  → $2"; }
for a in amf amf2 amf3; do systemctl is-active -q open5gs-amfd@$a && ok "AMF $a aktif" || bad "AMF $a" "section 3: restart"; done
for p in 127.0.0.5 127.0.0.105 127.0.0.205; do ss -Snl | grep -q "$p:38412" && ok "NGAP $p dinliyor" || bad "NGAP $p yok" "AMF restart"; done
systemctl is-active -q open5gs-upfd && ok "UPF aktif" || bad UPF "restart upfd"
ip link show ogstun >/dev/null 2>&1 && ok "ogstun var" || bad ogstun "ogstun yeniden oluştur"
[ "$(sysctl -n net.ipv4.ip_forward)" = 1 ] && ok "ip_forward=1" || bad "ip_forward=0" "10.2 NAT onarımı"
iptables -t nat -S | grep -q 10.46.0.0 && ok "NAT kuralları var" || bad "NAT kuralı eksik" "10.2"
ip addr show lo | grep -q 127.0.0.50 && ok "lo:127.0.0.50 (proxy IP) var" || echo "  ⚪ 127.0.0.50 yok (proxy başlatılınca eklenir)"
lsmod | grep -q sctp && ok "sctp modülü" || bad "sctp" "modprobe sctp"
[ "$(sysctl -n net.ipv4.conf.all.rp_filter)" = 0 ] && ok "rp_filter=0" || echo "  ⚪ rp_filter=$(sysctl -n net.ipv4.conf.all.rp_filter)"
""", "Teşhis")
''')
md("## 10.2 NAT / IP forwarding onarımı")
code(r'''
_ = ssh_script(r"""
sysctl -w net.ipv4.ip_forward=1 >/dev/null
IF=$(ip route | awk '/default/{print $5; exit}'); echo "WAN if: $IF"
for ip in 10.46.0.1/16 10.47.0.1/16; do
  ip addr show ogstun | grep -q $ip || ip addr add $ip dev ogstun
done
for sub in 10.45.0.0/16 10.46.0.0/16 10.47.0.0/16; do
  iptables -t nat -C POSTROUTING -s $sub ! -o ogstun -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s $sub ! -o ogstun -j MASQUERADE
done
iptables -C FORWARD -i ogstun -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i ogstun -j ACCEPT
iptables -C FORWARD -o ogstun -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o ogstun -j ACCEPT
echo "✅ NAT/forward kuralları ve ogstun IP'leri uygulandı"
iptables -t nat -S POSTROUTING | grep -E '10.45|10.46|10.47'
""", "NAT onarım")
''')
md("## 10.3 Soft temizlik (gNB/UE/proxy/artık namespace)\nServis konfigürasyonuna dokunmaz.")
code(r'''
_ = ssh_script(r"""
pkill -9 -x nr-ue; pkill -9 -x nr-gnb; pkill -9 -x n2proxy_master; pkill -9 -x n2proxy_advanced
for ns in proxy-ns amf-ns; do ip netns del $ns 2>/dev/null && echo "netns $ns silindi"; done
ip link del veth-proxy 2>/dev/null
rm -f /tmp/gnb-*.log /tmp/ue-*.log
echo "✅ temizlendi"; echo "gNB=$(pgrep -x nr-gnb|wc -l) UE=$(pgrep -x nr-ue|wc -l) proxy=$(pgrep -x n2proxy_master | wc -l)"
""", "Soft cleanup")
''')
md("## 10.4 ⚠️ Tam Rollback — AMF2/AMF3 dahil lab öncesi duruma dönüş\n`/etc/open5gs` **silinip** `n2lab_backup_*/open5gs` yedeğinden geri yüklenir (`~/99_rollback.sh`). Çalıştırmak için `CONFIRM = True` yapın.")
code(r'''
CONFIRM = False
if CONFIRM:
    _ = ssh_script("bash /home/localadmin/99_rollback.sh; systemctl restart open5gs-amfd@amf; systemctl stop open5gs-amfd@amf2 open5gs-amfd@amf3", "ROLLBACK", timeout=240)
else:
    print("⛔ CONFIRM=False — rollback çalıştırılmadı.")
''')
md("## 10.5 Tüm 5GC yeniden başlatma (sıralı)")
code(r'''
RUN = False
if RUN:
    _ = ssh_script(r"""
for s in nrfd scpd seppd udrd udmd ausfd pcfd nssfd bsfd smfd upfd; do systemctl restart open5gs-$s; sleep 1; done
for a in amf amf2 amf3; do systemctl restart open5gs-amfd@$a; sleep 1; done
sleep 3; for s in nrfd smfd upfd; do echo "$s $(systemctl is-active open5gs-$s)"; done
""", "5GC restart", timeout=240)
else:
    print("RUN=True yaparak çalıştırın.")
''')

# ---------------------------------------------------------------- 11
md("## 7.3 Hata detayı (en sık geçen farklı mesajlar)\nHata özeti bir NF'i işaretlediyse nedenini gösterir. VM yeni başlatıldıysa hssd/pcfd/pcrfd/udrd'deki aynı sayıdaki hatalar genellikle MongoDB/Diameter hazır olmadan başlayan servislerin açılış yarışıdır (VM açılışından sonra tekrarlamıyorsa zararsız).")
code(r'''
NFS = ["hssd", "pcfd", "pcrfd", "udrd", "seppd"]
SINCE = "1 hour ago"
_ = ssh_script(f"""
for n in {" ".join(NFS)}; do
  echo "──── open5gs-$n ────"
  journalctl -u open5gs-$n --since '{SINCE}' --no-pager -o short-iso 2>/dev/null | grep -iE 'error|fail|ERR\\]|FATAL' | grep -vE 'sepp2\\.localdomain' | {{ case $n in seppd) grep -vE 'ogs_sbi_client_handler\\(\\) failed';; *) cat;; esac; }} \\
    | sed -E 's/^[^ ]+ [^ ]+ //; s/\\x1b\\[[0-9;]*m//g; s/[0-9a-f]{{8}}-[0-9a-f-]{{27}}/<uuid>/g; s/[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+:[0-9]+/<ip:port>/g' \\
    | cut -c1-170 | sort | uniq -c | sort -rn | head -4
done
journalctl --list-boots --no-pager 2>/dev/null | tail -2
""", "Hata detayı")
''')

md("# 11. Yedek\n`/etc/open5gs`, UERANSIM n2lab profilleri, proxy kaynağı (`n2proxy_src/`), iptables ve MongoDB dump. Son 5 yedek saklanır.")
code(r'''
_ = ssh_script(r"""
D=/home/localadmin/ops_backup_$(date +%Y%m%d_%H%M%S); mkdir -p $D
cp -r /etc/open5gs $D/open5gs
cp -r /opt/UERANSIM/config/n2lab $D/ueransim_n2lab
cp -r /home/localadmin/n2proxy_src $D/n2proxy_src 2>/dev/null
cp /home/localadmin/proxy_master.log $D/ 2>/dev/null
cp /etc/systemd/system/open5gs-amfd@.service $D/ 2>/dev/null
iptables-save > $D/iptables.rules
mongodump --quiet --db open5gs --out $D/mongo 2>/dev/null && echo "mongo dump ok" || echo "mongodump yok/hata"
ls -dt /home/localadmin/ops_backup_* | tail -n +6 | xargs -r rm -rf    # son 5 yedek kalır
chown -R localadmin: $D; echo "✅ $D"; du -sh $D
""", "Yedekleme")
''')

# ---------------------------------------------------------------- 12
md("""
# 12. Roadmap Durumu (`PROJECT_SUMMARY_AND_ROADMAP.md`)

| Aşama | Konu | Durum |
|---|---|---|
| 1-2 | Discovery, host hazırlığı (sysctl, lksctp) | ✅ |
| 3-4 | AMF havuzu (3x), gNB/UE profilleri | ✅ |
| 5 | Baseline + rollback | ✅ |
| 6-7 | C++ epoll proxy iskeleti | ✅ |
| 8 | NGAP/APER ayrıştırma (RAN/AMF UE ID, 5G-S-TMSI, kapasite, GUAMI) | ✅ `ngap.h` (pycrate vektörleriyle doğrulandı) |
| 9 | LB + demux: `(gNB,ran)` tablosu, ağırlıklı RR, S-TMSI affinity | ✅ |
| 10 | HA: yeniden bağlanma, NGSetup replay, SCTP heartbeat, release, yoklama, drain | ✅ (VM'de doğrulandı) |
| 11 | Metrikler (`:9190`) + yük testi | ✅ yazıldı; VM yük sonuçları `01_…v2` `load`/`load_big` |
| — | Slice farkındalıklı seçim (C3) | ⏸ ertelendi: Requested NSSAI şifreli NAS içinde; InitialUEMessage'da yalnızca AllowedNSSAI (yeniden kayıt) var |

**Bilinen sınırlar:** yazma kuyruğu yok (EAGAIN'de mesaj düşer) · tek thread · AMF'i hiç yanıt vermemiş UE için release komutu üretilemez (NG Reset'e düşülür) · yoklama opt-in (`N2P_PROBE_MS`).
**Topoloji notları:** proxy `127.0.0.50` (lo alias) üzerinde; UE'ler `ns-<profil>` + `br-ue` (10.201.0.0/24) ile izole (bölüm 5.0c). `amf_name` ↔ servis eşlemesi için bkz. `TOPOLOGY.md`.
""")

# ---------------------------------------------------------------- 13
md("""
# 13. Hızlı Referans

| Ne | Değer |
|---|---|
| SSH | `ssh -p 2222 localadmin@127.0.0.1` (parola: `open5gs`) |
| AMF servisleri | `open5gs-amfd@amf` (127.0.0.5, cap100, ptr0) · `@amf2` (127.0.0.105, cap100, ptr1) · `@amf3` (127.0.0.205, cap50, ptr2) |
| gNB | A 127.0.0.11 (nci 0x10) · B .12 (0x11) · C .13 (0x12) |
| UE | s1=…101 → gNB-A · s1sd1=…102 → gNB-B · s2=…103 → gNB-C · s3=…104 → gNB-A |
| Proxy | `127.0.0.50:38412` · `~/n2proxy_master` · `~/proxy_master.log` · metrik `127.0.0.1:9190/metrics` · komut `/tmp/n2proxy.ctl` |
| NRF | `127.0.0.10:7777` (HTTP/2 h2c) |
| UE pool | `10.45.0.0/16` (ogstun) |
| WebUI | `:9999` (SSH tünel ile) |
| Dosyalar | `~/n2proxy_src/` · `~/loadgen/` · `~/99_rollback.sh` · `~/n2lab_backup_*` · `~/n2lab_pcaps/` |
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
      "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_vm_daily_ops_v1.ipynb")
with open(out, "w") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print("written", out, len(cells), "cells")
