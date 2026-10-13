"""NGAP test vektörleri üretici (pycrate, APER). Çıktı: vectors.h  — `pip install pycrate`"""
from pycrate_asn1dir import NGAP
P = NGAP.NGAP_PDU_Descriptions.NGAP_PDU

def enc(kind, code, crit, name, ies):
    P.set_val((kind, {'procedureCode': code, 'criticality': crit,
                      'value': (name, {'protocolIEs': ies})}))
    return P.to_aper()

def ie(i, crit, tname, v): return {'id': i, 'criticality': crit, 'value': (tname, v)}
NAS = b'\x7e\x00\x41\x79\x00\x0d\x01\x99\xf9\x07\x00\x00\x00\x00\x00\x00\x00\x00\x01\x2e\x04\xf0\xf0\xf0\xf0'
ULI = ('userLocationInformationNR', {'nR-CGI': {'pLMNIdentity': b'\x99\xf9\x07', 'nRCellIdentity': (0x10, 36)},
                                    'tAI': {'pLMNIdentity': b'\x99\xf9\x07', 'tAC': b'\x00\x00\x01'}})
V = {}
for n, rid in [("rid0", 0), ("rid1", 1), ("rid255", 255), ("rid256", 256), ("rid65535", 65535),
               ("rid65536", 65536), ("rid_max", 2**32 - 1)]:
    ies = [ie(85, 'reject', 'RAN-UE-NGAP-ID', rid), ie(38, 'reject', 'NAS-PDU', NAS),
           ie(121, 'reject', 'UserLocationInformation', ULI), ie(90, 'ignore', 'RRCEstablishmentCause', 'mo-Signalling')]
    V["initial_ue_" + n] = (enc('initiatingMessage', 15, 'ignore', 'InitialUEMessage', ies), 15, rid, None)
# 5G-S-TMSI (id 26): AMF Set ID 10 bit, AMF Pointer 6 bit, 5G-TMSI 4 byte
ies = [ie(85, 'reject', 'RAN-UE-NGAP-ID', 7), ie(38, 'reject', 'NAS-PDU', NAS),
       ie(121, 'reject', 'UserLocationInformation', ULI),
       ie(26, 'reject', 'FiveG-S-TMSI', {'aMFSetID': (1, 10), 'aMFPointer': (2, 6), 'fiveG-TMSI': b'\xde\xad\xbe\xef'}),
       ie(90, 'ignore', 'RRCEstablishmentCause', 'mo-Signalling')]
V["initial_ue_stmsi"] = (enc('initiatingMessage', 15, 'ignore', 'InitialUEMessage', ies), 15, 7, (1, 2))
for n, (a, r) in [("ul_small", (1, 1)), ("ul_big", (70000, 300)), ("ul_zero", (0, 0)), ("ul_amf_max", (2**40 - 1, 5))]:
    ies = [ie(10, 'reject', 'AMF-UE-NGAP-ID', a), ie(85, 'reject', 'RAN-UE-NGAP-ID', r),
           ie(38, 'reject', 'NAS-PDU', NAS), ie(121, 'reject', 'UserLocationInformation', ULI)]
    V["uplink_nas_" + n] = (enc('initiatingMessage', 46, 'ignore', 'UplinkNASTransport', ies), 46, r, a)
# NG Reset (reset all): referans kodlama — proxy'nin elle kurduğu PDU ile karşılaştırılır
ies = [ie(15, 'ignore', 'Cause', ('misc', 'unspecified')), ie(88, 'reject', 'ResetType', ('nG-Interface', 'reset-all'))]
RESET = enc('initiatingMessage', 20, 'reject', 'NGReset', ies)

def reset_part(rans):
    items = [{'rAN-UE-NGAP-ID': r} for r in rans]
    P.set_val(('initiatingMessage', {'procedureCode': 20, 'criticality': 'reject', 'value': ('NGReset', {'protocolIEs': [
        {'id': 15, 'criticality': 'ignore', 'value': ('Cause', ('misc', 'unspecified'))},
        {'id': 88, 'criticality': 'reject', 'value': ('ResetType', ('partOfNG-Interface', items))}]})}))
    return P.to_aper()
RESETS = [[1], [1, 2], [255, 256], [0], [70000, 3], list(range(1, 131)), list(range(1, 301))]

def release_cmd(a, r):
    P.set_val(('initiatingMessage', {'procedureCode': 41, 'criticality': 'reject', 'value': ('UEContextReleaseCommand', {'protocolIEs': [
        {'id': 114, 'criticality': 'reject', 'value': ('UE-NGAP-IDs', ('uE-NGAP-ID-pair', {'aMF-UE-NGAP-ID': a, 'rAN-UE-NGAP-ID': r}))},
        {'id': 15, 'criticality': 'ignore', 'value': ('Cause', ('radioNetwork', 'unspecified'))}]})}))
    return P.to_aper()
RELEASES = [(1, 1), (0, 0), (255, 256), (70000, 3), (2**32, 65536), (2**40 - 1, 2**32 - 1), (300, 70000)]

def setup_resp(cap, guamis):
    items = [{'gUAMI': {'pLMNIdentity': b'\x99\xf9\x07', 'aMFRegionID': (g[0], 8), 'aMFSetID': (g[1], 10), 'aMFPointer': (g[2], 6)}} for g in guamis]
    P.set_val(('successfulOutcome', {'procedureCode': 21, 'criticality': 'reject', 'value': ('NGSetupResponse', {'protocolIEs': [
        ie(1, 'reject', 'AMFName', 'open5gs-amf0'), ie(96, 'reject', 'ServedGUAMIList', items),
        ie(86, 'ignore', 'RelativeAMFCapacity', cap),
        ie(80, 'reject', 'PLMNSupportList', [{'pLMNIdentity': b'\x99\xf9\x07', 'sliceSupportList': [{'s-NSSAI': {'sST': b'\x01'}}]}])]})}))
    return P.to_aper()
