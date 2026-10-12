// N2 LB Proxy — Faz B: gNB başına AMF havuzu, (gNB, RAN_UE_NGAP_ID) tabanlı UE tablosu,
// non-blocking bağlantı + yeniden bağlanma + NGSetup replay, SCTP heartbeat, PPID/stream koruma,
// AMF kaybında UE temizliği + NG Reset. Şeffaf mod: NGAP ID yeniden yazılmaz.
//
// Ortam değişkenleri:
//   N2P_LISTEN=127.0.0.50:38412        N2P_AMFS=127.0.0.5:38412,127.0.0.105:38412,127.0.0.205:38412
//   N2P_ON_AMF_LOSS=release|reset|silent       N2P_HB_MS=1000   (SCTP heartbeat aralığı)
//   N2P_PROBE_MS=0     (>0: uygulama düzeyi canlılık yoklaması; sessiz/donmuş AMF'i yakalar)
//   N2P_AFFINITY=on|off         (5G-S-TMSI affinity; varsayılan on)   N2P_STALE_MS=60000 (bayat S-TMSI hafızası)
//   N2P_SETUP_TIMEOUT_MS=5000   (bağlantı/NGSetup yanıtı bu sürede gelmezse backend düşürülür; donmuş AMF'e takılmayı önler)
//   N2P_METRICS=127.0.0.1:9190  (Prometheus metin biçimi; boş = kapalı)
//   N2P_CTL=/tmp/n2proxy.ctl   (komut dosyası: "drain N" | "undrain N" | "status"; N = 1..AMF sayısı)
//   -DN2P_TCP_TEST ile SCTP yerine TCP+2 bayt uzunluk öneki kullanılır (yalnızca yerel mantık testi için).
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdarg.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <time.h>
#include <sys/types.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#ifndef N2P_TCP_TEST
#include <netinet/sctp.h>
#endif

#include "lb_core.h"
#include "ngap.h"

// ------------------------------------------------------------------ log
static long long now_ms() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000LL + t.tv_nsec / 1000000; }
static void logf(const char* fmt, ...) {
    timespec t; clock_gettime(CLOCK_REALTIME, &t);
    tm tmv; localtime_r(&t.tv_sec, &tmv);
    char ts[32]; strftime(ts, sizeof ts, "%H:%M:%S", &tmv);
    printf("%s.%03ld ", ts, t.tv_nsec / 1000000);
    va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
    printf("\n");
}

// ------------------------------------------------------------------ taşıma katmanı (SCTP | test için TCP)
static const uint32_t NGAP_PPID = 60;
static int HB_MS = 1000;
static const size_t BUF = 65536;

#ifdef N2P_TCP_TEST
static std::unordered_map<int, std::string> rxbuf;
static int tp_socket() { return socket(AF_INET, SOCK_STREAM, IPPROTO_TCP); }
static void tp_tune(int fd) { int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one); }
static ssize_t tp_send(int fd, const uint8_t* b, size_t n, uint32_t, uint16_t) {
    std::string m(2 + n, 0); m[0] = n >> 8; m[1] = n & 0xFF; memcpy(&m[2], b, n);
    return send(fd, m.data(), m.size(), MSG_NOSIGNAL) == (ssize_t)m.size() ? (ssize_t)n : -1;
}
// dönüş: >0 mesaj, 0 kapandı, -1 hata/EAGAIN. notif/partial her zaman false.
static ssize_t tp_recv(int fd, uint8_t* b, size_t cap, uint32_t& ppid, uint16_t& stream, bool& notif, bool& partial) {
    ppid = NGAP_PPID; stream = 0; notif = partial = false;
    std::string& rb = rxbuf[fd];
    for (;;) {
        if (rb.size() >= 2) {
            size_t len = ((uint8_t)rb[0] << 8) | (uint8_t)rb[1];
            if (rb.size() >= 2 + len) { memcpy(b, rb.data() + 2, len < cap ? len : cap); rb.erase(0, 2 + len); return len; }
        }
        char tmp[4096]; ssize_t r = read(fd, tmp, sizeof tmp);
        if (r > 0) { rb.append(tmp, r); continue; }
        if (r == 0) { rxbuf.erase(fd); return 0; }
        return -1;
    }
}
#else
static int tp_socket() { return socket(AF_INET, SOCK_STREAM, IPPROTO_SCTP); }
// NODELAY + hızlı arıza tespiti (heartbeat, az yeniden iletim): sessiz kopma (blackhole) birkaç sn'de assoc'u düşürür.
static void tp_tune(int fd) {
    int one = 1; setsockopt(fd, IPPROTO_SCTP, SCTP_NODELAY, &one, sizeof one);
    sctp_paddrparams pp; memset(&pp, 0, sizeof pp);
    pp.spp_flags = SPP_HB_ENABLE; pp.spp_hbinterval = HB_MS; pp.spp_pathmaxrxt = 2;
    setsockopt(fd, IPPROTO_SCTP, SCTP_PEER_ADDR_PARAMS, &pp, sizeof pp);
    sctp_assocparams ap; memset(&ap, 0, sizeof ap); ap.sasoc_asocmaxrxt = 3;
    setsockopt(fd, IPPROTO_SCTP, SCTP_ASSOCINFO, &ap, sizeof ap);
    sctp_rtoinfo ri; memset(&ri, 0, sizeof ri); ri.srto_initial = 200; ri.srto_min = 100; ri.srto_max = 1000;
    setsockopt(fd, IPPROTO_SCTP, SCTP_RTOINFO, &ri, sizeof ri);
}
// PPID ve stream id aynen taşınır (B3).
// Karşı tarafın stream sayısı farklıysa (EINVAL) stream 0'a düşülür.
static ssize_t tp_send(int fd, const uint8_t* b, size_t n, uint32_t ppid, uint16_t stream) {
    ssize_t r = sctp_sendmsg(fd, b, n, nullptr, 0, htonl(ppid), 0, stream, 0, 0);
    if (r < 0 && errno == EINVAL && stream != 0) r = sctp_sendmsg(fd, b, n, nullptr, 0, htonl(ppid), 0, 0, 0, 0);
    return r;
}
static ssize_t tp_recv(int fd, uint8_t* b, size_t cap, uint32_t& ppid, uint16_t& stream, bool& notif, bool& partial) {
    sctp_sndrcvinfo si; memset(&si, 0, sizeof si); int flags = 0;
    ssize_t r = sctp_recvmsg(fd, b, cap, nullptr, nullptr, &si, &flags);
    ppid = ntohl(si.sinfo_ppid); stream = si.sinfo_stream;
    notif = flags & MSG_NOTIFICATION; partial = !(flags & MSG_EOR);
    return r;
}
#endif

