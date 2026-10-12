"""Proxy mantık testi (SCTP gerekmez): proxy -DN2P_TCP_TEST ile derlenir, sahte AMF'ler ve gNB'ler TCP+2 bayt uzunluk
öneki ile konuşur. NGAP PDU'ları pycrate (APER) ile üretilir.

    pip install pycrate
    g++ -std=c++17 -O1 -DN2P_TCP_TEST -o /tmp/n2p_tcp src/n2proxy_master.cpp
    python3 -I tests/proxy_logic_test.py /tmp/n2p_tcp
"""
import os, re, socket, struct, subprocess, sys, threading, time
from pycrate_asn1dir import NGAP

P = NGAP.NGAP_PDU_Descriptions.NGAP_PDU
NAS = b'\x7e\x00\x41\x79\x00\x0d\x01\x99\xf9\x07\x00\x00\x00\x00\x00\x00\x00\x00\x01\x2e\x04\xf0\xf0\xf0\xf0'
ULI = ('userLocationInformationNR', {'nR-CGI': {'pLMNIdentity': b'\x99\xf9\x07', 'nRCellIdentity': (0x10, 36)},
                                    'tAI': {'pLMNIdentity': b'\x99\xf9\x07', 'tAC': b'\x00\x00\x01'}})


def ie(i, c, t, v): return {'id': i, 'criticality': c, 'value': (t, v)}


def pdu(kind, code, crit, name, ies):
    P.set_val((kind, {'procedureCode': code, 'criticality': crit, 'value': (name, {'protocolIEs': ies})}))
    return P.to_aper()


def ng_setup_req(): return pdu('initiatingMessage', 21, 'reject', 'NGSetupRequest', [ie(82, 'ignore', 'RANNodeName', 'gnb')] if False else
                               [ie(27, 'reject', 'GlobalRANNodeID', ('globalGNB-ID', {'pLMNIdentity': b'\x99\xf9\x07', 'gNB-ID': ('gNB-ID', (1, 22))}))])
def ng_setup_ok(cap=None, ptr=None):
    ies = [ie(1, 'reject', 'AMFName', 'amf')]
    if ptr is not None:
        ies.append(ie(96, 'reject', 'ServedGUAMIList', [{'gUAMI': {'pLMNIdentity': b'\x99\xf9\x07', 'aMFRegionID': (2, 8), 'aMFSetID': (1, 10), 'aMFPointer': (ptr, 6)}}]))
    if cap is not None:
        ies.append(ie(86, 'ignore', 'RelativeAMFCapacity', cap))
    return pdu('successfulOutcome', 21, 'reject', 'NGSetupResponse', ies)
def ran_cfg_ack(): return pdu('successfulOutcome', 35, 'reject', 'RANConfigurationUpdateAcknowledge', [])
def ng_setup_fail(): return pdu('unsuccessfulOutcome', 21, 'reject', 'NGSetupFailure', [ie(15, 'ignore', 'Cause', ('misc', 'unspecified'))])
def initial_ue(r, stmsi=None):
    ies = [ie(85, 'reject', 'RAN-UE-NGAP-ID', r), ie(38, 'reject', 'NAS-PDU', NAS), ie(121, 'reject', 'UserLocationInformation', ULI)]
    if stmsi:
        ies.append(ie(26, 'reject', 'FiveG-S-TMSI', {'aMFSetID': (stmsi[0], 10), 'aMFPointer': (stmsi[1], 6), 'fiveG-TMSI': b'\xde\xad\xbe\xef'}))
    ies.append(ie(90, 'ignore', 'RRCEstablishmentCause', 'mo-Signalling'))
    return pdu('initiatingMessage', 15, 'ignore', 'InitialUEMessage', ies)
def ul_nas(a, r): return pdu('initiatingMessage', 46, 'ignore', 'UplinkNASTransport', [
    ie(10, 'reject', 'AMF-UE-NGAP-ID', a), ie(85, 'reject', 'RAN-UE-NGAP-ID', r), ie(38, 'reject', 'NAS-PDU', NAS), ie(121, 'reject', 'UserLocationInformation', ULI)])
def dl_nas(a, r): return pdu('initiatingMessage', 4, 'ignore', 'DownlinkNASTransport', [
    ie(10, 'reject', 'AMF-UE-NGAP-ID', a), ie(85, 'reject', 'RAN-UE-NGAP-ID', r), ie(38, 'reject', 'NAS-PDU', NAS)])
def ctx_rel_complete(a, r): return pdu('successfulOutcome', 41, 'reject', 'UEContextReleaseComplete', [
    ie(10, 'ignore', 'AMF-UE-NGAP-ID', a), ie(85, 'ignore', 'RAN-UE-NGAP-ID', r)])


