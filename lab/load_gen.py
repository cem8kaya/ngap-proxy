#!/usr/bin/env python3
"""N2 proxy yük üreteci (yalnızca stdlib; VM'de çalışır). Sentetik gNB'ler NGSetup yapar, ardından InitialUEMessage gönderir;
AMF'in döndürdüğü ilk DownlinkNAS'a kadar geçen süre ölçülür. NAS içeriği sahte (kayıt reddedilir/kimlik istenir) — amaç proxy yoludur.

  python3 load_gen.py --proxy 127.0.0.50:38412 --gnbs 3 --ues 50 --rate 200 --json /tmp/load.json
  python3 load_gen.py --tcp ...   # yalnızca yerel mantık testi (TCP + 2 bayt uzunluk öneki)
"""
import argparse, json, os, select, socket, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load_templates as T

SCTP = getattr(socket, "IPPROTO_SCTP", 132)
SCTP_SNDINFO = 34


class Peer:
    def __init__(self, gid, addr, src, tcp):
        self.tcp, self.buf = tcp, b""
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP if tcp else SCTP)
        if src: self.s.bind((src, 0))
        self.s.connect(addr)
        self.s.setblocking(False)
        self.gid = gid

    def send(self, data):
        if self.tcp:
            self.s.sendall(struct.pack(">H", len(data)) + data)
            return
        anc = [(SCTP, SCTP_SNDINFO, struct.pack("=HHIII", 0, 0, socket.htonl(60), 0, 0))]   # PPID=60 (NGAP)
        try:
            self.s.sendmsg([data], anc)
        except OSError:
            self.s.send(data)

    def recv_all(self):
        out = []
        while True:
            try:
                if self.tcp:
                    d = self.s.recv(65536)
                    if not d: raise ConnectionError("kapandi")
                    self.buf += d
                    while len(self.buf) >= 2:
                        n = struct.unpack(">H", self.buf[:2])[0]
                        if len(self.buf) < 2 + n: break
                        out.append(self.buf[2:2 + n]); self.buf = self.buf[2 + n:]
                else:
                    d, _, _, _ = self.s.recvmsg(65536)
                    if not d: raise ConnectionError("kapandi")
                    out.append(d)
            except BlockingIOError:
                return out


def rd_len(b, p):
    if b[p] & 0x80 == 0: return b[p], p + 1
    return ((b[p] & 0x3F) << 8) | b[p + 1], p + 2


def peek(b):
    """(pdu, proc, ran) — yalnızca IE 85'i arar (APER gezinmesi, proxy'deki ngap.h ile aynı mantık)."""
    try:
        pdu, proc = (b[0] >> 5) & 3, b[1]
        _, p = rd_len(b, 3)
        p += 1; n = (b[p] << 8) | b[p + 1]; p += 2
        for _ in range(n):
            ie = (b[p] << 8) | b[p + 1]; l, q = rd_len(b, p + 3)
            if ie == 85:
                k = (b[q] >> 6) + 1
                return pdu, proc, int.from_bytes(b[q + 1:q + 1 + k], "big")
            p = q + l
        return pdu, proc, None
    except IndexError:
        return None, None, None


def proc_cpu(pid):
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
    except OSError:
        return None


def rss_kb(pid):
    try:
        for l in open(f"/proc/{pid}/status"):
            if l.startswith("VmRSS"): return int(l.split()[1])
    except OSError:
        return None