static void set_nonblocking(int fd) { int o = fcntl(fd, F_GETFL); if (o >= 0) fcntl(fd, F_SETFL, o | O_NONBLOCK); }

// ------------------------------------------------------------------ durum
enum BeState { DOWN, CONNECTING, CONNECTED, WAIT_SETUP, READY, REJECTED };

struct Backend {
    int fd = -1;
    BeState st = DOWN;
    long long last_rx = 0, probe_at = 0, state_since = 0; bool probe_pending = false, ever_up = false;
    std::string ip; int port = 38412;
    long long next_retry = 0; int backoff_ms = 1000;
    bool up() const { return st == CONNECTED || st == WAIT_SETUP || st == READY || st == REJECTED; }
};

struct Gnb {
    int fd = -1;                                   // aynı zamanda gNB kimliği (kapanınca UE'leri silindiği için yeniden kullanım güvenli)
    std::vector<Backend> be;
    std::vector<uint8_t> setup_req; uint32_t setup_ppid = NGAP_PPID; uint16_t setup_stream = 0;
    std::map<int, std::set<int>> awaiting;         // proc -> yanıt bekleyen backend'ler
    std::set<int> forwarded;                       // yanıtı gNB'ye iletilmiş proc'lar
    std::map<int, int> amf_origin;                 // AMF-initiated proc -> başlatan backend
    std::set<uint32_t> orphan;                     // proxy'nin bizzat release ettiği (AMF'i kaybolmuş) RAN UE id'leri
};

struct FdRef { bool is_gnb; int gnb; int be; };

static std::vector<std::pair<std::string, int>> AMFS;
static std::string LISTEN_IP = "127.0.0.50"; static int LISTEN_PORT = 38412;
enum LossAction { LOSS_RELEASE, LOSS_RESET, LOSS_SILENT };
static LossAction ON_LOSS = LOSS_RELEASE;
static int ep = -1;
static std::unordered_map<int, FdRef> fdmap;
static std::unordered_map<int, Gnb> gnbs;
static lb::UeTable ues;
static lb::WeightedRR wrr;
static std::vector<int> AMF_CAP;                       // AMF başına RelativeAMFCapacity (NGSetupResponse'tan; -1 = bilinmiyor)
static std::vector<std::vector<ngap::Guami>> AMF_GUAMI; // AMF başına ServedGUAMIList
static std::vector<bool> AMF_DRAIN;                    // true: yeni UE almaz
static int PROBE_MS = 0;
static int SETUP_TIMEOUT_MS = 5000;
static bool AFFINITY_ON = true;
static int STALE_MS = 60000;
static std::unordered_map<uint64_t, long long> STALE_TMSI;   // (pointer<<32 | tmsi) -> geçerlilik sonu; AMF bu GUTI'yi tanımıyor
static std::string METRICS_ADDR;
static int mfd = -1;
static long long START_MS = 0;

struct Metrics {
    std::vector<unsigned long long> assign_wrr, assign_aff;      // AMF başına seçim sayısı (sebebe göre)
    unsigned long long drop_unknown_ue = 0, drop_unparsable = 0, reject_no_healthy = 0, send_fail = 0, failover = 0,
        reconnect = 0, probe_timeouts = 0, setup_timeouts = 0, release_cmds = 0, ng_resets = 0, orphan_release = 0,
        msgs_from_gnb = 0, msgs_from_amf = 0, gnb_accepted = 0,
        stale_learned = 0, fwd_to_gnb = 0, fwd_to_amf = 0, swallowed = 0, mcast_extra = 0, injected = 0, drop_backend_down = 0, drop_other = 0;
} M;
static std::string CTL_PATH = "/tmp/n2proxy.ctl";

static void ep_add(int fd, uint32_t ev) { epoll_event e{}; e.events = ev; e.data.fd = fd; epoll_ctl(ep, EPOLL_CTL_ADD, fd, &e); }
static void ep_mod(int fd, uint32_t ev) { epoll_event e{}; e.events = ev; e.data.fd = fd; epoll_ctl(ep, EPOLL_CTL_MOD, fd, &e); }
static void ep_close(int fd) { epoll_ctl(ep, EPOLL_CTL_DEL, fd, nullptr); fdmap.erase(fd); close(fd); }

// ------------------------------------------------------------------ backend yaşam döngüsü
static void set_state(Backend& b, BeState st) { b.st = st; b.state_since = now_ms(); }