def ctx_rel_req(a, r): return pdu('initiatingMessage', 42, 'ignore', 'UEContextReleaseRequest', [
    ie(10, 'reject', 'AMF-UE-NGAP-ID', a), ie(85, 'reject', 'RAN-UE-NGAP-ID', r), ie(15, 'ignore', 'Cause', ('radioNetwork', 'unspecified'))])
def rel_cmd(a, r):
    P.set_val(('initiatingMessage', {'procedureCode': 41, 'criticality': 'reject', 'value': ('UEContextReleaseCommand', {'protocolIEs': [
        ie(114, 'reject', 'UE-NGAP-IDs', ('uE-NGAP-ID-pair', {'aMF-UE-NGAP-ID': a, 'rAN-UE-NGAP-ID': r})),
        ie(15, 'ignore', 'Cause', ('radioNetwork', 'unspecified'))]})}))
    return P.to_aper()


def info(b):
    """(pdu_kind, proc, ran, amf) — pycrate ile bağımsız çözüm."""
    P.from_aper(b); k, v = P.get_val(); ran = amf = None
    for x in v['value'][1]['protocolIEs']:
        if x['id'] == 85: ran = x['value'][1]
        if x['id'] == 10: amf = x['value'][1]
        if x['id'] == 114: amf, ran = x['value'][1][1]['aMF-UE-NGAP-ID'], x['value'][1][1]['rAN-UE-NGAP-ID']
    return k, v['procedureCode'], ran, amf


def send(s, b): s.sendall(struct.pack('>H', len(b)) + b)


class Conn:
    """Çerçeveli TCP uçu; okunan çerçeveleri listeye biriktirir."""
    def __init__(self, s):
        self.s, self.frames, self.buf, self.alive = s, [], b'', True
        self.lock = threading.Lock()
        threading.Thread(target=self._rd, daemon=True).start()

    def _rd(self):
        while True:
            try: d = self.s.recv(65536)
            except OSError: d = b''
            if not d: self.alive = False; return
            self.buf += d
            while len(self.buf) >= 2:
                n = struct.unpack('>H', self.buf[:2])[0]
                if len(self.buf) < 2 + n: break
                with self.lock: self.frames.append(self.buf[2:2 + n])
                self.buf = self.buf[2 + n:]

    def take(self):
        with self.lock: f, self.frames = self.frames, []
        return f

    def send(self, b): send(self.s, b)
    def close(self):
        try: self.s.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.s.close()


class MockAmf:
    """Sahte AMF: setup'a başarı döner, InitialUE/UplinkNAS'a DownlinkNAS döner. Gelenleri kaydeder."""
    def __init__(self, name, setup='ok', accept=True, cap=None, ptr=None, frozen=False, cold=False):
        self.name, self.setup, self.conns, self.rx = name, setup, [], []
        self.cap, self.ptr, self.frozen, self.cold = cap, ptr, frozen, cold
        self.ls = socket.socket(); self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(('127.0.0.1', 0)); self.ls.listen(8); self.port = self.ls.getsockname()[1]
        self.accepting = accept
        threading.Thread(target=self._acc, daemon=True).start()
        self.amf_ids = 100 * name

    def _acc(self):
        while True:
            try: s, _ = self.ls.accept()
            except OSError: return
            c = Conn(s); self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        while c.alive:
            for f in c.take():
                self.rx.append(f)
                try: k, proc, ran, amf = info(f)
                except Exception: continue
                if self.frozen: continue                      # SIGSTOP benzeri: alır ama hiç yanıt vermez
                if proc == 21: c.send(ng_setup_fail() if self.setup == 'fail' else ng_setup_ok(self.cap, self.ptr))
                elif proc == 35: c.send(ran_cfg_ack())
                elif proc == 15: c.send(rel_cmd(self.amf_ids + ran, ran) if self.cold else dl_nas(self.amf_ids + ran, ran))   # cold: yeniden başlamış AMF, bağlamı yok
            time.sleep(0.005)

    def kill_conns(self):
        for c in self.conns: c.close()
        self.conns = []

    def stop_listening(self):
        try: self.ls.shutdown(socket.SHUT_RDWR)      # bloklu accept()'i uyandırır; yalnızca close() dinlemeyi kapatmaz
        except OSError: pass
        self.ls.close()

    def count(self, proc): return sum(1 for f in self.rx if info(f)[1] == proc)


class Proxy:
    def __init__(self, binary, amfs, env=None):
        self.port = free_port()
        self.env = dict(env or {})
        e = {**os.environ, 'N2P_LISTEN': f'127.0.0.1:{self.port}', 'N2P_AMFS': ','.join(f'127.0.0.1:{a.port}' for a in amfs), **(env or {})}
        self.log = open('/tmp/n2p_test.log', 'w+')
        self.p = subprocess.Popen([binary], env=e, stdout=self.log, stderr=subprocess.STDOUT)
        time.sleep(0.3)

    def text(self): self.log.flush(); self.log.seek(0); return self.log.read()
    def stop(self): self.p.terminate(); self.p.wait()
    def gnb(self):
        s = socket.create_connection(('127.0.0.1', self.port)); return Conn(s)


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def wait(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.02)
    return False


