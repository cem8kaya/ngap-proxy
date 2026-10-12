"""lab/load_templates.py üretir: yük üretecinin (VM'de yalnızca stdlib) kullandığı NGAP şablonları. `pip install pycrate`"""
from pycrate_asn1dir import NGAP
P = NGAP.NGAP_PDU_Descriptions.NGAP_PDU
def ie(i, c, t, v): return {'id': i, 'criticality': c, 'value': (t, v)}
NAS = b'\x7e\x00\x41\x79\x00\x0d\x01\x99\xf9\x07\x00\x00\x00\x00\x00\x00\x00\x00\x01\x2e\x04\xf0\xf0\xf0\xf0'
ULI = ('userLocationInformationNR', {'nR-CGI': {'pLMNIdentity': b'\x99\xf9\x07', 'nRCellIdentity': (0x7f0010, 36)},
                                    'tAI': {'pLMNIdentity': b'\x99\xf9\x07', 'tAC': b'\x00\x00\x01'}})
def setup_req(gid):
    P.set_val(('initiatingMessage', {'procedureCode': 21, 'criticality': 'reject', 'value': ('NGSetupRequest', {'protocolIEs': [
        ie(27, 'reject', 'GlobalRANNodeID', ('globalGNB-ID', {'pLMNIdentity': b'\x99\xf9\x07', 'gNB-ID': ('gNB-ID', (gid, 22))})),
        ie(82, 'ignore', 'RANNodeName', 'loadgen-%d' % gid),
        ie(102, 'reject', 'SupportedTAList', [{'tAC': b'\x00\x00\x01', 'broadcastPLMNList': [
            {'pLMNIdentity': b'\x99\xf9\x07', 'tAISliceSupportList': [{'s-NSSAI': {'sST': b'\x01'}}]}]}]),
        ie(21, 'ignore', 'PagingDRX', 'v128')]})}))
    return P.to_aper()
def initial_ue(rid):
    P.set_val(('initiatingMessage', {'procedureCode': 15, 'criticality': 'ignore', 'value': ('InitialUEMessage', {'protocolIEs': [
        ie(85, 'reject', 'RAN-UE-NGAP-ID', rid), ie(38, 'reject', 'NAS-PDU', NAS), ie(121, 'reject', 'UserLocationInformation', ULI),
        ie(90, 'ignore', 'RRCEstablishmentCause', 'mo-Signalling'), ie(112, 'ignore', 'UEContextRequest', 'requested')]})}))
    return P.to_aper()
BASE = 0x01000000                         # 4 baytlık RAN id (başlık 0xC0) -> sabit uzunluk, bayt yamalanabilir
t = initial_ue(BASE)
off = t.index(bytes([0x00, 0x55, 0x00, 0x05, 0xC0])) + 5
assert t[off:off + 4] == BASE.to_bytes(4, 'big')
with open("../../lab/load_templates.py", "w") as f:
    f.write('"""Otomatik üretildi: proxy/tests/gen_load_templates.py (pycrate). Elle düzenlemeyin."""\n')
    f.write("INITIAL_UE = bytes.fromhex(%r)\nRAN_ID_OFFSET = %d\nRAN_ID_BASE = %d\n" % (t.hex(), off, BASE))
    f.write("NG_SETUP = {%s}\n" % ", ".join("%d: bytes.fromhex(%r)" % (g, setup_req(g).hex()) for g in range(0x7f0001, 0x7f0009)))
print("ok", len(t), off)