static void be_schedule_retry(Backend& b) {
    set_state(b, DOWN); b.next_retry = now_ms() + b.backoff_ms; b.probe_at = 0; b.probe_pending = false;
    b.backoff_ms = b.backoff_ms * 2 > 10000 ? 10000 : b.backoff_ms * 2;
}

static void be_connect(Gnb& g, int idx) {
    Backend& b = g.be[idx];
    int fd = tp_socket();
    if (fd < 0) { be_schedule_retry(b); return; }
    set_nonblocking(fd); tp_tune(fd);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(b.port); inet_pton(AF_INET, b.ip.c_str(), &a.sin_addr);
    int rc = connect(fd, (sockaddr*)&a, sizeof a);
    if (rc < 0 && errno != EINPROGRESS) {
        logf("  -> AMF-%d (%s) ERISILEMIYOR! (%s)", idx + 1, b.ip.c_str(), strerror(errno));
        close(fd); be_schedule_retry(b); return;
    }
    b.fd = fd; set_state(b, CONNECTING);
    fdmap[fd] = {false, g.fd, idx};
    ep_add(fd, EPOLLIN | EPOLLOUT);
}

static void send_to_be(Gnb& g, int idx, const std::vector<uint8_t>& m, uint32_t ppid, uint16_t stream) {
    Backend& b = g.be[idx];
    if (!b.up()) { M.drop_backend_down++; return; }
    if (tp_send(b.fd, m.data(), m.size(), ppid, stream) < 0) {
        M.send_fail++;
        logf("[WARN] AMF-%d'ye gonderilemedi (errno=%d)", idx + 1, errno);
    } else M.fwd_to_amf++;
}

static void be_connected(Gnb& g, int idx) {
    Backend& b = g.be[idx];
    ep_mod(b.fd, EPOLLIN);
    set_state(b, CONNECTED); b.backoff_ms = 1000;
    if (b.ever_up) M.reconnect++;
    b.ever_up = true;
    logf("  -> AMF-%d (%s) BAĞLANDI (FD=%d)", idx + 1, b.ip.c_str(), b.fd);
    if (!g.setup_req.empty()) {                    // NGSetup replay (B5)
        send_to_be(g, idx, g.setup_req, g.setup_ppid, g.setup_stream); M.injected++;
        set_state(b, WAIT_SETUP); g.awaiting[ngap::NG_SETUP].insert(idx);
    }
}

static void gnb_send(Gnb& g, const std::vector<uint8_t>& m, uint32_t ppid, uint16_t stream) {
    if (tp_send(g.fd, m.data(), m.size(), ppid, stream) < 0) { M.send_fail++; logf("[WARN] gNB (FD=%d) gonderilemedi (errno=%d)", g.fd, errno); } else M.fwd_to_gnb++;
}

static void be_lost(Gnb& g, int idx, const char* why) {
    Backend& b = g.be[idx];
    M.failover++;
    logf("[ALARM] Bir AMF ile bağlantı koptu! (FD=%d) neden=%s", b.fd, why);
    if (b.fd >= 0) ep_close(b.fd);
    b.fd = -1;
    be_schedule_retry(b);
    for (auto& kv : g.awaiting) kv.second.erase(idx);
    for (auto it = g.amf_origin.begin(); it != g.amf_origin.end();) it = (it->second == idx) ? g.amf_origin.erase(it) : std::next(it);
    auto lost = ues.drop_backend(g.fd, idx);
    logf("[HA] AMF-%d pasife alindi. Kesintisizlik saglaniyor. (etkilenen UE=%zu, yeniden deneme %lld ms sonra)",
         idx + 1, lost.size(), (long long)(b.next_retry - now_ms()));
    if (lost.empty() || ON_LOSS == LOSS_SILENT) return;
    std::vector<uint32_t> reset_ids;
    for (auto& u : lost) {
        if (ON_LOSS == LOSS_RELEASE && u.second >= 0) {   // AMF id biliniyorsa: UE bazında release (UERANSIM bunu işler)
            gnb_send(g, ngap::build_ue_ctx_release_cmd((uint64_t)u.second, u.first), NGAP_PPID, 0);
            g.orphan.insert(u.first); M.release_cmds++; M.injected++;
        } else reset_ids.push_back(u.first);
    }
    if (lost.size() > reset_ids.size()) logf("[HA] gNB (FD=%d) icin %zu UE'ye UEContextReleaseCommand gonderildi", g.fd, lost.size() - reset_ids.size());
    if (!reset_ids.empty()) {
        gnb_send(g, ngap::build_ng_reset(reset_ids), NGAP_PPID, 0); M.ng_resets++; M.injected++;
        logf("[HA] gNB (FD=%d) icin NG Reset gonderildi (%zu UE)", g.fd, reset_ids.size());
    }
}

static void gnb_lost(Gnb& g) {
    logf("[PROXY] gNB baglantiyi kopardı (FD=%d)", g.fd);
    for (auto& b : g.be) if (b.fd >= 0) { ep_close(b.fd); b.fd = -1; }
    ues.erase_gnb(g.fd);
    int fd = g.fd;
    ep_close(fd);
    gnbs.erase(fd);
}