fails = checks = 0


def check(cond, msg):
    global fails, checks
    checks += 1
    if not cond: fails += 1
    print(('  ✔ ' if cond else '  ✘ ') + msg)


def lb_lines(text): return [(int(g), int(r), int(a)) for g, r, a in re.findall(r'\[LB\] YENI UE \(GNB_FD=(\d+) RAN_ID=(\d+)\) -> AMF-(\d)', text)]


def scenario(name, binary, fn, **kw):
    print(f'\n━━ {name}')
    caps, ptrs = kw.get('caps', [None] * 3), kw.get('ptrs', [None] * 3)
    amfs = [MockAmf(i + 1, setup=kw.get('setups', ['ok'] * 3)[i], cap=caps[i], ptr=ptrs[i], frozen=(i in kw.get('frozen', ())), cold=(i in kw.get('cold', ()))) for i in range(3)]
    px = Proxy(binary, amfs, kw.get('env'))
    try: fn(px, amfs)
    except Exception as e:
        check(False, f'istisna: {e!r}')
    finally:
        px.stop()
        for a in amfs: a.stop_listening(); a.kill_conns()
        if '-v' in sys.argv: print(px.text())


def setup_gnb(px, amfs):
    g = px.gnb(); g.send(ng_setup_req())
    ok = wait(lambda: all(a.count(21) >= 1 for a in amfs))
    return g, ok


# ----------------------------------------------------------------------------- senaryolar
def t_setup(px, amfs):
    g, ok = setup_gnb(px, amfs)
    check(ok, 'NGSetupRequest 3 AMF\'e çoğaltıldı')
    time.sleep(0.5)
    resp = [f for f in g.take() if info(f)[1] == 21]
    check(len(resp) == 1 and info(resp[0])[0] == 'successfulOutcome', f'gNB tam 1 NGSetupResponse aldı ({len(resp)})')


def t_setup_fail_one(px, amfs):
    g, ok = setup_gnb(px, amfs)
    time.sleep(0.5)
    resp = [info(f)[0] for f in g.take() if info(f)[1] == 21]
    check(resp == ['successfulOutcome'], f'1 AMF reddetse bile gNB başarı gördü: {resp}')
    g.send(initial_ue(1)); g.send(initial_ue(2)); g.send(initial_ue(3)); time.sleep(0.5)
    check({a for *_, a in lb_lines(px.text())} == {2, 3}, f'reddeden AMF-1 havuz dışı: {lb_lines(px.text())}')


def t_b1_multi_gnb(px, amfs):
    gs = []
    for _ in range(3):
        g, ok = setup_gnb(px, amfs); gs.append(g)
        wait(lambda: any(info(f)[1] == 21 for f in g.frames))
    for g in gs: g.take()
    time.sleep(0.3)
    for g in gs: g.send(initial_ue(1))                       # üç gNB'de de RAN_ID=1 (eşzamanlı)
    time.sleep(0.5)
    ev = lb_lines(px.text())
    check(len(ev) == 3 and {a for *_, a in ev} == {1, 2, 3}, f'3 gNB × RAN_ID=1 → 3 farklı AMF (RR): {ev}')
    by_gnb = {gnb: a for gnb, _, a in ev}
    for g in gs: g.send(ul_nas(0, 1))
    time.sleep(0.5)
    # her AMF'in aldığı UplinkNAS, kendi UE'si kadar (1) olmalı — çapraz yönlenme yok
    per = [a.count(46) for a in amfs]
    check(per == [1, 1, 1], f'UplinkNAS her AMF\'e tam 1 kez gitti (çakışma yok): {per}')
    dl = [[info(f) for f in g.take() if info(f)[1] == 4] for g in gs]
    check(all(len(x) == 1 for x in dl), f'her gNB yalnızca kendi UE\'sinin DownlinkNAS\'ını aldı: {[len(x) for x in dl]}')


def t_burst(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.4); g.take()
    blob = b''.join(struct.pack('>H', len(m)) + m for m in (initial_ue(r) for r in range(1, 61)))
    g.s.sendall(blob)                                         # 60 mesaj tek yazmada birleşik (B2)
    check(wait(lambda: len(lb_lines(px.text())) == 60, 5), f'birleşik 60 InitialUE\'nin hepsi işlendi: {len(lb_lines(px.text()))}')
    cnt = [sum(1 for *_, a in lb_lines(px.text()) if a == i) for i in (1, 2, 3)]
    check(cnt == [20, 20, 20], f'RR dağılımı 20/20/20: {cnt}')


