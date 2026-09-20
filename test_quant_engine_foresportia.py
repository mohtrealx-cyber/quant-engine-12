from datetime import date
from quant_engine import ConsensusEngine


DISCOVERY_HTML = '''
<html><body>
<a href="/en/prediction/premier-league/afc-sunderland-arsenal-fc-2026-09-20/">
  Sunderland vs Arsenal
</a>
<a href="/en/prediction/premier-league/other-match-2026-09-20/">
  Other Match
</a>
</body></html>
'''

MATCH_HTML = '''
<html><body>
<h1>Prediction for AFC Sunderland vs Arsenal FC:</h1>
<div>Scheduled September 20, 2026 • 19:00</div>
<section>
  <h2>1X2 PREDICTION BALANCE</h2>
  <div>16.9% AFC Sunderland</div>
  <div>30.3% Draw</div>
  <div>52.8% Arsenal FC</div>
</section>
</body></html>
'''


def test_foresportia_link_discovery():
    all_links, candidates = ConsensusEngine._foresportia_find_candidate_links(
        DISCOVERY_HTML,
        ["Sunderland vs Arsenal"],
    )

    assert len(all_links) == 2
    assert len(candidates) == 1
    assert candidates[0].endswith(
        "/en/prediction/premier-league/afc-sunderland-arsenal-fc-2026-09-20/"
    )

    print("PASS: Foresportia candidate discovery")


def test_foresportia_parsing():
    text = ConsensusEngine._foresportia_page_text(MATCH_HTML)

    fixture_names = ConsensusEngine._foresportia_extract_fixture_names(text)
    assert fixture_names == ("AFC Sunderland", "Arsenal FC")

    kickoff = ConsensusEngine._foresportia_extract_scheduled_kickoff(text)
    assert kickoff is not None
    assert kickoff.isoformat() == "2026-09-20T18:00:00+00:00"

    probabilities = ConsensusEngine._foresportia_extract_probabilities(
        text,
        "AFC Sunderland",
        "Arsenal FC",
    )
    assert probabilities == {
        "HOME": 16.9,
        "DRAW": 30.3,
        "AWAY": 52.8,
    }

    selection = ConsensusEngine._foresportia_probability_to_selection(
        probabilities
    )
    assert selection == "AWAY"

    print("PASS: Foresportia fixture/probability parsing")


def test_foresportia_end_to_end_without_live_requests():
    engine = ConsensusEngine({})
    engine.master_matrix = {
        "Sunderland vs Arsenal": []
    }

    def fake_fetch(url, timeout=60):
        if url.endswith("/en/results_by_date.html"):
            return DISCOVERY_HTML
        return MATCH_HTML

    engine._fetch_foresportia_html = fake_fetch

    engine.fetch_foresportia_sync(today_eat=date(2026, 9, 20))

    assert engine.diagnostics["Foresportia"].startswith("🟢 OK")
    assert engine.diagnostics["Foresportia_Detail"].startswith("📊 Discovered: 2")

    assert engine.master_matrix["Sunderland vs Arsenal"] == [
        ("Foresportia", "2")
    ]

    print("PASS: Foresportia end-to-end integration")


if __name__ == "__main__":
    print("QUANT ENGINE FORESPORTIA TEST")
    print("=" * 70)
    test_foresportia_link_discovery()
    test_foresportia_parsing()
    test_foresportia_end_to_end_without_live_requests()
    print("=" * 70)
    print("ALL FORESPORTIA TESTS PASSED")
    print("=" * 70)