// ------------------------------------------------------------------ AMF seçimi
// 1) 5G-S-TMSI taşıyorsa ve sahibi AMF READY ise onu seç (GUTI affinity; drain'deki AMF'e de döner)
// 2) aksi halde READY ve drain'de olmayan AMF'ler arasında ağırlıklı RR (ağırlık = RelativeAMFCapacity)
// 3) hepsi drain'deyse yine READY olanlardan seç (reddetmektense)
static int select_amf(Gnb& g, const ngap::Info& in, char* how, size_t hn) {
    size_t n = g.be.size();
    std::vector<bool> ready(n), cand(n); std::vector<int> w(n);
    for (size_t i = 0; i < n; i++) {
        ready[i] = g.be[i].st == READY; cand[i] = ready[i] && !AMF_DRAIN[i];
        w[i] = AMF_CAP[i] < 0 ? 100 : AMF_CAP[i];
    }
    bool stale = false;
    if (in.has_stmsi && AFFINITY_ON) {
        auto it = STALE_TMSI.find(((uint64_t)in.stmsi_ptr << 32) | in.stmsi_tmsi);
        if (it != STALE_TMSI.end()) { if (it->second > now_ms()) stale = true; else STALE_TMSI.erase(it); }
    }
    if (in.has_stmsi && AFFINITY_ON && !stale)
        for (size_t i = 0; i < n; i++) {
            if (!ready[i]) continue;
            for (auto& gm : AMF_GUAMI[i])
                if (gm.set == in.stmsi_set && gm.ptr == in.stmsi_ptr) {
                    snprintf(how, hn, "S-TMSI affinity set=%d ptr=%d", gm.set, gm.ptr); return (int)i;
                }
        }
    int idx = wrr.pick(cand, w);
    if (idx < 0) idx = wrr.pick(ready, w);
    snprintf(how, hn, "%s%s", !in.has_stmsi ? "" : stale ? "S-TMSI bayat (sahibi AMF baglami bilmiyor), " : !AFFINITY_ON ? "affinity kapali, " : "S-TMSI sahibi AMF yok, ", idx >= 0 && !cand[idx] ? "Agirlikli RR (hepsi drain)" : "Agirlikli RR");
    return idx;
}

// ------------------------------------------------------------------ mesaj işleme
static bool ue_forward(Gnb& g, const ngap::Info& in, const std::vector<uint8_t>& m, uint32_t ppid, uint16_t stream) {
    lb::UeEntry* e = ues.find(g.fd, (uint32_t)in.ran);
    if (!e && in.proc == ngap::UE_CTX_RELEASE && in.pdu == ngap::SUCCESSFUL && g.orphan.erase((uint32_t)in.ran)) {
        M.swallowed++; logf("[HA] AMF'i kaybolan UE (RAN_ID=%lld) release tamamlandi", (long long)in.ran); return true;
    }
    if (!e && in.proc == ngap::UE_CTX_RELEASE_REQUEST && in.pdu == ngap::INITIATING && in.amf >= 0) {   // yetim UE: gNB'ye release komutu ver
        gnb_send(g, ngap::build_ue_ctx_release_cmd((uint64_t)in.amf, (uint32_t)in.ran), NGAP_PPID, 0);
        g.orphan.insert((uint32_t)in.ran); M.orphan_release++; M.injected++; M.swallowed++;
        logf("[HA] yetim UE (RAN_ID=%lld) UEContextReleaseRequest'i icin release komutu gonderildi", (long long)in.ran); return true;
    }
    if (!e) { M.drop_unknown_ue++; logf("[WARN] bilinmeyen UE (GNB_FD=%d RAN_ID=%lld proc=%d) — mesaj dusuruldu", g.fd, (long long)in.ran, in.proc); return false; }
    if (in.amf >= 0) e->amf_id = in.amf;
    send_to_be(g, e->backend, m, ppid, stream);
    return true;
}

static void from_gnb(Gnb& g, const std::vector<uint8_t>& m, uint32_t ppid, uint16_t stream) {
    ngap::Info in = ngap::peek(m.data(), m.size());
    if (!in.ok) { M.drop_unparsable++; logf("[WARN] gNB'den cozulemeyen NGAP (%zu bayt) dusuruldu", m.size()); return; }

    if (in.pdu == ngap::INITIATING && in.proc == ngap::INITIAL_UE) {
        if (in.ran < 0) { M.drop_unparsable++; logf("[WARN] InitialUEMessage'da RAN_UE_NGAP_ID yok — dusuruldu"); return; }
        char how[96];
        int idx = select_amf(g, in, how, sizeof how);
        if (idx < 0) { M.reject_no_healthy++; logf("[CRITICAL] HIC SAGLIKLI AMF KALMADI! UE reddediliyor. (GNB_FD=%d RAN_ID=%lld)", g.fd, (long long)in.ran); return; }
        ues.put(g.fd, (uint32_t)in.ran, idx);
        if (lb::UeEntry* ne = ues.find(g.fd, (uint32_t)in.ran)) { ne->via_affinity = !strncmp(how, "S-TMSI affinity", 15); ne->tmsi = in.stmsi_tmsi; ne->born_ms = now_ms(); }
        if (!strncmp(how, "S-TMSI affinity", 15)) M.assign_aff[idx]++; else M.assign_wrr[idx]++;
        logf("[LB] YENI UE (GNB_FD=%d RAN_ID=%lld) -> AMF-%d secildi! (%s)", g.fd, (long long)in.ran, idx + 1, how);
        send_to_be(g, idx, m, ppid, stream);
        return;
    }
    if (in.ue_associated()) {
        bool fwd = ue_forward(g, in, m, ppid, stream);
        if (fwd && in.pdu == ngap::SUCCESSFUL && in.proc == ngap::UE_CTX_RELEASE && in.ran >= 0) ues.erase(g.fd, (uint32_t)in.ran);
        return;
    }
    if (in.pdu == ngap::INITIATING) {              // UE'ye bağlı olmayan gNB prosedürü (NGSetup, RANConfigurationUpdate, ...): çoğalt
        if (in.proc == ngap::NG_SETUP) { g.setup_req = m; g.setup_ppid = ppid; g.setup_stream = stream; }
        g.forwarded.erase(in.proc); g.awaiting[in.proc].clear();
        size_t sent_n = 0;
        for (size_t i = 0; i < g.be.size(); i++) {
            Backend& b = g.be[i];
            if (b.st == CONNECTED || b.st == WAIT_SETUP || b.st == READY) {
                send_to_be(g, i, m, ppid, stream); sent_n++;
                g.awaiting[in.proc].insert(i);
                if (in.proc == ngap::NG_SETUP && b.st == CONNECTED) set_state(b, WAIT_SETUP);
            }
        }
        if (sent_n > 1) M.mcast_extra += sent_n - 1; else if (sent_n == 0) M.swallowed++;   // henüz backend yok: saklandı, bağlanınca replay edilir (injected)
        return;
    }
    // gNB'nin AMF-initiated prosedüre yanıtı (ör. AMFConfigurationUpdateAcknowledge): başlatan AMF'e
    auto o = g.amf_origin.find(in.proc);
    if (o == g.amf_origin.end()) { M.drop_other++; logf("[WARN] yanıt icin kaynak AMF bilinmiyor (proc=%d)", in.proc); return; }
    send_to_be(g, o->second, m, ppid, stream);
}