def t_amf_loss_reset_reconnect(px, amfs):   # N2P_ON_AMF_LOSS=reset
    g, _ = setup_gnb(px, amfs); time.sleep(0.4); g.take()
    for r in (1, 2, 3, 4, 5, 6): g.send(initial_ue(r))
    time.sleep(0.5); g.take()
    ev = lb_lines(px.text()); on2 = sorted(r for _, r, a in ev if a == 2)
    check(on2 == [2, 5], f'RR: AMF-2 UE\'leri {on2}')
    amfs[1].kill_conns()                                      # AMF-2 bağlantıyı düşürür (dinlemeye devam eder)
    check(wait(lambda: '[HA] AMF-2 pasife' in px.text()), 'AMF-2 kaybı algılandı')
    time.sleep(0.2)
    resets = [f for f in g.take() if info(f)[1] == 20]
    import subprocess as sp
    exp = None
    P.set_val(('initiatingMessage', {'procedureCode': 20, 'criticality': 'reject', 'value': ('NGReset', {'protocolIEs': [
        ie(15, 'ignore', 'Cause', ('misc', 'unspecified')),
        ie(88, 'reject', 'ResetType', ('partOfNG-Interface', [{'rAN-UE-NGAP-ID': 2}, {'rAN-UE-NGAP-ID': 5}]))]})}))
    exp = P.to_aper()
    check(len(resets) == 1 and resets[0] == exp, 'gNB yalnızca etkilenen UE\'ler (2,5) için NG Reset aldı')
    g.send(ul_nas(0, 3)); time.sleep(0.3)
    check(amfs[2].count(46) == 1, 'etkilenmeyen UE (AMF-3) kesintisiz çalışıyor')
    g.send(ul_nas(0, 2)); time.sleep(0.3)
    check(sum(a.count(46) for a in amfs) == 1, 'silinen UE için mesaj hiçbir AMF\'e gitmedi (bayat fd yok)')
    for r in (11, 12, 13, 14): g.send(initial_ue(r))
    time.sleep(0.5)
    new = [a for _, r, a in lb_lines(px.text()) if r >= 11]
    # AMF-2 hemen yeniden bağlanır (dinliyor), ama NGSetup yanıtı gelene kadar READY değil
    check(wait(lambda: amfs[1].count(21) >= 2, 5), 'AMF-2 geri bağlandı ve NGSetup replay edildi')
    time.sleep(0.5)
    resp = [f for f in g.take() if info(f)[1] == 21]
    check(len(resp) == 0, 'replay yanıtı gNB\'ye ikinci kez iletilmedi')
    g.send(initial_ue(21)); g.send(initial_ue(22)); g.send(initial_ue(23)); time.sleep(0.5)
    last = [a for _, r, a in lb_lines(px.text()) if r >= 21]
    check(2 in last, f'AMF-2 havuza döndü ve yeni UE aldı: {last}')


def t_amf_loss_release(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.4); g.take()
    for r in (1, 2, 3, 4, 5, 6): g.send(initial_ue(r))
    time.sleep(0.5); g.take()
    amfs[1].kill_conns()                                      # AMF-2: RAN 2 ve 5 (AMF id 202, 205)
    check(wait(lambda: '[HA] AMF-2 pasife' in px.text()), 'AMF-2 kaybı algılandı')
    time.sleep(0.3)
    fr = g.take(); rel = sorted((info(f)[3], info(f)[2]) for f in fr if info(f)[1] == 41)
    check(rel == [(202, 2), (205, 5)], f'gNB etkilenen UE başına UEContextReleaseCommand aldı: {rel}')
    check(not [f for f in fr if info(f)[1] == 20], 'NG Reset gönderilmedi')
    g.send(ctx_rel_complete(202, 2)); g.send(ctx_rel_complete(205, 5)); time.sleep(0.3)
    check('bilinmeyen UE' not in px.text() and px.text().count('release tamamlandi') == 2, 'gNB\'nin ReleaseComplete\'leri uyarı üretmeden karşılandı')
    # yetim UE: gNB, tablo dışı bir UE için release ister -> proxy komut verir
    g.send(ctx_rel_req(777, 42)); time.sleep(0.3)
    got = [(info(f)[3], info(f)[2]) for f in g.take() if info(f)[1] == 41]
    check(got == [(777, 42)], f'yetim UE için release komutu üretildi: {got}')
    g.send(ul_nas(0, 3)); time.sleep(0.3)
    check(amfs[2].count(46) == 1, 'etkilenmeyen UE kesintisiz')


