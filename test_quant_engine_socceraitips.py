from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import quant_engine


def _future_today_utc():
    now = datetime.now(timezone.utc)
    # Keep the fixture comfortably ahead of the current time.
    candidate = now + timedelta(minutes=30)
    return candidate.replace(microsecond=0)


def test_socceraitips_secondary_market_ingestion():
    kickoff = _future_today_utc().isoformat().replace("+00:00", "Z")

    payload = {
        "success": True,
        "data": {
            "matches": [
                {
                    "match_id": 1001,
                    "home_team": "Sevilla",
                    "away_team": "Valencia",
                    "match_time": "20:00",
                    "match_time_utc": kickoff,
                    "bet_type": "btts",
                    "prediction": "VAR",
                    "prediction_display": "BTTS",
                    "confidence": 81,
                    "league": "LaLiga",
                },
                {
                    "match_id": 1002,
                    "home_team": "Arsenal",
                    "away_team": "Brighton",
                    "match_time": "21:00",
                    "match_time_utc": kickoff,
                    "bet_type": "over_2_5",
                    "prediction": "ÜST",
                    "prediction_display": "OVER 2.5",
                    "confidence": 79,
                    "league": "Premier League",
                },
            ]
        },
    }

    response = SimpleNamespace(
        status_code=200,
        text="{\"success\":true}",
        json=lambda: payload,
        raise_for_status=lambda: None,
    )

    engine = quant_engine.ConsensusEngine(quant_engine.get_dynamic_configs())

    # Establish one canonical fixture so one SoccerAiTips result is proven to
    # match the existing engine matrix, while the other remains a new fixture.
    engine.log_prediction_qa("Statarea", "Sevilla", "Valencia", "1")

    with patch("quant_engine.requests.get", return_value=response):
        engine.fetch_socceraitips_sync()

    assert engine.secondary_market_data
    assert len(engine.secondary_market_data) == 2

    btts = next(
        item for item in engine.secondary_market_data
        if item["market"] == "BTTS"
    )
    over = next(
        item for item in engine.secondary_market_data
        if item["market"] == "OVER_2.5"
    )

    assert btts["selection"] == "BTTS_YES"
    assert over["selection"] == "OVER_2.5"
    assert btts["matched_existing_fixture"] is True
    assert over["matched_existing_fixture"] is False

    assert engine.diagnostics["SoccerAiTips"].startswith("🟢 OK")
    assert "Secondary Markets" in engine.diagnostics["SoccerAiTips"]

    print("PASS: SoccerAiTips API parsing")
    print("PASS: BTTS normalization")
    print("PASS: Over 2.5 normalization")
    print("PASS: Existing fixture matching")
    print("PASS: New fixture detection")
    print("PASS: SoccerAiTips diagnostics")


if __name__ == "__main__":
    test_socceraitips_secondary_market_ingestion()
    print("ALL SOCCERAITIPS INTEGRATION TESTS PASSED")