static void from_be(Gnb& g, int idx, const std::vector<uint8_t>& m, uint32_t ppid, uint16_t stream) {
    ngap::Info in = ngap::peek(m.data(), m.size());
    if (!in.ok) { M.drop_unparsable++; logf("[WARN] AMF-%d'den cozulemeyen NGAP — dusuruldu", idx + 1); return; }
    Backend& b = g.be[idx];

    if (in.pdu == ngap::INITIATING && in.proc == ngap::UE_CTX_RELEASE) {     // AMF -> gNB release komutu (UE-NGAP-ID çifti)
        lb::UeEntry* re = in.ran >= 0 ? ues.find(g.fd, (uint32_t)in.ran) : nullptr;
        if (re && re->backend == idx && re->via_affinity && re->tmsi && now_ms() - re->born_ms < 1500) {
            // affinity ile gönderilen UE'yi AMF hemen bıraktırdı: bağlamı yok (yeniden başlamış) -> bu S-TMSI için affinity'yi geçici kapat
            int ptr = -1; for (auto& gm : AMF_GUAMI[idx]) { ptr = gm.ptr; break; }
            STALE_TMSI[((uint64_t)(ptr < 0 ? 0 : ptr) << 32) | re->tmsi] = now_ms() + STALE_MS; M.stale_learned++;
            logf("[LB] AMF-%d S-TMSI 0x%08x icin baglam bilmiyor (hemen release) — %d ms boyunca affinity yerine WRR", idx + 1, re->tmsi, STALE_MS);
        }
        g.amf_origin[in.proc] = idx;
        gnb_send(g, m, ppid, stream);
        return;
    }
    if (in.ue_associated()) {
        if (in.ran >= 0) {
            lb::UeEntry* e = ues.find(g.fd, (uint32_t)in.ran);
            if (!e) { M.drop_unknown_ue++; logf("[WARN] AMF-%d bilinmeyen UE icin mesaj gonderdi (RAN_ID=%lld proc=%d) — dusuruldu", idx + 1, (long long)in.ran, in.proc); return; }
            if (e->backend != idx) { M.drop_other++; logf("[WARN] AMF-%d baska AMF'e ait UE icin mesaj gonderdi (RAN_ID=%lld) — dusuruldu", idx + 1, (long long)in.ran); return; }
            if (in.amf >= 0) e->amf_id = in.amf;
        }
        gnb_send(g, m, ppid, stream);
        return;
    }
    if (in.pdu == ngap::INITIATING) {              // AMF-initiated, UE'siz (Paging, AMFConfigurationUpdate, NGReset, ErrorIndication ...)
        g.amf_origin[in.proc] = idx;
        gnb_send(g, m, ppid, stream);
        return;
    }
    // gNB-initiated UE'siz prosedürün yanıtı: ilk başarılı yanıt iletilir, kalanlar yutulur (B7)
    int proc = in.proc;
    if (proc == ngap::RAN_CONFIG_UPDATE && b.probe_pending && !g.awaiting[proc].count(idx)) { b.probe_pending = false; M.swallowed++; return; }  // canlılık yoklaması yanıtı
    if (proc == ngap::NG_SETUP && in.pdu == ngap::SUCCESSFUL) {
        AMF_CAP[idx] = in.capacity; AMF_GUAMI[idx] = in.guamis;
        logf("[HA] AMF-%d kapasite=%d GUAMI=%zu adet%s", idx + 1, in.capacity, in.guamis.size(),
             in.guamis.empty() ? "" : (std::string(" (ilk: set=") + std::to_string(in.guamis[0].set) + " ptr=" + std::to_string(in.guamis[0].ptr) + ")").c_str());
    }
    auto& aw = g.awaiting[proc]; aw.erase(idx);
    if (proc == ngap::NG_SETUP) {
        set_state(b, (in.pdu == ngap::SUCCESSFUL) ? READY : REJECTED);
        logf("[HA] AMF-%d NGSetup %s — %s", idx + 1, b.st == READY ? "tamam" : "REDDEDILDI", b.st == READY ? "havuzda (READY)" : "havuz disi");
    }
    if (g.forwarded.count(proc)) { M.swallowed++; logf("  -> AMF-%d yaniti (proc=%d) gNB'ye zaten iletilmisti — yutuldu", idx + 1, proc); return; }
    if (in.pdu == ngap::SUCCESSFUL || aw.empty()) { g.forwarded.insert(proc); gnb_send(g, m, ppid, stream); }
    else { M.swallowed++; logf("  -> AMF-%d reddetti (proc=%d); diger AMF'ler bekleniyor", idx + 1, proc); }
}