def t_weights(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.5); g.take()
    check(all(f'AMF-{i} kapasite={c}' in px.text() for i, c in ((1, 100), (2, 100), (3, 50))), 'kapasiteler NGSetupResponse\'tan öğrenildi')
    for r in range(1, 101): g.send(initial_ue(r))
    check(wait(lambda: len(lb_lines(px.text())) == 100, 5), '100 UE işlendi')
    cnt = [sum(1 for *_, a in lb_lines(px.text()) if a == i) for i in (1, 2, 3)]
    check(abs(cnt[0] - 40) <= 1 and abs(cnt[1] - 40) <= 1 and abs(cnt[2] - 20) <= 1, f'ağırlıklı dağılım ≈ 40/40/20: {cnt}')
    amfs[0].kill_conns(); time.sleep(0.5)                         # AMF-1 düşünce kalan 100:50 oranı korunur
    base = len(lb_lines(px.text()))
    for r in range(101, 151): g.send(initial_ue(r))
    wait(lambda: len(lb_lines(px.text())) == base + 50, 5)
    c2 = [sum(1 for *_, a in lb_lines(px.text())[base:] if a == i) for i in (1, 2, 3)]
    check(c2[0] <= 0 + 0 and abs(c2[1] - 33) <= 2 and abs(c2[2] - 17) <= 2, f'AMF-1 yokken 50 UE ≈ 0/33/17: {c2}')


def t_affinity(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.5); g.take()
    for r in (1, 2, 3, 4): g.send(initial_ue(r, stmsi=(1, 2)))       # sahibi pointer 2 -> AMF-3
    g.send(initial_ue(5, stmsi=(1, 9)))                                # sahipsiz pointer -> ağırlıklı RR
    g.send(initial_ue(6))                                              # S-TMSI yok -> ağırlıklı RR
    time.sleep(0.6)
    ev = {r: a for _, r, a in lb_lines(px.text())}
    check([ev[1], ev[2], ev[3], ev[4]] == [3, 3, 3, 3], f'S-TMSI(set=1,ptr=2) taşıyan UE\'ler hep AMF-3\'e: {[ev[i] for i in (1,2,3,4)]}')
    check('S-TMSI affinity set=1 ptr=2' in px.text(), 'affinity kararı loglandı')
    check([ev[5], ev[6]] == [1, 2], f'sahipsiz/S-TMSI\'siz UE\'ler RR sırasından devam eder (affinity sayacı bozmaz): {[ev[5], ev[6]]}')
    amfs[2].kill_conns(); amfs[2].stop_listening(); time.sleep(0.5)
    g.send(initial_ue(7, stmsi=(1, 2))); time.sleep(0.4)
    check([a for _, r, a in lb_lines(px.text()) if r == 7][0] in (1, 2), 'sahip AMF düştüyse sağlıklı AMF\'e düşülür')


def t_stale_stmsi(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.5); g.take()
    g.send(initial_ue(1, stmsi=(1, 2))); time.sleep(0.4)
    ev = {r: a for _, r, a in lb_lines(px.text())}
    check(ev[1] == 3 and 'S-TMSI affinity set=1 ptr=2' in px.text(), 'ilk deneme affinity ile sahibi AMF-3\'e gitti')
    check(wait(lambda: 'icin baglam bilmiyor' in px.text(), 2), 'AMF-3 hemen release verince S-TMSI bayat olarak öğrenildi')
    check(any(info(f)[1] == 41 for f in g.take()), 'release komutu gNB\'ye iletildi')
    g.send(ctx_rel_complete(301, 1)); time.sleep(0.2)
    g.send(initial_ue(2, stmsi=(1, 2))); time.sleep(0.4)                  # aynı TMSI, UE tekrar dener
    line = [l for l in px.text().splitlines() if 'RAN_ID=2)' in l][0]
    check('S-TMSI bayat' in line and 'affinity set' not in line, f'aynı S-TMSI ile yeniden deneme WRR\'ye yönlendi: {line.split("secildi!")[1].strip()}')
    g.send(initial_ue(3, stmsi=(1, 0))); time.sleep(0.3)                    # başka AMF'in S-TMSI'si etkilenmez
    check('RAN_ID=3) -> AMF-1 secildi! (S-TMSI affinity' in px.text(), 'bayatlık yalnızca ilgili (pointer,TMSI) için; diğer AMF affinity\'si sürüyor')


def t_affinity_off(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.5); g.take()
    for r in (1, 2, 3): g.send(initial_ue(r, stmsi=(1, 2)))
    time.sleep(0.5)
    check('affinity set' not in px.text() and 'affinity kapali' in px.text(), 'N2P_AFFINITY=off: S-TMSI yok sayılır, WRR')
    check({a for *_, a in lb_lines(px.text())} == {1, 2, 3}, 'dağılım WRR sırasıyla')