SETUPS = [(100, [(2, 1, 0)]), (50, [(2, 1, 2)]), (0, [(2, 1, 63)]), (255, [(2, 1023, 1), (2, 5, 2)]), (7, [(255, 0, 0)]),
          (200, [(1, 1, 0), (1, 1, 1), (1, 1, 2), (1, 1, 3), (1, 1, 4)])]
P.set_val(('initiatingMessage', {'procedureCode': 35, 'criticality': 'reject', 'value': ('RANConfigurationUpdate', {'protocolIEs': []})}))
PROBE = P.to_aper()

def patch_ie(pdu, old, new):
    """Replace one IE (header+value bytes) inside a short-form-length PDU and fix the outer length determinant."""
    i = pdu.index(bytes(old))
    body = bytes(pdu[:i]) + bytes(new) + bytes(pdu[i + len(old):])
    hdr, vlen_size = body[:3], 1 if body[3] < 0x80 else 2
    # the payload is everything after the old length determinant
    payload = body[3 + vlen_size:]
    n = len(payload)
    det = bytes([n]) if n < 0x80 else bytes([0x80 | (n >> 8), n & 0xFF])
    return hdr + det + payload

def uint_ie(ie_id, k, fill, len_bits):
    """IE with an INTEGER whose length header claims k value bytes (all set to `fill`)."""
    val = bytes([(k - 1) << (8 - len_bits)]) + bytes([fill]) * k
    return bytes([ie_id >> 8, ie_id & 0xFF, 0x00, len(val)]) + val

UL = V["uplink_nas_ul_small"][0]
OLD_AMF = bytes([0x00, 0x0A, 0x00, 0x02, 0x00, 0x01])              # AMF-UE-NGAP-ID = 1
BAD = {
    "amf_id_6_bytes": (patch_ie(UL, OLD_AMF, uint_ie(10, 6, 0xFF, 3)), 1, -1),   # > 2^40-1 range
    "amf_id_8_bytes": (patch_ie(UL, OLD_AMF, uint_ie(10, 8, 0x80, 3)), 1, -1),   # wrapped to a negative int64 before the fix
}
REL = release_cmd(1, 1)
OLD_PAIR = bytes([0x00, 0x72, 0x00, 0x04, 0x00, 0x01, 0x00, 0x01])   # UE-NGAP-ID-pair (1,1)
big_pair = bytes([0x0E]) + b"\x80" * 8 + bytes([0x00, 0x01])         # AMF id claims 8 bytes
BAD["pair_amf_8_bytes"] = (patch_ie(REL, OLD_PAIR, bytes([0x00, 0x72, 0x00, len(big_pair)]) + big_pair), -1, -1)

def c(b): return "{" + ",".join("0x%02x" % x for x in b) + "}"
with open("vectors.h", "w") as f:
    f.write("// otomatik üretildi: gen_vectors.py (pycrate)\n#pragma once\n#include <cstdint>\n#include <vector>\n#include <string>\n")
    f.write("struct Vec { const char* name; std::vector<uint8_t> pdu; int proc; int64_t ran; int64_t amf; int stmsi_set; int stmsi_ptr; };\n")
    f.write("static const std::vector<Vec> VECS = {\n")
    for k, (b, proc, ran, extra) in V.items():
        amf, s, p = -1, -1, -1
        if k.startswith("uplink"): amf = extra
        if k == "initial_ue_stmsi": s, p = extra
        f.write('  {"%s", %s, %d, %d, %d, %d, %d},\n' % (k, c(b), proc, ran, amf, s, p))
    f.write("};\nstatic const std::vector<uint8_t> NG_RESET_REF = %s;\n" % c(RESET))
with open("vectors.h", "a") as f:
    f.write("struct ResetVec { std::vector<uint32_t> ids; std::vector<uint8_t> pdu; };\nstatic const std::vector<ResetVec> RESET_VECS = {\n")
    for r in RESETS:
        f.write("  {{%s}, %s},\n" % (",".join(map(str, r)), c(reset_part(r))))
    f.write("};\n")
with open("vectors.h", "a") as f:
    f.write("struct RelVec { uint64_t amf; uint32_t ran; std::vector<uint8_t> pdu; };\nstatic const std::vector<RelVec> REL_VECS = {\n")
    for a, r in RELEASES:
        f.write("  {%dULL, %du, %s},\n" % (a, r, c(release_cmd(a, r))))
    f.write("};\n")
with open("vectors.h", "a") as f:
    f.write("struct SetupVec { int cap; std::vector<std::vector<int>> guamis; std::vector<uint8_t> pdu; };\nstatic const std::vector<SetupVec> SETUP_VECS = {\n")
    for cap, gs in SETUPS:
        f.write("  {%d, {%s}, %s},\n" % (cap, ",".join("{%d,%d,%d}" % g for g in gs), c(setup_resp(cap, gs))))
    f.write("};\nstatic const std::vector<uint8_t> PROBE_REF = %s;\n" % c(PROBE))
with open("vectors.h", "a") as f:
    f.write("struct BadVec { const char* name; std::vector<uint8_t> pdu; int64_t ran; int64_t amf; };\nstatic const std::vector<BadVec> BAD_VECS = {\n")
    for k, (b, ran, amf) in BAD.items():
        f.write('  {"%s", %s, %d, %d},\n' % (k, c(b), ran, amf))
    f.write("};\n")
print({k: v[0].hex() for k, v in V.items()}); print("reset", RESET.hex())