// ------------------------------------------------------------------ olay döngüsü
static void gnb_accept(int lfd) {
    for (;;) {
        sockaddr_in ca; socklen_t l = sizeof ca;
        int fd = accept(lfd, (sockaddr*)&ca, &l);
        if (fd < 0) return;
        set_nonblocking(fd); tp_tune(fd);
        Gnb& g = gnbs[fd]; g.fd = fd; M.gnb_accepted++;
        fdmap[fd] = {true, fd, -1};
        ep_add(fd, EPOLLIN);
        logf("[PROXY] Yeni gNB bağlandı! (FD=%d) AMF'lere (Havuz) bağlanılıyor...", fd);
        g.be.resize(AMFS.size());
        for (size_t i = 0; i < AMFS.size(); i++) { g.be[i].ip = AMFS[i].first; g.be[i].port = AMFS[i].second; be_connect(g, i); }
    }
}

static void drain(int fd) {
    static std::vector<uint8_t> buf(BUF);
#ifdef N2P_TCP_TEST
    const int kMaxDrain = 1 << 30;       // test taşıması mesajları kullanıcı alanında tamponlar -> çekirdek yeniden uyarmaz, sınırsız boşalt
#else
    const int kMaxDrain = 64;            // adalet sınırı: level-triggered epoll kalan mesajlar için yeniden uyarır
#endif
    for (int k = 0; k < kMaxDrain; k++) {
        auto it = fdmap.find(fd); if (it == fdmap.end()) return;            // işlem sırasında kapanmış olabilir
        FdRef ref = it->second;
        auto gi = gnbs.find(ref.gnb); if (gi == gnbs.end()) return;
        Gnb& g = gi->second;
        uint32_t ppid; uint16_t stream; bool notif, partial;
        ssize_t n = tp_recv(fd, buf.data(), buf.size(), ppid, stream, notif, partial);
        if (n > 0) {
            if (!ref.is_gnb) { Backend& bb = g.be[ref.be]; bb.last_rx = now_ms(); bb.probe_at = 0; }
            if (notif) continue;
            if (partial) { logf("[WARN] %zd bayttan buyuk SCTP mesaji — dusuruldu", (size_t)n); continue; }
            std::vector<uint8_t> m(buf.begin(), buf.begin() + n);
            if (ref.is_gnb) M.msgs_from_gnb++; else M.msgs_from_amf++;
            if (ref.is_gnb) from_gnb(g, m, ppid, stream); else from_be(g, ref.be, m, ppid, stream);
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        if (n < 0 && errno == EINTR) continue;
        if (ref.is_gnb) gnb_lost(g); else be_lost(g, ref.be, n == 0 ? "close" : strerror(errno));
        return;
    }
}

static void metrics_serve(int lfd);
static void on_event(int fd, uint32_t ev, int lfd) {
    if (fd == lfd) { gnb_accept(lfd); return; }
    if (fd == mfd) { metrics_serve(mfd); return; }
    auto it = fdmap.find(fd); if (it == fdmap.end()) return;
    FdRef ref = it->second;
    auto gi = gnbs.find(ref.gnb); if (gi == gnbs.end()) return;
    Gnb& g = gi->second;
    if (!ref.is_gnb && g.be[ref.be].st == CONNECTING) {
        int err = 0; socklen_t l = sizeof err;
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &l);
        if (err || (ev & (EPOLLERR | EPOLLHUP))) { logf("  -> AMF-%d (%s) ERISILEMIYOR! (%s)", ref.be + 1, g.be[ref.be].ip.c_str(), strerror(err ? err : ECONNREFUSED)); ep_close(fd); g.be[ref.be].fd = -1; be_schedule_retry(g.be[ref.be]); return; }
        if (ev & EPOLLOUT) be_connected(g, ref.be);
        if (!(ev & EPOLLIN)) return;
    }
    if (ev & (EPOLLIN | EPOLLERR | EPOLLHUP | EPOLLRDHUP)) drain(fd);
}