def t_drain(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.5); g.take()
    ctl = '/tmp/n2p_test.ctl'
    open(ctl, 'w').write('drain 2\n')
    check(wait(lambda: 'AMF-2 drain moduna alindi' in px.text()), 'drain komutu işlendi')
    for r in range(1, 7): g.send(initial_ue(r))
    time.sleep(0.5)
    ev = lb_lines(px.text())
    check(len(ev) == 6 and all(a != 2 for *_, a in ev), f'drain\'deki AMF-2 yeni UE almadı: {[a for *_, a in ev]}')
    g.send(initial_ue(50, stmsi=(1, 1))); time.sleep(0.3)
    check([a for _, r, a in lb_lines(px.text()) if r == 50] == [2], 'drain\'deki AMF, sahibi olduğu dönen UE\'yi (S-TMSI) yine de alır')
    open(ctl, 'w').write('undrain 2\nstatus\n')
    check(wait(lambda: 'drain\'den cikarildi' in px.text() and 'AMF-3 127.0.0.1' in px.text()), 'undrain + status çalıştı')
    n0 = len(lb_lines(px.text()))
    for r in range(100, 109): g.send(initial_ue(r))
    wait(lambda: len(lb_lines(px.text())) == n0 + 9, 3)
    check({a for *_, a in lb_lines(px.text())[n0:]} == {1, 2, 3}, 'undrain sonrası AMF-2 yine UE alıyor')
    open(ctl, 'w').write('drain 1\ndrain 2\ndrain 3\n'); time.sleep(0.6)
    g.send(initial_ue(200)); time.sleep(0.3)
    check([a for _, r, a in lb_lines(px.text()) if r == 200] != [], 'hepsi drain\'deyse UE reddedilmez')


def t_probe(px, amfs):
    g, _ = setup_gnb(px, amfs)          # frames'i atma: ilk yoklama yanıtının sızması da yakalanmalı
    time.sleep(2.0)
    check('[HA] AMF-' not in px.text().replace('NGSetup tamam', '').replace('kapasite', '') or '[ALARM]' not in px.text(), 'sağlıklı AMF\'ler yoklamalara yanıt veriyor, kimse düşmedi')
    check(not [f for f in g.take() if info(f)[1] == 35], 'yoklama yanıtları gNB\'ye sızmadı')
    amfs[1].frozen = True                                          # AMF-2 donar (soket açık, yanıt yok)
    check(wait(lambda: '[HA] AMF-2 pasife' in px.text(), 3), 'donmuş AMF-2 yoklama zaman aşımıyla algılandı')
    check('probe zaman asimi' in px.text(), 'neden: probe zaman aşımı')
    g.send(initial_ue(1)); g.send(initial_ue(2)); g.send(initial_ue(3)); time.sleep(0.4)
    check({a for *_, a in lb_lines(px.text())} <= {1, 3}, 'yeni UE\'ler yalnızca canlı AMF\'lere')


def t_setup_timeout(px, amfs):
    g, _ = setup_gnb_partial(px, amfs)
    check(wait(lambda: 'NGSetup zaman asimi' in px.text(), 3), 'NGSetup yanıtı vermeyen (donmuş) AMF-2 zaman aşımıyla düşürüldü')
    check(wait(lambda: px.text().count('NGSetup zaman asimi') >= 2, 4), 'yeniden bağlanma + tekrar zaman aşımı döngüsü sürüyor (takılmadı)')
    resp = [info(f)[0] for f in g.take() if info(f)[1] == 21]
    check(resp == ['successfulOutcome'], f'gNB yine de tek NGSetupResponse gördü: {resp}')
    for r in range(1, 7): g.send(initial_ue(r))
    time.sleep(0.5)
    check({a for *_, a in lb_lines(px.text())} == {1, 3}, 'UE\'ler yalnızca yanıt veren AMF\'lere')


def setup_gnb_partial(px, amfs):
    g = px.gnb(); g.send(ng_setup_req())
    wait(lambda: amfs[0].count(21) >= 1 and amfs[2].count(21) >= 1)
    return g, True


def scrape(port):
    import http.client
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=3); c.request('GET', '/metrics'); r = c.getresponse(); body = r.read().decode()
    m = {}
    for l in body.splitlines():
        if l and not l.startswith('#'):
            k, v = l.rsplit(' ', 1); m[k] = float(v)
    return m


def conservation(m):
    """Mesaj korunumu: alınan + çoğaltılan fazlalık + proxy'nin ürettikleri == iletilen + yutulan + düşen."""
    rx = m['n2_messages_total{dir="gnb_to_proxy"}'] + m['n2_messages_total{dir="amf_to_proxy"}'] + m['n2_messages_consumed_total{kind="mcast_extra"}'] + m['n2_messages_consumed_total{kind="injected"}']
    out = m['n2_messages_total{dir="proxy_to_gnb"}'] + m['n2_messages_total{dir="proxy_to_amf"}'] + m['n2_messages_consumed_total{kind="swallowed"}'] + \
        sum(v for k, v in m.items() if k.startswith('n2_dropped_total'))
    return rx, out


