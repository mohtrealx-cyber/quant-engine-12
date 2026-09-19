import json

from quant_engine import ConsensusEngine


EXPECTED90_HTML = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SportsEvent",
  "name": "Brentford vs Chelsea prediction",
  "description": "Brentford win 42%, Draw 30%, Chelsea win 29%",
  "startDate": "2026-09-19T19:00:00+00:00",
  "homeTeam": {
    "@type": "SportsTeam",
    "name": "Brentford"
  },
  "awayTeam": {
    "@type": "SportsTeam",
    "name": "Chelsea"
  }
}
</script>
</head>
<body></body>
</html>
"""


LINKS_HTML = """
<html>
<body>
<a href="/football-predictions/premier-league/brentford-vs-chelsea">
  Brentford vs Chelsea
</a>
<a href="/football-predictions/premier-league/tottenham-hotspur-vs-aston-villa">
  Tottenham Hotspur vs Aston Villa
</a>
</body>
</html>
"""


def main():
    engine = ConsensusEngine({"Expected90": {"url": engine_url}})

    event_objects = engine._expected90_parse_json_ld(EXPECTED90_HTML)
    assert event_objects
    event = engine._expected90_find_sports_event(event_objects)
    assert event is not None

    probabilities = engine._expected90_extract_probabilities(
        event["description"]
    )
    assert probabilities == {
        "HOME": 42.0,
        "DRAW": 30.0,
        "AWAY": 29.0,
    }

    selection = engine._expected90_probability_to_selection(probabilities)
    assert selection == "HOME"

    links = engine._expected90_extract_match_links(LINKS_HTML)
    assert len(links) == 2
    assert links[0].endswith(
        "/football-predictions/premier-league/brentford-vs-chelsea"
    )

    print("PASS: Expected90 JSON-LD extraction")
    print("PASS: Expected90 1X2 probability parsing")
    print("PASS: Expected90 highest-probability selection")
    print("PASS: Expected90 match-link discovery")
    print("ALL EXPECTED90 INTEGRATION UNIT TESTS PASSED")


if __name__ == "__main__":
    # Module-level placeholder used only for constructing the engine.
    engine_url = "https://expected90.com/football-predictions"
    main()