// ------------------------------------------------------------------ Prometheus metrikleri
static std::string render_metrics() {
    std::string o; char b[256];
    auto line = [&](const char* fmt, auto... a) { snprintf(b, sizeof b, fmt, a...); o += b; };
    static const char* SN[] = {"DOWN", "CONNECTING", "CONNECTED", "WAIT_SETUP", "READY", "REJECTED"};
    o += "# TYPE n2_uptime_seconds gauge\n"; line("n2_uptime_seconds %lld\n", (now_ms() - START_MS) / 1000);
    o += "# TYPE n2_gnb_connections gauge\n"; line("n2_gnb_connections %zu\n", gnbs.size());
    o += "# TYPE n2_ue_contexts gauge\n"; line("n2_ue_contexts %zu\n", ues.size());
    for (size_t i = 0; i < AMFS.size(); i++) {
        line("n2_ue_contexts{amf=\"%zu\"} %zu\n", i + 1, ues.count_backend((int)i));
        line("n2_amf_capacity{amf=\"%zu\"} %d\n", i + 1, AMF_CAP[i]);
        line("n2_amf_drain{amf=\"%zu\"} %d\n", i + 1, (int)AMF_DRAIN[i]);
    }
    o += "# TYPE n2_amf_ready gauge\n";
    for (auto& kv : gnbs)
        for (size_t i = 0; i < kv.second.be.size(); i++)
            line("n2_amf_ready{gnb=\"%d\",amf=\"%zu\",state=\"%s\"} %d\n", kv.first, i + 1, SN[kv.second.be[i].st], (int)(kv.second.be[i].st == READY));
    o += "# TYPE n2_lb_assignments_total counter\n";
    for (size_t i = 0; i < AMFS.size(); i++) {
        line("n2_lb_assignments_total{amf=\"%zu\",reason=\"wrr\"} %llu\n", i + 1, M.assign_wrr[i]);
        line("n2_lb_assignments_total{amf=\"%zu\",reason=\"affinity\"} %llu\n", i + 1, M.assign_aff[i]);
    }
    o += "# TYPE n2_messages_total counter\n";
    line("n2_messages_total{dir=\"gnb_to_proxy\"} %llu\n", M.msgs_from_gnb);
    line("n2_messages_total{dir=\"amf_to_proxy\"} %llu\n", M.msgs_from_amf);
    line("n2_messages_total{dir=\"proxy_to_gnb\"} %llu\n", M.fwd_to_gnb);
    line("n2_messages_total{dir=\"proxy_to_amf\"} %llu\n", M.fwd_to_amf);
    o += "# TYPE n2_messages_consumed_total counter\n";             // mesaj korunumu: rx + mcast_extra + injected == fwd + swallowed + drops
    line("n2_messages_consumed_total{kind=\"swallowed\"} %llu\n", M.swallowed);
    line("n2_messages_consumed_total{kind=\"mcast_extra\"} %llu\n", M.mcast_extra);
    line("n2_messages_consumed_total{kind=\"injected\"} %llu\n", M.injected);
    o += "# TYPE n2_dropped_total counter\n";
    line("n2_dropped_total{reason=\"unknown_ue\"} %llu\n", M.drop_unknown_ue);
    line("n2_dropped_total{reason=\"unparsable\"} %llu\n", M.drop_unparsable);
    line("n2_dropped_total{reason=\"no_healthy_amf\"} %llu\n", M.reject_no_healthy);
    line("n2_dropped_total{reason=\"send_failed\"} %llu\n", M.send_fail);
    line("n2_dropped_total{reason=\"backend_down\"} %llu\n", M.drop_backend_down);
    line("n2_dropped_total{reason=\"other\"} %llu\n", M.drop_other);
    o += "# TYPE n2_ha_events_total counter\n";
    line("n2_ha_events_total{event=\"amf_failover\"} %llu\n", M.failover);
    line("n2_ha_events_total{event=\"amf_reconnect\"} %llu\n", M.reconnect);
    line("n2_ha_events_total{event=\"probe_timeout\"} %llu\n", M.probe_timeouts);
    line("n2_ha_events_total{event=\"setup_timeout\"} %llu\n", M.setup_timeouts);
    line("n2_ha_events_total{event=\"ue_release_cmd\"} %llu\n", M.release_cmds);
    line("n2_ha_events_total{event=\"ng_reset\"} %llu\n", M.ng_resets);
    line("n2_ha_events_total{event=\"orphan_release\"} %llu\n", M.orphan_release);
    line("n2_ha_events_total{event=\"stale_stmsi_learned\"} %llu\n", M.stale_learned);
    o += "# TYPE n2_gnb_accepted_total counter\n"; line("n2_gnb_accepted_total %llu\n", M.gnb_accepted);
    return o;
}

static void metrics_serve(int lfd) {
    for (;;) {
        int c = accept(lfd, nullptr, nullptr);
        if (c < 0) return;
        char req[512]; recv(c, req, sizeof req, MSG_DONTWAIT);     // istek içeriği önemsiz: her yola metrik döner
        std::string body = render_metrics();
        std::string h = "HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4\r\nContent-Length: " + std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n";
        std::string all = h + body;
        send(c, all.data(), all.size(), MSG_NOSIGNAL);
        close(c);
    }
}

static void ctl_poll() {
    FILE* f = fopen(CTL_PATH.c_str(), "r");
    if (!f) return;
    char line[128];
    while (fgets(line, sizeof line, f)) {
        char cmd[32] = {0}; int n = 0; int got = sscanf(line, "%31s %d", cmd, &n);
        if (got >= 1 && !strcmp(cmd, "status")) {
            logf("[CTL] durum: gNB=%zu UE=%zu", gnbs.size(), ues.size());
            for (size_t i = 0; i < AMFS.size(); i++) {
                const char* st = "-";
                static const char* N[] = {"DOWN", "CONNECTING", "CONNECTED", "WAIT_SETUP", "READY", "REJECTED"};
                if (!gnbs.empty()) st = N[gnbs.begin()->second.be[i].st];
                logf("[CTL]   AMF-%zu %s:%d durum=%s kapasite=%d drain=%s", i + 1, AMFS[i].first.c_str(), AMFS[i].second, st, AMF_CAP[i], AMF_DRAIN[i] ? "evet" : "hayir");
            }
        } else if (got == 2 && (!strcmp(cmd, "drain") || !strcmp(cmd, "undrain")) && n >= 1 && (size_t)n <= AMFS.size()) {
            AMF_DRAIN[n - 1] = !strcmp(cmd, "drain");
            logf("[CTL] AMF-%d %s", n, AMF_DRAIN[n - 1] ? "drain moduna alindi (yeni UE almaz)" : "drain'den cikarildi");
        } else logf("[CTL] taninmayan komut: %s", line);
    }
    fclose(f); unlink(CTL_PATH.c_str());
}