def t_metrics(px, amfs):
    port = int(px.env['N2P_METRICS'].split(':')[1])
    g, _ = setup_gnb(px, amfs); time.sleep(0.5); g.take()
    for r in range(1, 21): g.send(initial_ue(r))
    check(wait(lambda: len(lb_lines(px.text())) == 20, 4), '20 UE işlendi')
    m = scrape(port)
    per = [m[f'n2_lb_assignments_total{{amf="{i}",reason="wrr"}}'] for i in (1, 2, 3)]
    check(sum(per) == 20 and per == [8, 8, 4], f'atama sayaçları (WRR 100/100/50): {per}')
    check(m['n2_ue_contexts'] == 20 and m['n2_ue_contexts{amf="3"}'] == 4, 'UE bağlam gauge\'ları')
    check(m['n2_amf_capacity{amf="3"}'] == 50 and m['n2_gnb_connections'] == 1, 'kapasite / gNB gauge')
    check(m['n2_messages_total{dir="gnb_to_proxy"}'] >= 21, 'mesaj sayacı')
    rx, out = conservation(m); check(rx == out, f'mesaj korunumu (yük altında hiçbir mesaj sessizce kaybolmadı): {rx} == {out}')
    g.send(initial_ue(99, stmsi=(1, 2))); time.sleep(0.3)
    check(scrape(port)['n2_lb_assignments_total{amf="3",reason="affinity"}'] == 1, 'affinity sayacı')
    g.send(ul_nas(0, 4242)); time.sleep(0.3)
    check(scrape(port)['n2_dropped_total{reason="unknown_ue"}'] == 1, 'bilinmeyen UE düşme sayacı')
    amfs[1].kill_conns(); time.sleep(0.4)
    m = scrape(port)
    check(m['n2_ha_events_total{event="amf_failover"}'] == 1 and m['n2_ue_contexts{amf="2"}'] == 0, 'failover sayacı ve AMF-2 bağlamları temizlendi')
    check(m['n2_ha_events_total{event="ue_release_cmd"}'] == 8, f'release komut sayacı = 8: {m["n2_ha_events_total{event=\"ue_release_cmd\"}"]}')
    time.sleep(1.8)
    m = scrape(port)
    rx, out = conservation(m); check(rx == out, f'mesaj korunumu failover/replay/release sonrası da geçerli: {rx} == {out}')
    check(m['n2_ha_events_total{event="amf_reconnect"}'] >= 1 and m['n2_amf_ready{gnb="%d",amf="2",state="READY"}' % [int(k.split('"')[1]) for k in m if k.startswith('n2_amf_ready')][0]] == 1, 'yeniden bağlanma sayacı ve AMF-2 tekrar READY')


def t_loadgen(px, amfs):
    lab = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lab')
    mport = int(px.env['N2P_METRICS'].split(':')[1])
    out = subprocess.run([sys.executable, '-I', os.path.join(lab, 'load_gen.py'), '--tcp', '--proxy', f'127.0.0.1:{px.port}', '--gnbs', '3', '--ues', '40',
                          '--rate', '400', '--wait', '5', '--pid', str(px.p.pid), '--json', '/tmp/n2p_load.json'],
                         capture_output=True, text=True, cwd=lab, env={**os.environ, 'PYTHONPATH': lab})
    import json
    r = json.load(open('/tmp/n2p_load.json')) if os.path.exists('/tmp/n2p_load.json') else {}
    check(out.returncode == 0 and r.get('sent') == 120, f"yük üreteci tamamlandı: rc={out.returncode} {out.stderr[-200:]}")
    check(r.get('answered') == 120 and r.get('lost') == 0, f"120/120 InitialUE yanıtlandı: {r.get('answered')}")
    ev = lb_lines(px.text())
    check(len(ev) == 120, f'proxy 120 UE seçti: {len(ev)}')
    cnt = [sum(1 for *_, a in ev if a == i) for i in (1, 2, 3)]
    # NGSetupResponse ilk AMF yanıtında iletilir; diğer backend'ler henüz READY değilken gelen ilk UE'ler dağılımı ±birkaç kaydırabilir
    check(sum(cnt) == 120 and all(abs(c - 40) <= 3 for c in cnt), f'3 gNB × aynı RAN id\'ler, dağılım eşite yakın: {cnt}')
    check('bilinmeyen UE' not in px.text(), 'çakışma/yanlış yönlenme uyarısı yok')
    rx, o2 = conservation(scrape(mport)); check(rx == o2, f'yük sonrası mesaj korunumu: {rx} == {o2}')


