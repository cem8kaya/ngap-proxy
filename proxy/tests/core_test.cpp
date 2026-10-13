#include <cstdio>
#include <cstdlib>
#include "../src/ngap.h"
#include "../src/lb_core.h"
#include "vectors.h"

static int fails = 0, checks = 0;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("FAIL %s:%d  ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

int main() {
    // ---- NGAP peek: pycrate vektörleri
    for (auto& v : VECS) {
        auto i = ngap::peek(v.pdu.data(), v.pdu.size());
        CHECK(i.ok, "%s: ok değil", v.name);
        CHECK(i.proc == v.proc, "%s: proc %d != %d", v.name, i.proc, v.proc);
        CHECK(i.ran == v.ran, "%s: ran %lld != %lld", v.name, (long long)i.ran, (long long)v.ran);
        CHECK(i.amf == v.amf, "%s: amf %lld != %lld", v.name, (long long)i.amf, (long long)v.amf);
        CHECK(i.has_stmsi == (v.stmsi_set >= 0), "%s: stmsi var/yok", v.name);
        if (v.stmsi_set >= 0) CHECK(i.stmsi_set == v.stmsi_set && i.stmsi_ptr == v.stmsi_ptr, "%s: stmsi %d/%d", v.name, i.stmsi_set, i.stmsi_ptr);
    }
    // kısa/bozuk girdiler çökmemeli
    uint8_t junk[] = {0x00, 0x0f, 0x40, 0x7f, 0x00, 0x00, 0x04, 0x00, 0x55};
    for (size_t n = 0; n <= sizeof junk; n++) ngap::peek(junk, n);
    for (auto& v : VECS) for (size_t n = 0; n < v.pdu.size(); n++) ngap::peek(v.pdu.data(), n);  // her kesilme noktası
    // ---- bozuk INTEGER uzunlukları: spec aralığını aşan değerler reddedilir (int64 taşması olmadan)
    for (auto& v : BAD_VECS) {
        auto i = ngap::peek(v.pdu.data(), v.pdu.size());
        CHECK(i.ok, "%s: başlık geçerli olmalı", v.name);
        CHECK(i.amf == v.amf, "%s: amf %lld != %lld", v.name, (long long)i.amf, (long long)v.amf);
        CHECK(i.ran == v.ran, "%s: ran %lld != %lld", v.name, (long long)i.ran, (long long)v.ran);
        for (size_t n = 0; n < v.pdu.size(); n++) ngap::peek(v.pdu.data(), n);
    }
    // ---- NG Reset kurucu (pycrate referansı)
    {
        auto all = ngap::build_ng_reset({});
        CHECK(all == NG_RESET_REF, "reset-all kodlaması farklı");
        for (auto& r : RESET_VECS) {
            auto got = ngap::build_ng_reset(r.ids);
            CHECK(got == r.pdu, "reset partOf n=%zu: kodlama farklı (len %zu vs %zu)", r.ids.size(), got.size(), r.pdu.size());
        }
    }
    for (auto& r : REL_VECS) {
        auto got = ngap::build_ue_ctx_release_cmd(r.amf, r.ran);
        CHECK(got == r.pdu, "release cmd amf=%llu ran=%u: kodlama farklı", (unsigned long long)r.amf, r.ran);
    }
    // ---- NGSetupResponse: kapasite + GUAMI listesi
    for (auto& v : SETUP_VECS) {
        auto i = ngap::peek(v.pdu.data(), v.pdu.size());
        CHECK(i.ok && i.proc == 21 && i.pdu == ngap::SUCCESSFUL, "setup resp başlık");
        CHECK(i.capacity == v.cap, "kapasite %d != %d", i.capacity, v.cap);
        bool same = i.guamis.size() == v.guamis.size();
        for (size_t k = 0; same && k < v.guamis.size(); k++)
            same = i.guamis[k].region == v.guamis[k][0] && i.guamis[k].set == v.guamis[k][1] && i.guamis[k].ptr == v.guamis[k][2];
        CHECK(same, "GUAMI listesi cap=%d: %zu öğe", v.cap, i.guamis.size());
    }
    CHECK(ngap::build_probe() == PROBE_REF, "probe kodlaması pycrate ile aynı değil");
    {   // UEContextReleaseCommand'taki UE-NGAP-ID çifti ayrıştırılır (AMF'in bağlamsız UE'yi bıraktırması tespiti için)
        for (auto& r : REL_VECS) {
            auto i = ngap::peek(r.pdu.data(), r.pdu.size());
            CHECK(i.ok && i.pdu == ngap::INITIATING && i.proc == 41 && i.amf == (int64_t)r.amf && i.ran == (int64_t)r.ran,
                  "release cmd peek amf=%llu ran=%u -> amf=%lld ran=%lld", (unsigned long long)r.amf, r.ran, (long long)i.amf, (long long)i.ran);
        }
        for (auto& v : VECS) if (std::string(v.name) == "initial_ue_stmsi") {
            auto i = ngap::peek(v.pdu.data(), v.pdu.size());
            CHECK(i.stmsi_tmsi == 0xdeadbeefu, "5G-TMSI 0x%08x", i.stmsi_tmsi);
        }
    }
    // ---- UE tablosu: B1 çakışma ve backend düşüşü
    {
        lb::UeTable t;
        t.put(5, 1, 0); t.put(9, 1, 1); t.put(13, 1, 2); t.put(5, 2, 1);
        CHECK(t.size() == 4, "4 UE, aynı RAN_ID=1 farklı gNB'lerde");
        CHECK(t.find(5, 1)->backend == 0 && t.find(9, 1)->backend == 1 && t.find(13, 1)->backend == 2, "gNB başına ayrı kayıt");
        CHECK(t.find(7, 1) == nullptr, "bilinmeyen gNB");
        auto d = t.drop_backend(5, 1);
        CHECK(d.size() == 1 && d[0].first == 2 && t.size() == 3, "backend 1 düşünce yalnızca o gNB'nin UE'si");
        t.erase_gnb(9);
        CHECK(t.find(9, 1) == nullptr && t.size() == 2, "gNB silinince UE'leri de gider");
    }
    // ---- Round-robin
    {
        lb::RoundRobin rr;
        std::vector<bool> up = {true, true, true};
        int seq[6]; for (int& s : seq) s = rr.pick(up);
        CHECK(seq[0] == 0 && seq[1] == 1 && seq[2] == 2 && seq[3] == 0 && seq[4] == 1 && seq[5] == 2, "RR sırası");
        up[1] = false;
        int a = rr.pick(up), b = rr.pick(up), c = rr.pick(up), d = rr.pick(up);
        CHECK(a != 1 && b != 1 && c != 1 && d != 1 && a != b && b != c, "ölü AMF atlanır ve dağılım dengeli: %d %d %d %d", a, b, c, d);
        up = {false, false, false};
        CHECK(rr.pick(up) == -1, "hiç sağlıklı yok");
    }
    // ---- Ağırlıklı RR: 100/100/50 -> 40/40/20; dışlanan aday seçilmez; ağırlık 0 seçilmez
    {
        lb::WeightedRR w; std::vector<bool> up = {true, true, true}; std::vector<int> wt = {100, 100, 50};
        int cnt[3] = {0, 0, 0}; for (int k = 0; k < 250; k++) cnt[w.pick(up, wt)]++;
        CHECK(cnt[0] == 100 && cnt[1] == 100 && cnt[2] == 50, "WRR 250 seçim: %d/%d/%d", cnt[0], cnt[1], cnt[2]);
        int first[4]; lb::WeightedRR w2; for (int& f : first) f = w2.pick(up, wt);
        CHECK(first[0] == 0 && first[1] == 1 && first[2] == 2 && first[3] == 0, "WRR ilk 4: %d %d %d %d", first[0], first[1], first[2], first[3]);
        up[1] = false; int c2[3] = {0, 0, 0}; for (int k = 0; k < 150; k++) c2[w.pick(up, wt)]++;
        CHECK(c2[1] == 0 && c2[0] == 100 && c2[2] == 50, "AMF-2 dışlanınca 100/0/50 oranı: %d/%d/%d", c2[0], c2[1], c2[2]);
        wt[2] = 0; up[1] = true; int c3[3] = {0, 0, 0}; for (int k = 0; k < 20; k++) c3[w.pick(up, wt)]++;
        CHECK(c3[2] == 0, "ağırlık 0 seçilmez");
        std::vector<bool> none = {false, false, false}; CHECK(w.pick(none, wt) == -1, "aday yok");
    }
    printf("%d kontrol, %d hata\n", checks, fails);
    return fails ? 1 : 0;
}