def cpu_snap():
    """{(comm,pid): tick} — ilgili 5GC süreçleri + proxy (yük penceresine özgü CPU ölçümü için)."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit(): continue
        try:
            comm = open(f"/proc/{d}/comm").read().strip()
            if comm == "n2proxy_master" or comm.startswith("open5gs-") or comm == "mongod":
                f = open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()
                out[(comm, d)] = int(f[11]) + int(f[12])
        except OSError:
            pass
    return out


def cpu_delta(a, b, dt):
    hz = os.sysconf("SC_CLK_TCK"); use = {}
    for k, v in b.items():
        use[k[0]] = use.get(k[0], 0) + (v - a.get(k, v)) / hz
    return {k: round(v / dt, 3) for k, v in sorted(use.items(), key=lambda kv: -kv[1])}, {k: round(v, 2) for k, v in use.items()}


def pctl(v, p):
    return v[min(len(v) - 1, int(len(v) * p))] if v else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="127.0.0.50:38412")
    ap.add_argument("--gnbs", type=int, default=3)
    ap.add_argument("--ues", type=int, default=50, help="gNB başına UE")
    ap.add_argument("--rate", type=float, default=0, help="toplam InitialUE/sn (0 = patlama)")
    ap.add_argument("--wait", type=float, default=10, help="son gönderimden sonra yanıt bekleme (sn)")
    ap.add_argument("--src-base", default="127.0.0.21", help="gNB kaynak IP'leri bu adresten artar (yok sayılırsa kaynak seçilmez)")
    ap.add_argument("--tcp", action="store_true")
    ap.add_argument("--pid", type=int, default=0, help="proxy pid (CPU/RSS ölçümü); 0 = pgrep n2proxy_master")
    ap.add_argument("--json")
    a = ap.parse_args()
    host, port = a.proxy.split(":"); addr = (host, int(port))
    pid = a.pid or int((subprocess.run(["pgrep", "-x", "n2proxy_master"], capture_output=True, text=True).stdout.split() or [0])[0])

    peers = []
    for i in range(a.gnbs):
        src = None
        if not a.tcp and a.src_base != "none":
            o = a.src_base.split("."); o[-1] = str(int(o[-1]) + i); src = ".".join(o)
        pr = Peer(T.NG_SETUP and list(T.NG_SETUP)[i], addr, src, a.tcp)
        pr.send(T.NG_SETUP[pr.gid]); peers.append(pr)
    t0 = time.time(); ok = 0
    while ok < len(peers) and time.time() - t0 < 10:
        select.select([p.s for p in peers], [], [], 0.2)
        for p in peers:
            for m in p.recv_all():
                pdu, proc, _ = peek(m)
                if proc == 21:
                    ok += 1; print(f"gNB {p.gid:#x}: NGSetup {'basarili' if pdu == 1 else 'BASARISIZ'}")
    if ok < len(peers):
        print("HATA: tüm gNB'ler NGSetup yanıtı almadı"); return 2

    sent, lat, order = {}, [], []
    total = a.gnbs * a.ues
    cpu0, w0 = proc_cpu(pid), time.time()
    snap0 = cpu_snap()
    for n in range(a.ues):                                # tur tur: her turda her gNB'den 1 UE (aynı RAN id'ler farklı gNB'lerde)
        for pi, p in enumerate(peers):
            m = bytearray(T.INITIAL_UE); off = T.RAN_ID_OFFSET
            m[off:off + 4] = (T.RAN_ID_BASE + n).to_bytes(4, "big")
            if a.rate: 
                due = w0 + (n * a.gnbs + pi) / a.rate
                while time.time() < due: time.sleep(0.0005)
            sent[(pi, T.RAN_ID_BASE + n)] = time.time()
            p.send(bytes(m))
        for pi, p in enumerate(peers):                    # gönderirken biriken yanıtları boşalt
            for m in p.recv_all():
                _, proc, ran = peek(m); k = (pi, ran)
                if proc is not None and ran is not None and k in sent and k not in order:
                    order.append(k); lat.append((time.time() - sent[k]) * 1000)
    t_send = time.time() - w0
    snap1 = cpu_snap()
    end = time.time() + a.wait
    while len(lat) < total and time.time() < end:
        select.select([p.s for p in peers], [], [], 0.1)
        for pi, p in enumerate(peers):
            for m in p.recv_all():
                _, proc, ran = peek(m); k = (pi, ran)
                if ran is not None and k in sent and k not in order:
                    order.append(k); lat.append((time.time() - sent[k]) * 1000)
    wall = time.time() - w0
    cpu1 = proc_cpu(pid)
    snap2 = cpu_snap()
    win_cores, _ = cpu_delta(snap0, snap1, max(t_send, 0.001))
    _, tot_s = cpu_delta(snap0, snap2, 1)
    missing = sorted([pi, r - T.RAN_ID_BASE] for (pi, r) in sent if (pi, r) not in set(order))
    lat.sort()
    res = {"gnbs": a.gnbs, "ues_per_gnb": a.ues, "sent": total, "answered": len(lat), "lost": total - len(lat),
           "send_seconds": round(t_send, 3), "send_rate": round(total / t_send, 1) if t_send else None,
           "lat_ms": {"p50": round(pctl(lat, .5), 2), "p95": round(pctl(lat, .95), 2), "p99": round(pctl(lat, .99), 2), "max": round(lat[-1], 2) if lat else None},
           "proxy_pid": pid or None, "proxy_cpu_s": round(cpu1 - cpu0, 3) if cpu0 is not None and cpu1 is not None else None,
           "proxy_rss_kb": rss_kb(pid) if pid else None, "wall_s": round(wall, 2),
           "cpu_send_window_cores": win_cores, "cpu_total_s": tot_s, "ncpu": os.cpu_count(), "missing": missing}
    print(json.dumps(res, indent=1))
    if a.json: json.dump(res, open(a.json, "w"))
    for p in peers: p.s.close()
    return 0 if res["lost"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
