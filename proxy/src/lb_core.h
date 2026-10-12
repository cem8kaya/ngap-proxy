// Yük dengeleme çekirdeği: UE tablosu (gnb, ran_id) + round-robin seçici. SCTP'den bağımsız.
#pragma once
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

namespace lb {

struct UeEntry { int backend = -1; int64_t amf_id = -1; bool via_affinity = false; uint32_t tmsi = 0; long long born_ms = 0; };

// Anahtar (gnb_id, ran_ue_ngap_id): farklı gNB'lerde aynı RAN_UE_NGAP_ID çakışmaz (B1).
class UeTable {
public:
    using Key = std::pair<int, uint32_t>;
    void put(int gnb, uint32_t ran, int backend) { t_[{gnb, ran}] = UeEntry{backend, -1}; }
    UeEntry* find(int gnb, uint32_t ran) { auto i = t_.find({gnb, ran}); return i == t_.end() ? nullptr : &i->second; }
    void erase(int gnb, uint32_t ran) { t_.erase({gnb, ran}); }
    void erase_gnb(int gnb) {
        for (auto i = t_.lower_bound({gnb, 0}); i != t_.end() && i->first.first == gnb;) i = t_.erase(i);
    }
    // backend düştüğünde: o backend'e bağlı UE'leri sil; (ran_id, amf_id) çiftlerini döndür (amf_id bilinmiyorsa -1).
    std::vector<std::pair<uint32_t, int64_t>> drop_backend(int gnb, int backend) {
        std::vector<std::pair<uint32_t, int64_t>> ids;
        for (auto i = t_.lower_bound({gnb, 0}); i != t_.end() && i->first.first == gnb;) {
            if (i->second.backend == backend) { ids.push_back({i->first.second, i->second.amf_id}); i = t_.erase(i); }
            else ++i;
        }
        return ids;
    }
    size_t size() const { return t_.size(); }
    size_t count_backend(int backend) const { size_t n = 0; for (auto& kv : t_) if (kv.second.backend == backend) n++; return n; }
private:
    std::map<Key, UeEntry> t_;
};

// Sabit sıralı round-robin: yalnızca gerçek UE seçiminde ilerler; sağlık değişse de dengeyi bozmaz.
class RoundRobin {
public:
    // up[i] true ise aday. Dönüş: seçilen indeks ya da -1.
    int pick(const std::vector<bool>& up) {
        size_t n = up.size();
        for (size_t k = 0; k < n; k++) {
            size_t i = (next_ + k) % n;
            if (up[i]) { next_ = (i + 1) % n; return (int)i; }
        }
        return -1;
    }
private:
    size_t next_ = 0;
};

// Düzgün (smooth) ağırlıklı round-robin (nginx algoritması). Ağırlık 0 -> aday değil.
// Yalnızca gerçek seçimde ilerler; down/drain adaylar dışlanır, dengeyi bozmaz.
class WeightedRR {
public:
    int pick(const std::vector<bool>& up, const std::vector<int>& w) {
        size_t n = up.size();
        if (cur_.size() != n) cur_.assign(n, 0);
        long total = 0; int best = -1;
        for (size_t i = 0; i < n; i++) {
            if (!up[i] || w[i] <= 0) continue;
            cur_[i] += w[i]; total += w[i];
            if (best < 0 || cur_[i] > cur_[best]) best = (int)i;
        }
        if (best >= 0) cur_[best] -= total;
        return best;
    }
private:
    std::vector<long> cur_;
};

}  // namespace lb
