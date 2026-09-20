from datetime import date
from unittest.mock import patch

import importlib.util
import sys
import types
import requests as real_requests

class _CurlStubSession:
    def __init__(self, *args, **kwargs):
        self._session = real_requests.Session()

    def get(self, *args, **kwargs):
        kwargs.pop("impersonate", None)
        return self._session.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        kwargs.pop("impersonate", None)
        return self._session.post(*args, **kwargs)

class _CurlStubModule(types.SimpleNamespace):
    def __init__(self):
        super().__init__(get=real_requests.get, post=real_requests.post, Session=_CurlStubSession)

stub = types.ModuleType("curl_cffi")
stub.requests = _CurlStubModule()
sys.modules["curl_cffi"] = stub

spec = importlib.util.spec_from_file_location("quant_engine_nv", "/mnt/data/quant_engine_golsinyali_expected90_socceraitips_nvtips.py")
quant_engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quant_engine)


NVTIPS_HTML = """
<html>
<head><title>NVtips</title></head>
<body>
  <div class="nv-row" data-country="Belgium" data-league="jupiler league" data-time="17:30"
       data-search="waregem charleroi 1 2-1 over 2.5 yes">
    <span class="nv-team-name">Waregem</span>
    <span class="nv-team-name">Charleroi</span>
    <span>34% 36% 30%</span>
    <span>1 2-1</span>
  </div>
  <div class="nv-row" data-country="Belgium" data-league="jupiler league" data-time="20:00"
       data-search="anderlecht standard 2 1-2 over 2.5 yes">
    <span class="nv-team-name">Anderlecht</span>
    <span class="nv-team-name">Standard Liege</span>
    <span>25% 30% 45%</span>
    <span>2 1-2</span>
  </div>
  <div class="nv-row" data-country="Belgium" data-league="jupiler league" data-time="21:00"
       data-search="broken row">
    <span class="nv-team-name">Broken</span>
  </div>
</body>
</html>
"""


def test_nvtips_row_extraction():
    rows = quant_engine.ConsensusEngine._nvtips_extract_rows(NVTIPS_HTML)

    assert len(rows) == 2
    assert rows[0]["home_team"] == "Waregem"
    assert rows[0]["away_team"] == "Charleroi"
    assert rows[0]["competition"] == "jupiler league"
    assert rows[0]["source_time"] == "17:30"
    assert rows[0]["probabilities"] == (34.0, 36.0, 30.0)
    assert rows[0]["prediction"] == "1"

    assert rows[1]["prediction"] == "2"

    print("PASS: NVtips row extraction")


def test_nvtips_sync_integration():
    engine = quant_engine.ConsensusEngine(quant_engine.get_dynamic_configs())

    # Existing canonical fixture already established by the base sources.
    engine.master_matrix = {
        "Waregem vs Charleroi": [
            ("Statarea", "1"),
            ("Vitibet", "1"),
        ]
    }

    engine._fetch_nvtips_html = lambda target_date: NVTIPS_HTML

    engine.fetch_nvtips_sync()

    assert "NVtips_Detail" in engine.diagnostics
    assert "NVtips" in engine.diagnostics
    assert "Waregem vs Charleroi" in engine.master_matrix

    listings = engine.master_matrix["Waregem vs Charleroi"]
    assert ("NVtips", "1") in listings

    # The second fixture should be added as a new match because it was not
    # already present in the canonical matrix.
    assert "Anderlecht vs Standard Liege" in engine.master_matrix
    assert (
        "NVtips", "2"
    ) in engine.master_matrix["Anderlecht vs Standard Liege"]

    assert "Betiball" not in quant_engine.get_dynamic_configs()

    print("PASS: NVtips consensus integration")


def test_nvtips_consensus_roster():
    engine = quant_engine.ConsensusEngine(quant_engine.get_dynamic_configs())
    engine.master_matrix = {
        "Waregem vs Charleroi": [
            ("Statarea", "1"),
            ("Vitibet", "1"),
            ("NVtips", "1"),
        ]
    }

    agreed, tickets, ai_input, threshold = engine.process_consensus_signals()

    assert threshold == 3
    assert agreed
    assert tickets
    assert ai_input
    assert "NVtips" in agreed[0]
    assert "Betiball" not in agreed[0]

    print("PASS: NVtips consensus roster")


if __name__ == "__main__":
    print("=" * 70)
    print("QUANT ENGINE NVTIPS INTEGRATION TEST")
    print("=" * 70)

    test_nvtips_row_extraction()
    test_nvtips_sync_integration()
    test_nvtips_consensus_roster()

    print()
    print("=" * 70)
    print("ALL NVTIPS INTEGRATION TESTS PASSED")
    print("=" * 70)
