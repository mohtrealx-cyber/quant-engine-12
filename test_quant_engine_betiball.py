from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from quant_engine_golsinyali_expected90_socceraitips_betiball import (
    ConsensusEngine,
)


EAT = ZoneInfo("Africa/Nairobi")
TODAY = datetime.now(timezone.utc).astimezone(EAT).date().isoformat()

DISCOVERY_HTML = """
<html><body>
<a href="/football-predictions/leeds-united-vs-newcastle-united-248830036">
Leeds United vs Newcastle United Prediction
</a>
</body></html>
"""

MATCH_HTML = f"""
<html><body>
<h1>Leeds United vs Newcastle United Prediction</h1>
<div>Scheduled date: {TODAY}</div>
<div>Our algorithm prediction: Leeds United to win with probability 41%.</div>
<div>Leeds United - Newcastle United 41 25 34 12</div>
</body></html>
"""


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.content = text.encode()
        self.status_code = status_code


def test_betiball_slug_and_probability_parsing():
    assert ConsensusEngine._betiball_slugify_team("Leeds United") == "leeds-united"

    probabilities = ConsensusEngine._betiball_extract_probabilities(
        text=ConsensusEngine._betiball_page_text(MATCH_HTML),
        home_team="Leeds United",
        away_team="Newcastle United",
    )

    assert probabilities == {
        "HOME": 41.0,
        "DRAW": 25.0,
        "AWAY": 34.0,
    }

    assert (
        ConsensusEngine._betiball_probability_to_selection(probabilities)
        == "HOME"
    )


def test_betiball_fixture_and_link_discovery():
    names = ConsensusEngine._betiball_extract_fixture_names(
        ConsensusEngine._betiball_page_text(MATCH_HTML)
    )
    assert names == ("Leeds United", "Newcastle United")

    candidate_map = ConsensusEngine._betiball_find_candidates(
        DISCOVERY_HTML,
        ["Leeds United vs Newcastle United"],
    )

    assert candidate_map["Leeds United vs Newcastle United"]
    assert (
        "leeds-united-vs-newcastle-united"
        in candidate_map["Leeds United vs Newcastle United"][0].lower()
    )


def test_betiball_end_to_end_without_live_requests():
    engine = ConsensusEngine({"Betiball": {"url": ConsensusEngine.BETIBALL_DISCOVERY_URL}})
    engine.master_matrix = {
        "Leeds United vs Newcastle United": [],
    }

    def fake_get(url, *args, **kwargs):
        if url.endswith("/football-predictions/"):
            return FakeResponse(DISCOVERY_HTML)
        if "leeds-united-vs-newcastle-united-248830036" in url:
            return FakeResponse(MATCH_HTML)
        if "/1x2" in url:
            return FakeResponse(DISCOVERY_HTML)
        raise AssertionError(f"Unexpected URL: {url}")

    with patch(
        "quant_engine_golsinyali_expected90_socceraitips_betiball.requests.get",
        side_effect=fake_get,
    ):
        engine.fetch_betiball_sync()

    assert "Leeds United vs Newcastle United" in engine.master_matrix
    assert (
        ("Betiball", "1")
        in engine.master_matrix["Leeds United vs Newcastle United"]
    )
    assert engine.diagnostics["Betiball"].startswith("🟢 OK")
    assert "Failed: 0" in engine.diagnostics["Betiball_Detail"]


if __name__ == "__main__":
    test_betiball_slug_and_probability_parsing()
    test_betiball_fixture_and_link_discovery()
    test_betiball_end_to_end_without_live_requests()
    print("ALL BETIBALL INTEGRATION TESTS PASSED")