static void tick() {
    long long t = now_ms();
    for (auto& kv : gnbs)
        for (size_t i = 0; i < kv.second.be.size(); i++) {
            Backend& b = kv.second.be[i];
            if (b.st == DOWN && t >= b.next_retry) { be_connect(kv.second, i); continue; }
            if ((b.st == CONNECTING || b.st == WAIT_SETUP) && t - b.state_since >= SETUP_TIMEOUT_MS) {
                M.setup_timeouts++; be_lost(kv.second, i, b.st == CONNECTING ? "baglanti zaman asimi" : "NGSetup zaman asimi"); continue;
            }
            if (PROBE_MS > 0 && b.st == READY) {       // uygulama düzeyi canlılık: sessiz AMF'e yoklama, yanıt gelmezse düşür
                if (b.probe_at == 0 && t - b.last_rx >= PROBE_MS) {
                    send_to_be(kv.second, i, ngap::build_probe(), NGAP_PPID, 0); M.injected++;
                    b.probe_at = t; b.probe_pending = true;
                } else if (b.probe_at != 0 && t - b.probe_at >= PROBE_MS) { M.probe_timeouts++; be_lost(kv.second, i, "probe zaman asimi"); }
            }
        }
    ctl_poll();
}

static std::vector<std::pair<std::string, int>> parse_list(const char* s) {
    std::vector<std::pair<std::string, int>> out; std::string x(s);
    size_t p = 0;
    while (p < x.size()) {
        size_t q = x.find(',', p); if (q == std::string::npos) q = x.size();
        std::string it = x.substr(p, q - p); size_t c = it.find(':');
        out.push_back({it.substr(0, c), c == std::string::npos ? 38412 : atoi(it.c_str() + c + 1)});
        p = q + 1;
    }
    return out;
}

int main() {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    signal(SIGPIPE, SIG_IGN);
    AMFS = parse_list(getenv("N2P_AMFS") ? getenv("N2P_AMFS") : "127.0.0.5:38412,127.0.0.105:38412,127.0.0.205:38412");
    if (const char* l = getenv("N2P_LISTEN")) { auto v = parse_list(l); LISTEN_IP = v[0].first; LISTEN_PORT = v[0].second; }
    if (const char* a = getenv("N2P_ON_AMF_LOSS")) ON_LOSS = !strcmp(a, "silent") ? LOSS_SILENT : !strcmp(a, "reset") ? LOSS_RESET : LOSS_RELEASE;
    if (const char* h = getenv("N2P_HB_MS")) HB_MS = atoi(h);
    if (const char* h = getenv("N2P_PROBE_MS")) PROBE_MS = atoi(h);
    if (const char* h = getenv("N2P_CTL")) CTL_PATH = h;
    if (const char* h = getenv("N2P_SETUP_TIMEOUT_MS")) SETUP_TIMEOUT_MS = atoi(h);
    if (const char* h = getenv("N2P_METRICS")) METRICS_ADDR = h;
    if (const char* h = getenv("N2P_AFFINITY")) AFFINITY_ON = strcmp(h, "off") != 0;
    if (const char* h = getenv("N2P_STALE_MS")) STALE_MS = atoi(h);
    START_MS = now_ms();
    M.assign_wrr.assign(AMFS.size(), 0); M.assign_aff.assign(AMFS.size(), 0);
    AMF_CAP.assign(AMFS.size(), -1); AMF_GUAMI.assign(AMFS.size(), {}); AMF_DRAIN.assign(AMFS.size(), false);

    int lfd = tp_socket();
    if (lfd < 0) { logf("[ERROR] Soket olusturulamadi (errno=%d)", errno); return 1; }
    int one = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa{}; sa.sin_family = AF_INET; sa.sin_port = htons(LISTEN_PORT); inet_pton(AF_INET, LISTEN_IP.c_str(), &sa.sin_addr);
    if (bind(lfd, (sockaddr*)&sa, sizeof sa) < 0) { logf("[ERROR] %s:%d bind edilemedi: %s", LISTEN_IP.c_str(), LISTEN_PORT, strerror(errno)); return 1; }
    listen(lfd, SOMAXCONN); set_nonblocking(lfd);
    ep = epoll_create1(0);
    ep_add(lfd, EPOLLIN);
    logf("[PROXY] Master Load Balancer devrede... (IP: %s, PORT: %d, AMF sayisi: %zu, AMF kaybinda: %s, HB: %d ms, probe: %d ms)",
         LISTEN_IP.c_str(), LISTEN_PORT, AMFS.size(), ON_LOSS == LOSS_RELEASE ? "UE release" : ON_LOSS == LOSS_RESET ? "NG Reset" : "sessiz", HB_MS, PROBE_MS);

    if (!METRICS_ADDR.empty()) {
        auto v = parse_list(METRICS_ADDR.c_str());
        mfd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        int o2 = 1; setsockopt(mfd, SOL_SOCKET, SO_REUSEADDR, &o2, sizeof o2);
        sockaddr_in ma{}; ma.sin_family = AF_INET; ma.sin_port = htons(v[0].second); inet_pton(AF_INET, v[0].first.c_str(), &ma.sin_addr);
        if (bind(mfd, (sockaddr*)&ma, sizeof ma) < 0 || listen(mfd, 16) < 0) { logf("[ERROR] metrik %s dinlenemedi: %s", METRICS_ADDR.c_str(), strerror(errno)); mfd = -1; }
        else { set_nonblocking(mfd); ep_add(mfd, EPOLLIN); logf("[PROXY] Prometheus metrikleri: http://%s/metrics", METRICS_ADDR.c_str()); }
    }

    epoll_event evs[64];
    for (;;) {
        int n = epoll_wait(ep, evs, 64, 200);
        for (int i = 0; i < n; i++) on_event(evs[i].data.fd, evs[i].events, lfd);
        tick();
    }
}
