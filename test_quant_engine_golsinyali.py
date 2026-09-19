import importlib.util

MODULE_PATH = "/mnt/data/quant_engine_with_golsinyali.py"
spec = importlib.util.spec_from_file_location("quant_engine", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SPORTS_EVENT_HTML = '''
<html><head><script type="application/ld+json">
{
  "@type":"SportsEvent",
  "description":"Arsenal vs Chelsea prediction. According to Golsinyali AI model, home win 64.5% probability predicted (confidence: 88%).",
  "startDate":"2026-09-19T18:00:00.000Z",
  "homeTeam":{"name":"Arsenal"},
  "awayTeam":{"name":"Chelsea"},
  "organizer":{"name":"England Premier League"},
  "eventStatus":"https://schema.org/EventScheduled"
}
</script></head></html>
'''

BALANCED_HTML = '''
<html><head><script type="application/ld+json">
{
  "@type":"SportsEvent",
  "description":"Liverpool vs Manchester United prediction. According to Golsinyali AI model, Balanced match (45%-28%-27%).",
  "startDate":"2026-09-19T20:00:00.000Z",
  "homeTeam":{"name":"Liverpool"},
  "awayTeam":{"name":"Manchester United"},
  "organizer":{"name":"England Premier League"},
  "eventStatus":"https://schema.org/EventScheduled"
}
</script></head></html>
'''

LINKS_HTML = '''
<html><body>
<a href="/en/match/1001/arsenal-chelsea">Arsenal Chelsea</a>
<a href="/en/match/1002/liverpool-manchester-united">Liverpool Manchester United</a>
<a href="/en/other-page">Not a match</a>
<a href="/en/match/1001/arsenal-chelsea">Duplicate</a>
</body></html>
'''

configs = mod.get_dynamic_configs()
assert "Golsinyali" in configs
assert len(configs) == 6

engine = mod.ConsensusEngine(configs)

home_event = engine._golsinyali_extract_sports_event_jsonld(SPORTS_EVENT_HTML)
balanced_event = engine._golsinyali_extract_sports_event_jsonld(BALANCED_HTML)

assert home_event["homeTeam"]["name"] == "Arsenal"
assert home_event["awayTeam"]["name"] == "Chelsea"
assert engine._golsinyali_extract_prediction(home_event, SPORTS_EVENT_HTML) == "HOME"
assert engine._golsinyali_extract_prediction(balanced_event, BALANCED_HTML) == "HOME"

mapping = {
    engine.GOLSINYALI_PREDICTIONS_URL: LINKS_HTML,
    engine.GOLSINYALI_BASE_URL + "/en/match/1001/arsenal-chelsea": SPORTS_EVENT_HTML,
    engine.GOLSINYALI_BASE_URL + "/en/match/1002/liverpool-manchester-united": BALANCED_HTML,
}

engine.GOLSINYALI_REQUEST_DELAY_SECONDS = 0
engine.GOLSINYALI_MAX_MATCH_PAGES = 10
engine._fetch_golsinyali_html = lambda url: mapping[url]
engine.fetch_golsinyali_sync()

assert engine.diagnostics["Golsinyali"].startswith("🟢 OK")
assert ("Golsinyali", "1") in engine.master_matrix["Arsenal vs Chelsea"]
assert ("Golsinyali", "1") in engine.master_matrix["Liverpool vs Manchester United"]

print("PASS: Golsinyali registered in source configuration")
print("PASS: SportsEvent JSON-LD extraction")
print("PASS: HOME prediction extraction")
print("PASS: Balanced probability extraction")
print("PASS: End-to-end master_matrix ingestion")
print("PASS: Golsinyali included in 6-source consensus roster")
print("ALL GOLSINYALI INTEGRATION TESTS PASSED")
