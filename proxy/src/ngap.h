// Minimal NGAP (APER) yardımcıları: PDU başlığı + IE gezinmesi + NG Reset kurucu.
// Bağımlılıksız (SCTP yok) — tests/ altında pycrate ile üretilen vektörlerle doğrulanır.
#pragma once
#include <cstdint>
#include <cstddef>
#include <vector>

namespace ngap {

enum Pdu { INITIATING = 0, SUCCESSFUL = 1, UNSUCCESSFUL = 2 };
enum Proc { AMF_CONFIG_UPDATE = 0, ERROR_INDICATION = 9, INITIAL_UE = 15, NG_RESET = 20, NG_SETUP = 21,
            PAGING = 24, RAN_CONFIG_UPDATE = 35, UE_CTX_RELEASE = 41, UE_CTX_RELEASE_REQUEST = 42 };
enum Ie { IE_AMF_UE_ID = 10, IE_5G_S_TMSI = 26, IE_UE_NGAP_IDS = 114, IE_RAN_UE_ID = 85, IE_REL_AMF_CAPACITY = 86, IE_SERVED_GUAMI_LIST = 96 };

struct Guami { int region, set, ptr; };

struct Info {
    bool ok = false;
    int pdu = -1, proc = -1;
    int64_t ran = -1;     // RAN_UE_NGAP_ID (-1: yok). 0 geçerli bir değerdir.
    int64_t amf = -1;     // AMF_UE_NGAP_ID
    bool has_stmsi = false;
    int stmsi_set = -1, stmsi_ptr = -1;
    uint32_t stmsi_tmsi = 0;           // 5G-TMSI (4 bayt)
    int capacity = -1;                 // NGSetupResponse: RelativeAMFCapacity (0..255)
    std::vector<Guami> guamis;         // NGSetupResponse: ServedGUAMIList
    bool ue_associated() const { return ran >= 0 || amf >= 0; }
};

// APER length determinant (<16384). Dönüş: tüketilen bayt, 0 = hata.
inline size_t read_len(const uint8_t* p, size_t n, size_t& out) {
    if (n < 1) return 0;
    if ((p[0] & 0x80) == 0) { out = p[0]; return 1; }
    if ((p[0] & 0xC0) == 0x80 && n >= 2) { out = ((p[0] & 0x3F) << 8) | p[1]; return 2; }
    return 0;  // parçalı (>=16K) desteklenmiyor
}

// INTEGER değeri: ilk bayt uzunluk başlığı. len_bits=2 (0..2^32-1, RAN id) veya 3 (0..2^40-1, AMF id).
inline bool read_uint(const uint8_t* v, size_t n, int len_bits, int64_t& out) {
    if (n < 2) return false;
    size_t k = (v[0] >> (8 - len_bits)) + 1;
    if (n != k + 1) return false;
    int64_t x = 0;
    for (size_t i = 0; i < k; i++) x = (x << 8) | v[1 + i];
    out = x;
    return true;
}

inline Info peek(const uint8_t* b, size_t n) {
    Info r;
    if (n < 5 || (b[0] & 0x9F) != 0) return r;      // ext biti veya ayrılmış bitler set -> tanınmayan PDU
    r.pdu = (b[0] >> 5) & 0x03;
    if (r.pdu > UNSUCCESSFUL) return r;
    r.proc = b[1];
    size_t vlen = 0, c = read_len(b + 3, n - 3, vlen);
    if (!c) return r;
    size_t pos = 3 + c;
    if (vlen > n - pos || vlen < 3) return r;
    const uint8_t* e = b + pos + vlen;
    const uint8_t* p = b + pos + 1;                 // SEQUENCE ön eki (ext biti)
    size_t cnt = (p[0] << 8) | p[1]; p += 2;
    r.ok = true;                                     // başlık geçerli; IE gezinmesi en iyi çaba
    for (size_t i = 0; i < cnt && e - p >= 4; i++) {
        int id = (p[0] << 8) | p[1];
        size_t l = 0, cc = read_len(p + 3, e - (p + 3), l);
        if (!cc || l > (size_t)(e - (p + 3 + cc))) break;
        const uint8_t* v = p + 3 + cc;
        if (id == IE_RAN_UE_ID) read_uint(v, l, 2, r.ran);
        else if (id == IE_AMF_UE_ID) read_uint(v, l, 3, r.amf);
        else if (id == IE_5G_S_TMSI && l == 7) {
            r.has_stmsi = true;
            r.stmsi_set = ((v[0] & 0x3F) << 4) | (v[1] >> 4);
            r.stmsi_ptr = ((v[1] & 0x0F) << 2) | (v[2] >> 6);
            r.stmsi_tmsi = ((uint32_t)v[3] << 24) | (v[4] << 16) | (v[5] << 8) | v[6];
        }
        else if (id == IE_UE_NGAP_IDS && l >= 4 && (v[0] & 0xF0) == 0) {   // UEContextReleaseCommand: uE-NGAP-ID-pair
            size_t n = ((v[0] >> 1) & 7) + 1;
            if (l >= 1 + n + 2) {
                int64_t a = 0; for (size_t k = 0; k < n; k++) a = (a << 8) | v[1 + k];
                size_t m = (v[1 + n] >> 6) + 1;
                if (l == 1 + n + 1 + m) { int64_t x = 0; for (size_t k = 0; k < m; k++) x = (x << 8) | v[2 + n + k]; r.amf = a; r.ran = x; }
            }
        }
        else if (id == IE_REL_AMF_CAPACITY && l == 1) r.capacity = v[0];
        else if (id == IE_SERVED_GUAMI_LIST && l >= 1) {       // count-1 (1 bayt) + 7 baytlık öğeler (PLMN 3, region 1, set/pointer 2)
            size_t n = (size_t)v[0] + 1, o = 1;
            for (size_t k = 0; k < n && o + 7 <= l; k++, o += 7) {
                if (v[o] != 0x00) break;                       // opsiyonel alan var -> sabit boyut varsayımı geçersiz
                int sp = (v[o + 5] << 8) | v[o + 6];
                r.guamis.push_back({v[o + 4], sp >> 6, sp & 0x3F});
            }
        }
        p = v + l;
    }
    return r;
}

inline void put_len(std::vector<uint8_t>& o, size_t n) {
    if (n < 128) o.push_back((uint8_t)n);
    else { o.push_back(0x80 | (n >> 8)); o.push_back(n & 0xFF); }
}

// AMF kaybında gNB'ye gönderilecek NG Reset (AMF-initiated).
// ids boş ya da çok büyükse "reset-all"; aksi halde yalnızca verilen RAN_UE_NGAP_ID'ler (partOfNG-Interface).
inline std::vector<uint8_t> build_ng_reset(const std::vector<uint32_t>& ids) {
    std::vector<uint8_t> rt;
    if (ids.empty() || ids.size() > 3000) {
        rt = {0x00};
    } else {
        rt.push_back(0x40);
        put_len(rt, ids.size());
        for (uint32_t id : ids) {
            size_t k = id < 0x100 ? 1 : id < 0x10000 ? 2 : id < 0x1000000 ? 3 : 4;
            rt.push_back(0x20 | ((k - 1) << 2));
            for (size_t i = k; i-- > 0;) rt.push_back((id >> (8 * i)) & 0xFF);
        }
    }
    std::vector<uint8_t> body = {0x00, 0x00, 0x02,                     // ön ek + IE sayısı
                                 0x00, 0x0F, 0x40, 0x01, 0x8A,         // Cause: misc/unspecified
                                 0x00, 0x58, 0x00};                    // ResetType
    put_len(body, rt.size());
    body.insert(body.end(), rt.begin(), rt.end());
    std::vector<uint8_t> out = {0x00, 0x14, 0x00};
    put_len(out, body.size());
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

inline size_t min_bytes(uint64_t v) { size_t k = 1; while (k < 8 && (v >> (8 * k))) k++; return k; }

// UE Context Release Command (AMF-initiated), UE-NGAP-ID-pair + Cause(radioNetwork/unspecified).
// AMF kaybında gNB'nin UE bağlamını bırakması için (UERANSIM NG Reset'i işlemiyor, bunu işliyor).
inline std::vector<uint8_t> build_ue_ctx_release_cmd(uint64_t amf_id, uint32_t ran_id) {
    size_t n = min_bytes(amf_id), m = min_bytes(ran_id);
    std::vector<uint8_t> pair = {(uint8_t)((n - 1) << 1)};
    for (size_t i = n; i-- > 0;) pair.push_back((amf_id >> (8 * i)) & 0xFF);
    pair.push_back((uint8_t)((m - 1) << 6));
    for (size_t i = m; i-- > 0;) pair.push_back((ran_id >> (8 * i)) & 0xFF);
    std::vector<uint8_t> body = {0x00, 0x00, 0x02, 0x00, 0x72, 0x00};
    put_len(body, pair.size());
    body.insert(body.end(), pair.begin(), pair.end());
    const uint8_t cause[] = {0x00, 0x0F, 0x40, 0x02, 0x00, 0x00};
    body.insert(body.end(), cause, cause + sizeof cause);
    std::vector<uint8_t> out = {0x00, 0x29, 0x00};
    put_len(out, body.size());
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

// Canlılık yoklaması: boş RANConfigurationUpdate (tüm IE'ler opsiyonel). Yanıtı gNB'ye iletilmez.
inline std::vector<uint8_t> build_probe() { return {0x00, 0x23, 0x00, 0x03, 0x00, 0x00, 0x00}; }

}  // namespace ngap