def t_amf_down_silent(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.4); g.take()
    for r in (1, 2, 3): g.send(initial_ue(r))
    time.sleep(0.4); g.take()
    amfs[0].kill_conns(); amfs[0].stop_listening()
    check(wait(lambda: '[HA] AMF-1 pasife' in px.text()), 'AMF-1 kaybı algılandı (dinlemiyor)')
    time.sleep(0.3)
    check(not [f for f in g.take() if info(f)[1] == 20], 'silent modda NG Reset gönderilmedi')
    g.send(initial_ue(9)); g.send(initial_ue(10)); time.sleep(0.4)
    check({a for _, r, a in lb_lines(px.text()) if r >= 9} <= {2, 3}, 'yeni UE\'ler yalnızca sağlıklı AMF\'lere')
    time.sleep(2.5)
    check('ERISILEMIYOR' in px.text(), 'yeniden bağlanma denemeleri sürüyor (backoff)')


def t_release(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.4); g.take()
    g.send(initial_ue(1)); time.sleep(0.3)
    g.send(ul_nas(0, 1)); time.sleep(0.2)
    n1 = sum(a.count(46) for a in amfs)
    g.send(ctx_rel_complete(101, 1)); time.sleep(0.2)
    g.send(ul_nas(0, 1)); time.sleep(0.3)
    n2 = sum(a.count(46) for a in amfs)
    check(n1 == 1 and n2 == 1, f'UEContextReleaseComplete sonrası kayıt silindi ({n1}->{n2})')
    check('bilinmeyen UE' in px.text(), 'bilinmeyen UE uyarısı loglandı')


def t_gnb_disconnect(px, amfs):
    g, _ = setup_gnb(px, amfs); time.sleep(0.3)
    g.send(initial_ue(1)); time.sleep(0.2); g.close()
    check(wait(lambda: 'gNB baglantiyi kopardı' in px.text()), 'gNB kopuşu işlendi')
    g2 = px.gnb(); g2.send(ng_setup_req()); time.sleep(0.5)
    g2.send(ul_nas(0, 1)); time.sleep(0.3)
    check('bilinmeyen UE' in px.text(), 'eski gNB\'nin UE kaydı yeni bağlantıya sızmadı')


if __name__ == '__main__':
    binary = sys.argv[1]
    scenario('NGSetup çoğaltma + tek yanıt', binary, t_setup)
    scenario('Bir AMF NGSetupFailure döner', binary, t_setup_fail_one, setups=['fail', 'ok', 'ok'])
    scenario('B1: 3 gNB, aynı RAN_ID', binary, t_b1_multi_gnb)
    scenario('B2: tek yazmada 60 birleşik mesaj', binary, t_burst)
    scenario('B4/B5: AMF kaybı → NG Reset → yeniden bağlanma + replay', binary, t_amf_loss_reset_reconnect, env={'N2P_ON_AMF_LOSS': 'reset'})
    scenario('AMF kaybı → UE bazında UEContextReleaseCommand (varsayılan)', binary, t_amf_loss_release)
    scenario('AMF tamamen kapalı (silent mod)', binary, t_amf_down_silent, env={'N2P_ON_AMF_LOSS': 'silent'})
    scenario('C1: kapasite-ağırlıklı dağılım (100/100/50)', binary, t_weights, caps=[100, 100, 50])
    scenario('C2: 5G-S-TMSI affinity', binary, t_affinity, ptrs=[0, 1, 2])
    scenario('C2b: bayat S-TMSI öğrenme (yeniden başlamış AMF)', binary, t_stale_stmsi, ptrs=[0, 1, 2], cold=(2,), env={'N2P_STALE_MS': '60000'})
    scenario('C2c: N2P_AFFINITY=off', binary, t_affinity_off, ptrs=[0, 1, 2], env={'N2P_AFFINITY': 'off'})
    scenario('C4: drain / undrain / status', binary, t_drain, ptrs=[0, 1, 2], env={'N2P_CTL': '/tmp/n2p_test.ctl'})
    scenario('C4: uygulama düzeyi canlılık yoklaması (donmuş AMF)', binary, t_probe, env={'N2P_PROBE_MS': '300'})
    scenario('D: NGSetup zaman aşımı (donmuş AMF)', binary, t_setup_timeout, frozen=(1,), env={'N2P_SETUP_TIMEOUT_MS': '500'})
    mp = free_port()
    scenario('D: Prometheus metrikleri', binary, t_metrics, caps=[100, 100, 50], ptrs=[0, 1, 2], env={'N2P_METRICS': f'127.0.0.1:{mp}'})
    scenario('D: yük üreteci (3 gNB × 40 UE, aynı RAN id\'ler)', binary, t_loadgen, env={'N2P_METRICS': f'127.0.0.1:{free_port()}'})
    scenario('UEContextRelease → kayıt silme', binary, t_release)
    scenario('gNB kopuşu → UE temizliği', binary, t_gnb_disconnect)
    print(f'\n{checks} kontrol, {fails} hata'); sys.exit(1 if fails else 0)
