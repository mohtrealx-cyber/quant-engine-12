import os
import re
import json
import time
import asyncio
import datetime
import difflib
import requests
from bs4 import BeautifulSoup
import concurrent.futures
from curl_cffi import requests as tls_requests

# ==============================================================================
# CONFIGURATION & SECURE ROUTING FALLBACKS
# ==============================================================================
TELEGRAM_TOKEN = os.environ.get("QUANT_TELEGRAM_TOKEN") or os.environ.get("TRACKER_TRACKER_TELEGRAM_TOKEN") or os.environ.get("TRACKER_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("QUANT_TELEGRAM_CHAT_ID") or os.environ.get("TRACKER_TRACKER_TELEGRAM_CHAT_ID") or os.environ.get("TRACKER_TELEGRAM_CHAT_ID")
SCRAPER_API_KEY = (os.environ.get("SCRAPER_API_KEY") or "").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
FORCE_RUN = os.environ.get("FORCE_RUN", "").strip().lower() in ["true", "1", "yes"]

MEMORY_FILE = "pending_tickets.json"

def get_dynamic_configs():
    eat_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    today_date = eat_time.strftime('%Y-%m-%d')
    cb = int(time.time())

    return {
        "Golsinyali": {
            "url": "https://www.golsinyali.com/en/predictions",
            "fallback_url": None,
            "use_scraperapi": False
        },
        "Expected90": {
            "url": "https://expected90.com/football-predictions",
            "fallback_url": None,
            "use_scraperapi": False
        },
        "SoccerAiTips": {
            "url": "https://www.socceraitips.com/api/daily-parlay",
            "fallback_url": None,
            "use_scraperapi": False
        },
        "NVtips": {
            "url": f"https://nvtips.com/?d={datetime.datetime.utcnow().day}&m={datetime.datetime.utcnow().month}&y={datetime.datetime.utcnow().year}",
            "fallback_url": None,
            "use_scraperapi": False
        },
        "Statarea": {
            "url": f"https://www.statarea.com/predictions/date/{today_date}/",
            "fallback_url": None,
            "row_selector": "div", "row_class": "matchrow",
            "home_selector": "div", "home_class": "name", "home_index": 0,
            "away_selector": "div", "away_class": "name", "away_index": 1,
            "pick_selector": "div", "pick_class": "type1", "pick_index": 0,
            "use_scraperapi": True  
        },
        "Vitibet": {
            "url": f"https://www.vitibet.com/index.php?clanek=quicktips&sekce=fotbal&lang=en&cb={cb}",
            "fallback_url": None,
            "row_selector": "a", "row_class": "livescore-match-row",
            "home_selector": "span", "home_class": "livescore-team-name", "home_index": 0,
            "away_selector": "span", "away_class": "livescore-team-name", "away_index": 1,
            "pick_selector": "span", "pick_class": "tip-indicator-circle", "pick_index": 0,
            "use_scraperapi": False
        },
        "PredictZ": {
            "url": "https://www.predictz.com/predictions/today/",
            "fallback_url": "https://www.predictz.com/predictions/",
            "row_selector": "div", "row_class": "pttr",
            "home_selector": "div", "home_class": "pttmobh", "home_index": 0,
            "away_selector": "div", "away_class": "pttmoba", "away_index": 0,
            "pick_selector": "div", "pick_class": "ptoddsdesc", "pick_index": 0,
            "use_scraperapi": True
        },
        "WinDrawWin": {
            "url": "https://www.windrawwin.com/predictions/today/",
            "fallback_url": "https://www.windrawwin.com/predictions/",
            "row_selector": "div", "row_class": "wtrow",
            "home_selector": "div", "home_class": "wttmobh", "home_index": 0,
            "away_selector": "div", "away_class": "wttmoba", "away_index": 0,
            "pick_selector": "div", "pick_class": "wtoddsdesc", "pick_index": 0,
            "use_scraperapi": True
        },
        "SoccerVista": {
            "url": "https://www.soccervista.com/",
            "fallback_url": "https://www.soccervista.com/predictions/",
            "row_selector": "tr", "row_class": "",
            "home_selector": "td", "home_class": "", "home_index": 0,
            "away_selector": "td", "away_class": "", "away_index": 1,
            "pick_selector": "td", "pick_class": "", "pick_index": 4,
            "use_scraperapi": True
        }
    }

class ConsensusEngine:
    def __init__(self, configs):
        self.configs = configs
        self.master_matrix = {}
        self.corner_stats = {}
        self.diagnostics = {}
        self.secondary_market_data = []

        # Dedicated browser-like session for Golsinyali.
        # This reduces false blocks from basic Python HTTP fingerprints.
        self.golsinyali_session = tls_requests.Session(impersonate="chrome124")

    def normalize_prediction(self, raw_text):
        text = str(raw_text).strip().lower()
        if text in ["home", "home win"]: return "1"
        if text in ["draw", "x", "0"]: return "X"
        if text in ["away", "away win"]: return "2"

        if len(text) > 0:
            char = text[0]
            if char == "1" or char == "h": return "1"
            if char in ["x", "0", "d"]: return "X"
            if char == "2" or char == "a": return "2"
        return None

    def clean_team_name(self, name):
        cleaned = re.sub(r'(?i)\b(match preview|preview|results?)\b', '', str(name))
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned.title()

    def is_match_active_or_played(self, row):
        text = row.get_text(separator=" ").upper()
        padded_text = f" {text} "
        status_flags = [" FT ", " HT ", "CANC", "POSTP", "FINISHED", " LIVE ", "AET ", "PEN ", "DELAYED"]
        for flag in status_flags:
            if flag in padded_text:
                return True
        return False

    def log_prediction_qa(self, site_name, home, away, raw_prediction):
        if not home or not away or not raw_prediction:
            return None

        normalized_pick = self.normalize_prediction(raw_prediction)
        if not normalized_pick:
            return None

        raw_match_key = f"{self.clean_team_name(home)} vs {self.clean_team_name(away)}"
        final_key = raw_match_key
        matched_existing_fixture = False

        for existing_key in self.master_matrix.keys():
            similarity = difflib.SequenceMatcher(
                None,
                raw_match_key.lower(),
                existing_key.lower(),
            ).ratio()
            if similarity >= 0.75:
                final_key = existing_key
                matched_existing_fixture = True
                break

        if final_key not in self.master_matrix:
            self.master_matrix[final_key] = []

        existing_sites = [entry[0] for entry in self.master_matrix[final_key]]
        if site_name not in existing_sites:
            self.master_matrix[final_key].append((site_name, normalized_pick))

        return {
            "match_key": final_key,
            "matched_existing_fixture": matched_existing_fixture,
            "created_new_match": not matched_existing_fixture,
            "normalized_pick": normalized_pick,
        }

    def fetch_corners_sync(self):
        url = "https://www.totalcorner.com/match/today"
        for attempt in range(1, 4):
            try:
                if SCRAPER_API_KEY and attempt == 1:
                    proxy_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={url}"
                    r = requests.get(proxy_url, timeout=40)
                else:
                    r = tls_requests.get(url, impersonate="chrome124", timeout=20)
                
                if r.status_code == 200:
                    soup = BeautifulSoup(r.content, 'html.parser')
                    rows = soup.find_all("tr")
                    
                    valid_corners = 0
                    for row in rows:
                        cols = [c.text.strip() for c in row.find_all(["td", "th"]) if c.text.strip()]
                        if len(cols) >= 5:
                            team_links = row.find_all("a", href=re.compile(r"/team/"))
                            if len(team_links) >= 2:
                                home_team = self.clean_team_name(team_links[0].text)
                                away_team = self.clean_team_name(team_links[1].text)
                                
                                row_text = row.get_text(separator=" ")
                                averages = re.findall(r'\b([7-9]\.\d|1[0-5]\.\d)\b', row_text)
                                
                                if averages:
                                    highest_avg = max([float(x) for x in averages])
                                    if highest_avg >= 8.5:
                                        self.corner_stats[home_team] = highest_avg
                                        self.corner_stats[away_team] = highest_avg
                                        valid_corners += 1
                                        
                    self.diagnostics["Corners_Engine"] = f"🟢 OK ({valid_corners} High-Corner Teams)"
                    return
                elif r.status_code in [403, 500, 502, 503, 504, 429]:
                    time.sleep(2 * attempt)
                    continue
                else:
                    self.diagnostics["Corners_Engine"] = f"🔴 FAILED (HTTP {r.status_code})"
                    return
            except Exception:
                time.sleep(2 * attempt)
                continue

        self.diagnostics["Corners_Engine"] = "🔴 TIMEOUT/ERROR"

    # ==========================================================================
    # GOLSINYALI DEDICATED ADAPTER
    # ==========================================================================

    GOLSINYALI_BASE_URL = "https://www.golsinyali.com"
    GOLSINYALI_PREDICTIONS_URL = f"{GOLSINYALI_BASE_URL}/en/predictions"
    GOLSINYALI_REQUEST_TIMEOUT = 30
    GOLSINYALI_MAX_MATCH_PAGES = 20
    GOLSINYALI_REQUEST_DELAY_SECONDS = 2.0
    GOLSINYALI_MAX_RETRIES = 2
    GOLSINYALI_MAX_RETRY_WAIT_SECONDS = 15.0
    GOLSINYALI_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    )

    @staticmethod
    def _golsinyali_parse_iso_datetime(value):
        if not value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00")
            )
        except ValueError:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)

        return parsed.astimezone(datetime.timezone.utc)

    @staticmethod
    def _golsinyali_extract_sports_event_jsonld(html):
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string
            if not raw:
                continue

            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue

            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                if not isinstance(item, dict):
                    continue

                if item.get("@type") == "SportsEvent":
                    return item

                graph = item.get("@graph")
                if isinstance(graph, list):
                    for graph_item in graph:
                        if (
                            isinstance(graph_item, dict)
                            and graph_item.get("@type") == "SportsEvent"
                        ):
                            return graph_item

        return None

    @staticmethod
    def _golsinyali_extract_team_name(value):
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()

        if isinstance(value, str) and value.strip():
            return value.strip()

        return None

    @classmethod
    def _golsinyali_extract_fixture_metadata(cls, sports_event):
        home = cls._golsinyali_extract_team_name(sports_event.get("homeTeam"))
        away = cls._golsinyali_extract_team_name(sports_event.get("awayTeam"))
        kickoff = cls._golsinyali_parse_iso_datetime(
            sports_event.get("startDate")
        )

        organizer = sports_event.get("organizer")
        competition = None
        if isinstance(organizer, dict):
            competition = organizer.get("name")
        elif isinstance(organizer, str):
            competition = organizer

        if not home or not away or not kickoff:
            return None

        return {
            "home_team": home,
            "away_team": away,
            "kickoff": kickoff,
            "competition": competition,
            "description": sports_event.get("description"),
            "event_status": sports_event.get("eventStatus"),
        }

    @staticmethod
    def _golsinyali_extract_prediction_from_description(description):
        if not description:
            return None

        lowered = str(description).lower()

        # Common descriptive forms used by Golsinyali.
        if "home win" in lowered:
            return "HOME"
        if "away win" in lowered:
            return "AWAY"
        if "draw" in lowered:
            return "DRAW"

        # Current/alternate 1X2 labels used on prediction cards.
        if re.search(r"\bms1\b|\bpick\s*:\s*1\b", lowered):
            return "HOME"
        if re.search(r"\bms2\b|\bpick\s*:\s*2\b", lowered):
            return "AWAY"
        if re.search(r"\bmsx\b|\bpick\s*:\s*x\b", lowered):
            return "DRAW"

        marker = lowered.find("balanced match")
        if marker >= 0:
            balanced_text = str(description)[marker:]
            probabilities = re.search(
                r"\((\d+(?:\.\d+)?)%\s*-\s*"
                r"(\d+(?:\.\d+)?)%\s*-\s*"
                r"(\d+(?:\.\d+)?)%\)",
                balanced_text,
            )

            if probabilities:
                values = {
                    "HOME": float(probabilities.group(1)),
                    "DRAW": float(probabilities.group(2)),
                    "AWAY": float(probabilities.group(3)),
                }
                return max(values, key=values.get)

        return None

    @classmethod
    def _golsinyali_extract_prediction(cls, sports_event, html):
        prediction = cls._golsinyali_extract_prediction_from_description(
            sports_event.get("description")
        )
        if prediction is not None:
            return prediction

        lowered = html.lower()
        patterns = [
            (
                r"home\s+(?:win\s+)?(\d+(?:\.\d+)?)%\s*"
                r"(?:probability|chance)",
                "HOME",
            ),
            (
                r"away\s+(?:win\s+)?(\d+(?:\.\d+)?)%\s*"
                r"(?:probability|chance)",
                "AWAY",
            ),
        ]

        for pattern, selection in patterns:
            if re.search(pattern, lowered):
                return selection

        return None

    def _fetch_golsinyali_html(self, url, referer=None):
        headers = {
            "User-Agent": self.GOLSINYALI_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Connection": "keep-alive",
        }
        if referer:
            headers["Referer"] = referer

        last_error = None
        last_status = None
        last_body_hint = None

        for attempt in range(1, self.GOLSINYALI_MAX_RETRIES + 1):
            try:
                # First try curl_cffi with a real Chrome TLS fingerprint.
                response = self.golsinyali_session.get(
                    url,
                    headers=headers,
                    timeout=self.GOLSINYALI_REQUEST_TIMEOUT,
                )
                last_status = response.status_code
                last_body_hint = response.text[:160].replace("\n", " ")

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait_seconds = (
                            float(retry_after)
                            if retry_after
                            else self.GOLSINYALI_REQUEST_DELAY_SECONDS * attempt * 2
                        )
                    except (TypeError, ValueError):
                        wait_seconds = self.GOLSINYALI_REQUEST_DELAY_SECONDS * attempt

                    wait_seconds = min(
                        max(0.0, wait_seconds),
                        self.GOLSINYALI_MAX_RETRY_WAIT_SECONDS,
                    )
                    last_error = f"HTTP 429 (rate limited)"
                    self.diagnostics["Golsinyali_RateLimit"] = (
                        f"🟡 HTTP 429; retry {attempt}/{self.GOLSINYALI_MAX_RETRIES}; "
                        f"waiting {wait_seconds:.1f}s"
                    )

                    # If ScraperAPI is configured, use it immediately as the fallback.
                    if SCRAPER_API_KEY:
                        proxy_url = (
                            "http://api.scraperapi.com"
                            f"?api_key={SCRAPER_API_KEY}"
                            f"&url={url}"
                            "&render=true"
                        )
                        proxy_response = requests.get(
                            proxy_url,
                            headers=headers,
                            timeout=60,
                        )
                        if proxy_response.status_code == 200 and proxy_response.text.strip():
                            self.diagnostics["Golsinyali_RateLimit"] = (
                                "🟢 ScraperAPI fallback succeeded"
                            )
                            return proxy_response.text
                        last_error = (
                            f"HTTP 429; ScraperAPI fallback returned "
                            f"HTTP {proxy_response.status_code}"
                        )

                    if attempt < self.GOLSINYALI_MAX_RETRIES and wait_seconds > 0:
                        time.sleep(wait_seconds)
                    continue

                if response.status_code in (403, 408, 425, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}"
                    if attempt < self.GOLSINYALI_MAX_RETRIES:
                        time.sleep(
                            min(
                                self.GOLSINYALI_REQUEST_DELAY_SECONDS * attempt,
                                self.GOLSINYALI_MAX_RETRY_WAIT_SECONDS,
                            )
                        )
                        continue
                    break

                response.raise_for_status()
                html = response.text
                if not html.strip():
                    last_error = "Empty response"
                    continue

                return html

            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.GOLSINYALI_MAX_RETRIES:
                    time.sleep(
                        min(
                            self.GOLSINYALI_REQUEST_DELAY_SECONDS * attempt,
                            self.GOLSINYALI_MAX_RETRY_WAIT_SECONDS,
                        )
                    )

        detail = last_error or (f"HTTP {last_status}" if last_status else "Unknown error")
        if last_body_hint and "http 429" not in detail.lower():
            detail = f"{detail}; body={last_body_hint!r}"
        raise RuntimeError(f"Golsinyali request failed for {url}: {detail}")

    @staticmethod
    def _golsinyali_compact_text(value):
        value = str(value or "").lower()
        value = value.replace("&", " and ")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return " ".join(value.split())

    def _golsinyali_candidate_score(self, anchor_text, url):
        combined = self._golsinyali_compact_text(
            f"{anchor_text} {url}"
        )
        if not combined:
            return 0

        score = 0
        for match_key in self.master_matrix.keys():
            parts = [p.strip() for p in match_key.split(" vs ", 1)]
            if len(parts) != 2:
                continue
            home = self._golsinyali_compact_text(parts[0])
            away = self._golsinyali_compact_text(parts[1])
            if home and home in combined:
                score += 2
            if away and away in combined:
                score += 2
            if home and away and home in combined and away in combined:
                score += 5

        return score

    def fetch_golsinyali_sync(self):
        """Fetch Golsinyali match pages and add normalized predictions to consensus."""
        try:
            predictions_html = self._fetch_golsinyali_html(
                self.GOLSINYALI_PREDICTIONS_URL
            )

            soup = BeautifulSoup(predictions_html, "html.parser")
            anchor_data = []
            seen = set()

            for anchor in soup.find_all("a", href=True):
                href = anchor["href"].strip()
                if not href.startswith("/en/match/"):
                    continue

                url = f"{self.GOLSINYALI_BASE_URL}{href}"
                if url in seen:
                    continue

                seen.add(url)
                anchor_data.append((url, anchor.get_text(" ", strip=True)))

            if not anchor_data:
                self.diagnostics["Golsinyali"] = (
                    "🟡 BLOCKED (No match links found)"
                )
                return

            # Prefer links that correspond to fixtures already discovered by the
            # original five sources. This mirrors the friend's DOMINION adapter
            # and dramatically reduces unnecessary match-page requests/rate limits.
            scored = []
            for url, anchor_text in anchor_data:
                score = self._golsinyali_candidate_score(anchor_text, url)
                if score > 0:
                    scored.append((score, url, anchor_text))

            if scored:
                scored.sort(key=lambda item: (-item[0], item[1]))
                candidates = [(url, anchor_text) for _, url, anchor_text in scored]
            else:
                # Safe fallback when the other sources produced no fixtures.
                candidates = anchor_data

            candidates = candidates[: self.GOLSINYALI_MAX_MATCH_PAGES]

            eat_tz = datetime.timezone(datetime.timedelta(hours=3))
            today_eat = (
                datetime.datetime.now(datetime.timezone.utc)
                .astimezone(eat_tz)
                .date()
            )

            valid_count = 0
            skipped_count = 0
            failed_count = 0
            matched_fixture_count = 0
            new_fixture_count = 0

            for link, _anchor_text in candidates:
                try:
                    if self.GOLSINYALI_REQUEST_DELAY_SECONDS > 0:
                        time.sleep(self.GOLSINYALI_REQUEST_DELAY_SECONDS)

                    match_html = self._fetch_golsinyali_html(
                        link,
                        referer=self.GOLSINYALI_PREDICTIONS_URL,
                    )
                    sports_event = self._golsinyali_extract_sports_event_jsonld(
                        match_html
                    )
                    if sports_event is None:
                        failed_count += 1
                        continue

                    metadata = self._golsinyali_extract_fixture_metadata(
                        sports_event
                    )
                    if metadata is None:
                        failed_count += 1
                        continue

                    kickoff_eat = metadata["kickoff"].astimezone(eat_tz)
                    if kickoff_eat.date() != today_eat:
                        skipped_count += 1
                        continue

                    event_status = str(metadata.get("event_status") or "").lower()
                    if any(
                        flag in event_status
                        for flag in (
                            "postponed",
                            "cancelled",
                            "canceled",
                            "finished",
                        )
                    ):
                        skipped_count += 1
                        continue

                    prediction = self._golsinyali_extract_prediction(
                        sports_event,
                        match_html,
                    )
                    if prediction is None:
                        skipped_count += 1
                        continue

                    log_result = self.log_prediction_qa(
                        "Golsinyali",
                        metadata["home_team"],
                        metadata["away_team"],
                        prediction,
                    )

                    if log_result is None:
                        failed_count += 1
                        continue

                    if log_result["matched_existing_fixture"]:
                        matched_fixture_count += 1
                    else:
                        new_fixture_count += 1

                    valid_count += 1

                except Exception as exc:
                    failed_count += 1
                    print(
                        f"Golsinyali: failed to process {link}: {exc}"
                    )

            self.diagnostics["Golsinyali_Detail"] = (
                f"📊 Discovered: {len(anchor_data)} | "
                f"Candidates: {len(candidates)} | "
                f"Today predictions: {valid_count} | "
                f"Matched existing fixtures: {matched_fixture_count} | "
                f"New/unmatched fixtures: {new_fixture_count} | "
                f"Skipped: {skipped_count} | "
                f"Failed: {failed_count}"
            )

            if valid_count > 0:
                self.diagnostics["Golsinyali"] = (
                    f"🟢 OK ({valid_count} Today | "
                    f"{matched_fixture_count} Matched | "
                    f"{new_fixture_count} New | "
                    f"{skipped_count} Skipped | "
                    f"{failed_count} Failed)"
                )
            elif failed_count > 0 and skipped_count == 0:
                self.diagnostics["Golsinyali"] = (
                    f"🔴 FAILED ({failed_count} page requests failed)"
                )
            else:
                self.diagnostics["Golsinyali"] = (
                    f"🟡 NO USABLE PREDICTIONS ({skipped_count} Skipped | "
                    f"{failed_count} Failed)"
                )

        except Exception as exc:
            self.diagnostics["Golsinyali"] = f"🔴 FAILED ({exc})"

    # ==========================================================================
    # EXPECTED90 DEDICATED ADAPTER
    # ==========================================================================
    EXPECTED90_BASE_URL = "https://expected90.com"
    EXPECTED90_PREDICTIONS_URL = f"{EXPECTED90_BASE_URL}/football-predictions"
    EXPECTED90_REQUEST_TIMEOUT = 30
    EXPECTED90_MAX_MATCH_PAGES = 60
    EXPECTED90_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    )

    def _fetch_expected90_html(self, url, referer=None):
        headers = {
            "User-Agent": self.EXPECTED90_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if referer:
            headers["Referer"] = referer

        response = requests.get(
            url,
            headers=headers,
            timeout=self.EXPECTED90_REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Expected90 returned HTTP {response.status_code}"
            )

        html = response.text
        if not html.strip():
            raise RuntimeError("Expected90 returned an empty response.")

        return html

    @staticmethod
    def _expected90_parse_json_ld(html):
        soup = BeautifulSoup(html, "html.parser")
        objects = []

        for script in soup.find_all("script", type="application/ld+json"):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue

            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                continue

            if isinstance(parsed, list):
                objects.extend(parsed)
            else:
                objects.append(parsed)

        return objects

    @staticmethod
    def _expected90_find_sports_event(objects):
        for obj in objects:
            if not isinstance(obj, dict):
                continue

            if obj.get("@type") == "SportsEvent":
                return obj

            graph = obj.get("@graph")
            if isinstance(graph, list):
                for graph_item in graph:
                    if (
                        isinstance(graph_item, dict)
                        and graph_item.get("@type") == "SportsEvent"
                    ):
                        return graph_item

        return None

    @staticmethod
    def _expected90_parse_kickoff(value):
        if not value:
            return None

        text = str(value).strip()
        candidates = [text, text.replace("Z", "+00:00")]

        for candidate in candidates:
            try:
                parsed = datetime.datetime.fromisoformat(candidate)
            except ValueError:
                continue

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)

            return parsed.astimezone(datetime.timezone.utc)

        return None

    @staticmethod
    def _expected90_extract_probabilities(description):
        if not description:
            return None

        pattern = re.compile(
            r"""
            (?P<home>[A-Za-z][^,%]*?)
            \s+
            (?P<home_pct>\d+(?:\.\d+)?)%
            \s*,\s*
            draw
            \s+
            (?P<draw_pct>\d+(?:\.\d+)?)%
            \s*,\s*
            (?P<away>[A-Za-z][^,%]*?)
            \s+
            (?P<away_pct>\d+(?:\.\d+)?)%
            """,
            re.IGNORECASE | re.VERBOSE,
        )

        match = pattern.search(str(description))
        if not match:
            return None

        try:
            return {
                "HOME": float(match.group("home_pct")),
                "DRAW": float(match.group("draw_pct")),
                "AWAY": float(match.group("away_pct")),
            }
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _expected90_probability_to_selection(probabilities):
        if not probabilities:
            return None
        return max(probabilities, key=probabilities.get)

    @staticmethod
    def _expected90_compact_text(value):
        value = str(value or "").lower()
        value = value.replace("&", " and ")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return " ".join(value.split())

    def _expected90_extract_match_links(self, html):
        soup = BeautifulSoup(html, "html.parser")
        links = []
        seen = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href.startswith("/football-predictions/"):
                continue

            # Expected90 individual match URLs are:
            # /football-predictions/<league>/<home>-vs-<away>
            if not re.search(
                r"^/football-predictions/[^/]+/[^/]+-vs-[^/]+/?$",
                href,
                flags=re.IGNORECASE,
            ):
                continue

            url = f"{self.EXPECTED90_BASE_URL}{href}"
            if url in seen:
                continue

            seen.add(url)
            links.append(url)

        return links

    def _expected90_candidate_score(self, url):
        compact_url = self._expected90_compact_text(url)
        score = 0

        for match_key in self.master_matrix.keys():
            parts = [p.strip() for p in match_key.split(" vs ", 1)]
            if len(parts) != 2:
                continue

            home = self._expected90_compact_text(parts[0])
            away = self._expected90_compact_text(parts[1])

            if home and home in compact_url:
                score += 2
            if away and away in compact_url:
                score += 2
            if home and away and home in compact_url and away in compact_url:
                score += 5

        return score

    def fetch_expected90_sync(self):
        """Fetch Expected90's daily match pages and feed 1X2 into consensus."""
        try:
            hub_html = self._fetch_expected90_html(
                self.EXPECTED90_PREDICTIONS_URL
            )

            all_links = self._expected90_extract_match_links(hub_html)

            if not all_links:
                self.diagnostics["Expected90_Detail"] = (
                    "📊 Discovered: 0 | Candidates: 0 | Today predictions: 0 | "
                    "Matched existing fixtures: 0 | New/unmatched fixtures: 0 | "
                    "Skipped: 0 | Failed: 0"
                )
                self.diagnostics["Expected90"] = (
                    "🟡 NO MATCH LINKS FOUND"
                )
                return

            scored_links = []
            for url in all_links:
                score = self._expected90_candidate_score(url)
                if score > 0:
                    scored_links.append((score, url))

            if scored_links:
                scored_links.sort(key=lambda item: (-item[0], item[1]))
                candidates = [
                    url for _, url in scored_links[:self.EXPECTED90_MAX_MATCH_PAGES]
                ]
            else:
                # Safe fallback when the base five sources don't provide
                # candidate fixtures that Expected90 can match.
                candidates = all_links[:self.EXPECTED90_MAX_MATCH_PAGES]

            eat_tz = datetime.timezone(datetime.timedelta(hours=3))
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            today_eat = now_utc.astimezone(eat_tz).date()

            valid_count = 0
            skipped_count = 0
            failed_count = 0
            matched_fixture_count = 0
            new_fixture_count = 0

            for link in candidates:
                try:
                    html = self._fetch_expected90_html(
                        link,
                        referer=self.EXPECTED90_PREDICTIONS_URL,
                    )

                    objects = self._expected90_parse_json_ld(html)
                    sports_event = self._expected90_find_sports_event(objects)

                    if sports_event is None:
                        failed_count += 1
                        continue

                    home_data = sports_event.get("homeTeam")
                    away_data = sports_event.get("awayTeam")

                    if not isinstance(home_data, dict) or not isinstance(away_data, dict):
                        failed_count += 1
                        continue

                    home = str(home_data.get("name") or "").strip()
                    away = str(away_data.get("name") or "").strip()
                    kickoff = self._expected90_parse_kickoff(
                        sports_event.get("startDate")
                    )

                    if not home or not away or kickoff is None:
                        failed_count += 1
                        continue

                    kickoff_eat = kickoff.astimezone(eat_tz)

                    if kickoff_eat.date() != today_eat:
                        skipped_count += 1
                        continue

                    # Ignore matches that have already started/finished.
                    if kickoff <= now_utc:
                        skipped_count += 1
                        continue

                    description = sports_event.get("description") or ""
                    probabilities = self._expected90_extract_probabilities(description)

                    if not probabilities:
                        skipped_count += 1
                        continue

                    prediction = self._expected90_probability_to_selection(
                        probabilities
                    )

                    if prediction is None:
                        skipped_count += 1
                        continue

                    log_result = self.log_prediction_qa(
                        "Expected90",
                        home,
                        away,
                        prediction,
                    )

                    if log_result is None:
                        failed_count += 1
                        continue

                    if log_result["matched_existing_fixture"]:
                        matched_fixture_count += 1
                    else:
                        new_fixture_count += 1

                    valid_count += 1

                except Exception as exc:
                    failed_count += 1
                    print(
                        f"Expected90: failed to process {link}: {exc}"
                    )

            self.diagnostics["Expected90_Detail"] = (
                f"📊 Discovered: {len(all_links)} | "
                f"Candidates: {len(candidates)} | "
                f"Today predictions: {valid_count} | "
                f"Matched existing fixtures: {matched_fixture_count} | "
                f"New/unmatched fixtures: {new_fixture_count} | "
                f"Skipped: {skipped_count} | "
                f"Failed: {failed_count}"
            )

            if valid_count > 0:
                self.diagnostics["Expected90"] = (
                    f"🟢 OK ({valid_count} Today | "
                    f"{matched_fixture_count} Matched | "
                    f"{new_fixture_count} New | "
                    f"{skipped_count} Skipped | "
                    f"{failed_count} Failed)"
                )
            elif failed_count > 0:
                self.diagnostics["Expected90"] = (
                    f"🔴 FAILED ({failed_count} page requests failed)"
                )
            else:
                self.diagnostics["Expected90"] = (
                    f"🟡 NO USABLE PREDICTIONS ({skipped_count} Skipped)"
                )

        except Exception as exc:
            self.diagnostics["Expected90"] = f"🔴 FAILED ({exc})"

    # ======================================================================
    # SOCCERAITIPS DEDICATED ADAPTER (SECONDARY MARKETS)
    # ======================================================================

    SOCCERAITIPS_BASE_URL = "https://www.socceraitips.com"
    SOCCERAITIPS_DAILY_PARLAY_PATH = "/api/daily-parlay"
    SOCCERAITIPS_REQUEST_TIMEOUT = 30
    SOCCERAITIPS_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )

    @staticmethod
    def _socceraitips_parse_display_time(value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        try:
            datetime.datetime.strptime(value, "%H:%M")
        except ValueError:
            return None
        return value

    @staticmethod
    def _socceraitips_parse_utc_datetime(value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None

        formats = (
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        )

        for fmt in formats:
            try:
                parsed = datetime.datetime.strptime(value, fmt)
                return parsed.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue

        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _socceraitips_normalize_market(bet_type, prediction_display):
        normalized_bet_type = bet_type.strip().lower() if isinstance(bet_type, str) else ""
        normalized_display = prediction_display.strip().upper() if isinstance(prediction_display, str) else ""

        if normalized_bet_type == "over_2_5" and (
            normalized_display == "OVER 2.5"
        ):
            return "OVER_2.5"

        if normalized_bet_type in {"kg_var", "btts"} and normalized_display == "BTTS":
            return "BTTS"

        return None

    @staticmethod
    def _socceraitips_normalize_selection(bet_type, prediction, prediction_display):
        normalized_bet_type = bet_type.strip().lower() if isinstance(bet_type, str) else ""
        normalized_prediction = prediction.strip().upper() if isinstance(prediction, str) else ""
        normalized_display = prediction_display.strip().upper() if isinstance(prediction_display, str) else ""

        if normalized_bet_type == "over_2_5":
            if normalized_prediction == "ÜST" or normalized_display == "OVER 2.5":
                return "OVER_2.5"
            return None

        if normalized_bet_type in {"kg_var", "btts"}:
            if normalized_prediction == "VAR" or normalized_display == "BTTS":
                return "BTTS_YES"
            return None

        return None

    def _socceraitips_find_existing_match(self, home, away):
        raw_key = f"{self.clean_team_name(home)} vs {self.clean_team_name(away)}"
        best_key = raw_key
        best_ratio = 0.0

        for existing_key in self.master_matrix.keys():
            ratio = difflib.SequenceMatcher(
                None,
                raw_key.lower(),
                existing_key.lower(),
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_key = existing_key

        if best_ratio >= 0.75:
            return best_key, True
        return raw_key, False

    def fetch_socceraitips_sync(self):
        """
        Fetch SoccerAiTips daily-parlay data.

        SoccerAiTips' current adapter exposes secondary markets (BTTS and
        Over 2.5) rather than a canonical 1X2 prediction. Therefore these
        records are intentionally NOT added to the 1X2 consensus matrix.
        They are retained as secondary-market evidence for diagnostics and
        Gemini optimization.
        """
        try:
            url = f"{self.SOCCERAITIPS_BASE_URL}{self.SOCCERAITIPS_DAILY_PARLAY_PATH}"
            response = requests.get(
                url,
                params={"locale": "en"},
                headers={
                    "User-Agent": self.SOCCERAITIPS_USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": f"{self.SOCCERAITIPS_BASE_URL}/en",
                    "Origin": self.SOCCERAITIPS_BASE_URL,
                },
                timeout=self.SOCCERAITIPS_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            if not response.text.strip():
                raise ValueError("Empty response received from SoccerAiTips.")

            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Unexpected SoccerAiTips response structure.")
            if payload.get("success") is not True:
                raise ValueError("SoccerAiTips returned an unsuccessful response.")

            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("SoccerAiTips response does not contain a valid data object.")

            matches = data.get("matches")
            if not isinstance(matches, list):
                raise ValueError("SoccerAiTips response does not contain a valid matches list.")

            eat_tz = datetime.timezone(datetime.timedelta(hours=3))
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            today_eat = now_utc.astimezone(eat_tz).date()

            valid_count = 0
            matched_count = 0
            new_count = 0
            skipped_count = 0
            failed_count = 0
            secondary_records = []

            for match in matches:
                if not isinstance(match, dict):
                    skipped_count += 1
                    continue

                home = match.get("home_team")
                away = match.get("away_team")
                match_time = match.get("match_time")
                match_time_utc = match.get("match_time_utc")
                bet_type = match.get("bet_type")
                prediction = match.get("prediction")
                prediction_display = match.get("prediction_display")

                if not all(isinstance(value, str) and value.strip() for value in [home, away, bet_type, prediction, prediction_display]):
                    failed_count += 1
                    continue

                kickoff = self._socceraitips_parse_utc_datetime(match_time_utc)
                if kickoff is None:
                    failed_count += 1
                    continue

                kickoff_eat = kickoff.astimezone(eat_tz)
                if kickoff_eat.date() != today_eat or kickoff <= now_utc:
                    skipped_count += 1
                    continue

                market = self._socceraitips_normalize_market(
                    bet_type,
                    prediction_display,
                )
                selection = self._socceraitips_normalize_selection(
                    bet_type,
                    prediction,
                    prediction_display,
                )

                if market is None or selection is None:
                    skipped_count += 1
                    continue

                match_key, matched_existing = self._socceraitips_find_existing_match(
                    home,
                    away,
                )

                record = {
                    "source": "SoccerAiTips",
                    "match": match_key,
                    "home_team": self.clean_team_name(home),
                    "away_team": self.clean_team_name(away),
                    "kickoff": kickoff.isoformat(),
                    "display_time": self._socceraitips_parse_display_time(match_time),
                    "market": market,
                    "selection": selection,
                    "bet_type": bet_type,
                    "prediction": prediction,
                    "prediction_display": prediction_display,
                    "confidence": match.get("confidence"),
                    "league": match.get("league"),
                    "matched_existing_fixture": matched_existing,
                }

                secondary_records.append(record)
                valid_count += 1

                if matched_existing:
                    matched_count += 1
                else:
                    new_count += 1

            self.secondary_market_data.extend(secondary_records)
            self.diagnostics["SoccerAiTips_Detail"] = (
                f"📊 Discovered: {len(matches)} | "
                f"Today markets: {valid_count} | "
                f"Matched existing fixtures: {matched_count} | "
                f"New/unmatched fixtures: {new_count} | "
                f"Skipped: {skipped_count} | Failed: {failed_count}"
            )

            if valid_count > 0:
                self.diagnostics["SoccerAiTips"] = (
                    f"🟢 OK ({valid_count} Secondary Markets | "
                    f"{matched_count} Matched | {new_count} New | "
                    f"{skipped_count} Skipped | {failed_count} Failed)"
                )
            elif failed_count > 0:
                self.diagnostics["SoccerAiTips"] = (
                    f"🔴 FAILED ({failed_count} Invalid/failed records)"
                )
            else:
                self.diagnostics["SoccerAiTips"] = (
                    f"🟡 NO USABLE SECONDARY MARKETS ({skipped_count} Skipped)"
                )

        except Exception as exc:
            self.diagnostics["SoccerAiTips"] = f"🔴 FAILED ({exc})"
            self.diagnostics["SoccerAiTips_Detail"] = "No secondary-market data collected."

    # ======================================================================
    # NVTIPS DEDICATED ADAPTER
    # ======================================================================

    NVTIPS_BASE_URL = "https://nvtips.com"
    NVTIPS_REQUEST_TIMEOUT = 30
    NVTIPS_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )

    @classmethod
    def _nvtips_build_url(cls, target_date):
        return (
            f"{cls.NVTIPS_BASE_URL}/"
            f"?d={target_date.day}&m={target_date.month}&y={target_date.year}"
        )

    @classmethod
    def _fetch_nvtips_html(cls, target_date):
        url = cls._nvtips_build_url(target_date)
        headers = {
            "User-Agent": cls.NVTIPS_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=cls.NVTIPS_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"NVtips request failed: {exc}") from exc

        if response.status_code != 200:
            # Optional proxy fallback using the repository's existing
            # ScraperAPI credential. Direct requests remain the primary path.
            if SCRAPER_API_KEY:
                try:
                    proxy_url = "https://api.scraperapi.com/"
                    proxy_response = requests.get(
                        proxy_url,
                        params={
                            "api_key": SCRAPER_API_KEY,
                            "url": url,
                            "premium": "true",
                            "country_code": "us",
                        },
                        headers=headers,
                        timeout=60,
                    )
                    if proxy_response.status_code == 200 and proxy_response.text.strip():
                        return proxy_response.text
                except requests.RequestException:
                    pass

            raise RuntimeError(
                f"NVtips returned HTTP {response.status_code}"
            )

        html = response.text
        if not html.strip():
            raise RuntimeError("NVtips returned an empty response.")

        if "NVtips" not in html:
            raise RuntimeError("NVtips branding was not found in the response.")

        return html

    @staticmethod
    def _nvtips_clean_text(value):
        return re.sub(r"\s+", " ", value or "").strip()

    @classmethod
    def _nvtips_extract_team_names(cls, row):
        team_nodes = row.select(".nv-team-name")
        teams = [
            cls._nvtips_clean_text(node.get_text(" ", strip=True))
            for node in team_nodes
        ]
        teams = [team for team in teams if team]
        if len(teams) < 2:
            return None
        return teams[0], teams[1]

    @staticmethod
    def _nvtips_extract_probabilities(row_text):
        values = re.findall(r"\b(\d{1,3}(?:\.\d+)?)%", row_text)
        if len(values) < 3:
            return None
        try:
            probabilities = tuple(float(value) for value in values[:3])
        except ValueError:
            return None
        if any(value < 0.0 or value > 100.0 for value in probabilities):
            return None
        return probabilities

    @classmethod
    def _nvtips_extract_prediction(cls, row):
        data_search = cls._nvtips_clean_text(row.get("data-search", "")).lower()
        score_prediction = re.search(
            r"\b([1x2])\s+(\d+)\s*-\s*(\d+)\b",
            data_search,
            re.IGNORECASE,
        )
        if score_prediction:
            return score_prediction.group(1).upper()

        row_text = cls._nvtips_clean_text(row.get_text(" ", strip=True))
        visible_match = re.search(
            r"\b([1x2])\b\s+\d+\s*-\s*\d+\b",
            row_text,
            re.IGNORECASE,
        )
        if visible_match:
            return visible_match.group(1).upper()

        return None

    @classmethod
    def _nvtips_extract_rows(cls, html):
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("div.nv-row")
        extracted = []

        for row in rows:
            teams = cls._nvtips_extract_team_names(row)
            if teams is None:
                continue

            home_team, away_team = teams
            row_text = cls._nvtips_clean_text(
                row.get_text(" ", strip=True)
            )
            probabilities = cls._nvtips_extract_probabilities(row_text)
            if probabilities is None:
                continue

            prediction = cls._nvtips_extract_prediction(row)
            if prediction is None:
                continue

            extracted.append(
                {
                    "home_team": home_team,
                    "away_team": away_team,
                    "competition": cls._nvtips_clean_text(
                        row.get("data-league", "")
                    ),
                    "country": cls._nvtips_clean_text(
                        row.get("data-country", "")
                    ),
                    "source_time": cls._nvtips_clean_text(
                        row.get("data-time", "")
                    ),
                    "probabilities": probabilities,
                    "prediction": prediction,
                }
            )

        return extracted

    def fetch_nvtips_sync(self):
        """Fetch today's NVtips rows and feed 1X2 predictions into consensus."""
        try:
            eat_tz = datetime.timezone(datetime.timedelta(hours=3))
            today_eat = datetime.datetime.now(datetime.timezone.utc).astimezone(eat_tz).date()
            html = self._fetch_nvtips_html(today_eat)
            rows = self._nvtips_extract_rows(html)

            valid_count = 0
            matched_fixture_count = 0
            new_fixture_count = 0
            skipped_count = 0
            failed_count = 0

            for row in rows:
                try:
                    home = row["home_team"]
                    away = row["away_team"]
                    prediction = row["prediction"]

                    if not home or not away or prediction not in {"1", "X", "2"}:
                        skipped_count += 1
                        continue

                    # NVtips' daily page is date-scoped, so its displayed time
                    # is used only for discovery. The canonical engine fixture
                    # remains authoritative for kickoff.
                    log_result = self.log_prediction_qa(
                        "NVtips",
                        home,
                        away,
                        prediction,
                    )

                    if log_result is None:
                        failed_count += 1
                        continue

                    if log_result["matched_existing_fixture"]:
                        matched_fixture_count += 1
                    else:
                        new_fixture_count += 1

                    valid_count += 1

                except Exception as exc:
                    failed_count += 1
                    print(
                        f"NVtips: failed to process "
                        f"{row.get('home_team')} vs {row.get('away_team')}: {exc}"
                    )

            self.diagnostics["NVtips_Detail"] = (
                f"📊 Discovered: {len(rows)} | "
                f"Today predictions: {valid_count} | "
                f"Matched existing fixtures: {matched_fixture_count} | "
                f"New/unmatched fixtures: {new_fixture_count} | "
                f"Skipped: {skipped_count} | "
                f"Failed: {failed_count}"
            )

            if valid_count > 0:
                self.diagnostics["NVtips"] = (
                    f"🟢 OK ({valid_count} Today | "
                    f"{matched_fixture_count} Matched | "
                    f"{new_fixture_count} New | "
                    f"{skipped_count} Skipped | "
                    f"{failed_count} Failed)"
                )
            elif failed_count > 0:
                self.diagnostics["NVtips"] = (
                    f"🔴 FAILED ({failed_count} row processing failures)"
                )
            else:
                self.diagnostics["NVtips"] = (
                    f"🟡 NO USABLE PREDICTIONS ({len(rows)} Rows)"
                )

        except Exception as exc:
            self.diagnostics["NVtips"] = f"🔴 FAILED ({exc})"
            self.diagnostics["NVtips_Detail"] = (
                f"📊 Discovery failed: {type(exc).__name__}: {exc}"
            )

    def fetch_and_scrape_sync(self, site_name, cfg):
        if site_name == "Golsinyali":
            self.fetch_golsinyali_sync()
            return

        if site_name == "Expected90":
            self.fetch_expected90_sync()
            return

        if site_name == "SoccerAiTips":
            self.fetch_socceraitips_sync()
            return

        if site_name == "NVtips":
            self.fetch_nvtips_sync()
            return

        max_attempts = 3
        last_status = None
        target_url = cfg["url"]

        strict_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1"
        }

        for attempt in range(1, max_attempts + 1):
            try:
                active_url = cfg.get("fallback_url") if (attempt == 3 and cfg.get("fallback_url")) else target_url

                if attempt == 1 and cfg.get("use_scraperapi") and SCRAPER_API_KEY:
                    proxy_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={active_url}"
                    if site_name == "SoccerVista": 
                        proxy_url += "&render=true"
                    if site_name == "PredictZ": 
                        proxy_url += "&premium=true&render=true" 
                    r = requests.get(proxy_url, timeout=60)
                
                elif attempt == 2:
                    r = tls_requests.get(active_url, impersonate="chrome124", headers=strict_headers, timeout=30)
                
                else:
                    if cfg.get("use_scraperapi") and SCRAPER_API_KEY:
                        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={active_url}&premium=true&country_code=us"
                        r = requests.get(proxy_url, timeout=60)
                    else:
                        r = tls_requests.get(active_url, impersonate="safari17_0", headers=strict_headers, timeout=30)

                last_status = r.status_code

                if r.status_code == 200:
                    challenge_phrases = ["just a moment...", "cf-browser-verification", "checking your browser", "turnstile", "ray id", "security check"]
                    if any(phrase in r.text.lower() for phrase in challenge_phrases) and len(r.text) < 25000:
                        if attempt < max_attempts:
                            time.sleep(2 * attempt)
                            continue
                        self.diagnostics[site_name] = "🟡 BLOCKED (Cloudflare Challenge)"
                        return

                    soup = BeautifulSoup(r.content, 'html.parser')
                    
                    if site_name in ["PredictZ", "WinDrawWin"]:
                        prefix = "pt" if site_name == "PredictZ" else "wt"
                        rows = soup.find_all("div", class_=re.compile(f"({prefix}tr|{prefix}row|match-row)"))
                        if not rows:
                            h_elements = soup.find_all("div", class_=re.compile(f"{prefix}tmobh|{prefix}team|{prefix}tm"))
                            rows = [h.parent for h in h_elements if h.parent]
                    elif site_name == "SoccerVista":
                        rows = soup.find_all("tr")
                        if not rows:
                            rows = soup.find_all("div", class_=re.compile("predict|match"))
                    else:
                        row_target = cfg["row_class"]
                        rows = soup.find_all(cfg["row_selector"], class_=row_target)

                    if not rows:
                        if attempt < max_attempts:
                            time.sleep(2 * attempt)
                            continue
                        self.diagnostics[site_name] = "🟡 BLOCKED (No Match Rows Found)"
                        return

                    valid_count = 0
                    skipped_count = 0

                    for row in rows:
                        try:
                            if self.is_match_active_or_played(row):
                                skipped_count += 1
                                continue

                            home, away, pick = None, None, None

                            if site_name in ["PredictZ", "WinDrawWin"]:
                                prefix = "pt" if site_name == "PredictZ" else "wt"
                                
                                h_elem = row.find(class_=f"{prefix}tmobh")
                                a_elem = row.find(class_=f"{prefix}tmoba")
                                p_elem = row.find(class_=re.compile(f"{prefix}oddsdesc|{prefix}mobpred"))

                                if h_elem and a_elem and p_elem:
                                    home = h_elem.text
                                    away = a_elem.text
                                    pick = p_elem.text
                                else:
                                    links = row.find_all("a")
                                    if len(links) >= 2:
                                        home = links[0].text
                                        away = links[1].text
                                        p_div = row.find(class_=re.compile(f"{prefix}prd|{prefix}pred"))
                                        if p_div: pick = p_div.text
                                    else:
                                        for td in row.find_all("div", class_=f"{prefix}td"):
                                            norm = self.normalize_prediction(td.text)
                                            if norm:
                                                pick = norm
                                                break
                                                
                            elif site_name == "SoccerVista":
                                tds = row.find_all("td")
                                if len(tds) >= 3:
                                    raw_home = tds[1].text.strip()
                                    if len(tds) >= 4 and (re.search(r'\d+:\d+', tds[2].text) or tds[2].text.strip() in ["-", "vs", "v", ""]):
                                        raw_away = tds[3].text.strip()
                                    else:
                                        raw_away = tds[2].text.strip()
                                        
                                    home = re.sub(r'^([WDL]\s+)+', '', raw_home).strip()
                                    away = re.sub(r'(\s+[WDL])+$', '', raw_away).strip()
                                    
                                    for td in tds:
                                        txt = td.text.strip().upper()
                                        if "10 ON " in txt:
                                            target = txt.replace("10 ON ", "").strip()
                                            if target in ["DRAW", "X"]: pick = "X"
                                            elif target and (target in home.upper() or home.upper().startswith(target)): pick = "1"
                                            elif target and (target in away.upper() or away.upper().startswith(target)): pick = "2"
                                            elif len(target) >= 3 and target[:3] in home.upper(): pick = "1"
                                            elif len(target) >= 3 and target[:3] in away.upper(): pick = "2"
                                            else: pick = "1"
                                            break
                                        elif txt in ["1", "X", "2", "1X", "X2", "12"]:
                                            pick = txt
                                            break

                            else:
                                home = row.find_all(cfg["home_selector"], class_=cfg["home_class"])[cfg["home_index"]].text
                                away = row.find_all(cfg["away_selector"], class_=cfg["away_class"])[cfg["away_index"]].text
                                pick = row.find_all(cfg["pick_selector"], class_=cfg["pick_class"])[cfg["pick_index"]].text

                            home_str = self.clean_team_name(home) if home else ""
                            away_str = self.clean_team_name(away) if away else ""

                            if not home_str and away_str:
                                if re.search(r'(?i)\s+vs?\s+', away_str):
                                    parts = re.split(r'(?i)\s+vs?\s+', away_str, maxsplit=1)
                                    home_str, away_str = self.clean_team_name(parts[0]), self.clean_team_name(parts[1])

                            if not away_str and home_str:
                                if re.search(r'(?i)\s+vs?\s+', home_str):
                                    parts = re.split(r'(?i)\s+vs?\s+', home_str, maxsplit=1)
                                    home_str, away_str = self.clean_team_name(parts[0]), self.clean_team_name(parts[1])

                            if re.search(r'(?i)\s+vs?\s+', away_str) and len(home_str) < 4:
                                parts = re.split(r'(?i)\s+vs?\s+', away_str, maxsplit=1)
                                if len(parts) == 2:
                                    home_str, away_str = self.clean_team_name(parts[0]), self.clean_team_name(parts[1])

                            if re.search(r'(?i)\s+vs?\s+', home_str) and len(away_str) < 4:
                                parts = re.split(r'(?i)\s+vs?\s+', home_str, maxsplit=1)
                                if len(parts) == 2:
                                    home_str, away_str = self.clean_team_name(parts[0]), self.clean_team_name(parts[1])

                            home, away = home_str, away_str

                            if home and away and pick:
                                self.log_prediction_qa(site_name, home, away, pick)
                                valid_count += 1

                        except Exception:
                            continue

                    if valid_count > 0 or skipped_count > 0:
                        self.diagnostics[site_name] = f"🟢 OK ({valid_count} Upcoming | {skipped_count} Played)"
                        return

                if r.status_code in [403, 500, 502, 503, 504, 429]:
                    time.sleep(2 * attempt)
                    continue
                else:
                    time.sleep(2 * attempt)
                    continue

            except Exception:
                time.sleep(2 * attempt)
                continue

        self.diagnostics[site_name] = f"🔴 FAILED (HTTP {last_status if last_status else 'TIMEOUT'})"

    def process_consensus_signals(self):
        agreed_matches = []
        structured_tickets = []
        ai_input_data = []

        all_scrapers = [
            "Golsinyali",
            "Expected90",
            "Statarea",
            "Vitibet",
            "PredictZ",
            "WinDrawWin",
            "SoccerVista",
            "NVtips",
        ]
        
        required_consensus = 3 

        for match, listings in self.master_matrix.items():
            prediction_weights = {}
            sites_backing = {}
            for site, pick in listings:
                prediction_weights[pick] = prediction_weights.get(pick, 0) + 1
                if pick not in sites_backing: sites_backing[pick] = []
                sites_backing[pick].append(site)

            if not prediction_weights:
                continue

            top_pick = max(prediction_weights, key=prediction_weights.get)
            
            if prediction_weights[top_pick] >= required_consensus:
                backing_sites_list = sites_backing[top_pick]
                backing_sites_str = " + ".join(backing_sites_list)
                contradictions = []

                match_text = (
                    f"• **{match}** ➔ {top_pick}\n"
                    f"  ↳ ✅ Backed by: `{backing_sites_str}`\n"
                )

                left_out_sites = [s for s in all_scrapers if s not in backing_sites_list]
                for left_out in left_out_sites:
                    other_pick = None
                    for pick, sites in sites_backing.items():
                        if pick != top_pick and left_out in sites:
                            other_pick = pick
                            break
                    
                    if other_pick: 
                        match_text += f"  ↳ ⚠️ {left_out} backed: {other_pick}\n"
                        contradictions.append(f"{left_out} ({other_pick})")
                    else: 
                        match_text += f"  ↳ ⚪ {left_out}: Not Listed\n"

                agreed_matches.append(match_text)
                structured_tickets.append({"match": match, "prediction": top_pick, "status": "PENDING", "score": "-"})
                
                ai_input_data.append({
                    "match": match, 
                    "consensus_pick": top_pick, 
                    "backed_by": backing_sites_str, 
                    "contradictions": contradictions, 
                    "tier": "Core Consensus"
                })

        return agreed_matches, structured_tickets, ai_input_data, required_consensus

    def get_available_gemini_models(self, api_key):
        list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        try:
            res = requests.get(list_url, timeout=15)
            if res.status_code == 200:
                data = res.json()
                available = []
                for m in data.get("models", []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        name = m.get("name", "").replace("models/", "")
                        if name:
                            available.append(name)
                
                preferred = [m for m in available if "flash" in m.lower() and not any(x in m.lower() for x in ["preview", "thinking", "lite"])]
                fallback_flash = [m for m in available if "flash" in m.lower() and m not in preferred]
                others = [m for m in available if m not in preferred and m not in fallback_flash]
                
                ordered = preferred + fallback_flash + others
                if ordered:
                    return ordered
        except Exception:
            pass
        return ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

    def ask_llm_to_optimize_tickets(self, ai_input_data, active_corner_teams, secondary_market_data=None):
        api_key = (GEMINI_API_KEY or "").strip()
        if not api_key:
            self.diagnostics["AI_Status"] = "🔴 Missing GEMINI_API_KEY"
            return None

        models_to_try = self.get_available_gemini_models(api_key)
        secondary_market_data = secondary_market_data or []

        prompt = f"""
        You are Titan, an elite quantitative sports betting AI Portfolio Manager.
        Your objective is to analyze the following raw consensus data and corner statistics, 
        and construct highly optimized, risk-mitigated betting tickets.

        === RAW CONSENSUS DATA ===
        {json.dumps(ai_input_data, indent=2)}

        === HIGH-PROBABILITY CORNER STATISTICS ===
        {json.dumps(active_corner_teams, indent=2)}

        === SECONDARY MARKET SIGNALS (SOCCERAITIPS) ===
        {json.dumps(secondary_market_data, indent=2)}

        NOTE: SoccerAiTips currently supplies BTTS / Over 2.5 markets, not
        canonical 1X2 picks. Do NOT count these signals as 1X2 consensus votes.
        They may be used as supporting secondary-market evidence when relevant.

        STRICT ARCHITECTURE RULES:
        1. NEVER repeat the same match across multiple tickets or reserve slots. Every match used (whether main or reserve) must be completely unique across your entire output.
        2. ACT AS A PORTFOLIO MANAGER: You are allowed to DROP weak consensus matches and REPLACE them with Corner predictions (e.g., 'Over 8.5 Corners') in Tickets 1, 2, or 3 if the corner data provides a mathematically safer floor.
        3. YOU MUST FORMAT YOUR HEADERS EXACTLY LIKE THIS to enforce my daily dynamic staking strategy:
            🛡️ Ticket 1: Ironclad (40% of Daily Stake)
            ⚖️ Ticket 2: Balanced (20% of Daily Stake)
            🎯 Ticket 3: Volatility (10% of Daily Stake)
            🧪 Ticket 4: Custom Tickets (30% of Daily Stake)
        4. TICKET BUILDING LOGIC:
            - TICKET 1: MUST contain EXACTLY THREE main matches sourced exclusively from the 'Core Consensus' tier with ZERO contradictions. If fewer than 3 pristine matches exist, fill remaining spots with safest Corner predictions.
            - TICKET 2: Mix any remaining 'Core Consensus' matches with Corners. Matches with contradictions can be placed here.
            - TICKET 3: Use the remaining matches and higher-risk options.
            - TICKET 4: Leave this ticket COMPLETELY BLANK under the header. Do not generate any matches for it.
        5. CRITICAL RESERVE/BACKUP RULE:
            - At the end of Tickets 1, 2, and 3 ONLY, append EXACTLY ONE additional backup match tagged as follows:
              `🔄 [RESERVE PICK]: [Match Name] ➔ [Optimized Prediction]`
            - Ticket 4 gets NO matches and NO reserve pick.
        6. IF there are only 1 or 2 matches available for the day: Output a single ticket using this exact header:
            🔥 Ticket 1: Premium Singles (100% of Daily Stake)
            • [Match Name] ➔ [Optimized Prediction]
            🔄 [RESERVE PICK]: [Match Name] ➔ [Optimized Prediction]
        7. Apply your advanced risk-mitigation optimizations DIRECTLY on the slip lines.
        8. NO paragraphs of text. NO explanations. Output ONLY the beautifully formatted tickets ready to be sent via Telegram.

        OUTPUT FORMAT TEMPLATE:
        🛡️ Ticket 1: Ironclad (40% of Daily Stake)
        • [Match Name] ➔ [Prediction]
        • [Match Name] ➔ [Prediction]
        • [Match Name] ➔ [Prediction]
        🔄 [RESERVE PICK]: [Match Name] ➔ [Prediction]

        ⚖️ Ticket 2: Balanced (20% of Daily Stake)
        • [Match Name] ➔ [Prediction]
        • [Match Name] ➔ [Prediction]
        🔄 [RESERVE PICK]: [Match Name] ➔ [Prediction]

        🎯 Ticket 3: Volatility (10% of Daily Stake)
        • [Match Name] ➔ [Prediction]
        • [Match Name] ➔ [Prediction]
        • [Match Name] ➔ [Prediction]
        🔄 [RESERVE PICK]: [Match Name] ➔ [Prediction]

        🧪 Ticket 4: Custom Tickets (30% of Daily Stake)
        """

        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        last_error = "Unknown"
        for model_name in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            for attempt in range(1, 3):
                try:
                    response = requests.post(url, json=payload, timeout=40)
                    if response.status_code == 200:
                        data = response.json()
                        self.diagnostics["AI_Handshake"] = f"🟢 Connected ({model_name})"
                        self.diagnostics["AI_Status"] = "🟢 Optimization Complete"
                        return data['candidates'][0]['content']['parts'][0]['text']
                    
                    try:
                        err_detail = response.json().get("error", {}).get("message", response.text[:100])
                    except Exception:
                        err_detail = response.text[:100]
                    last_error = f"HTTP {response.status_code} ({model_name}): {err_detail}"

                    if response.status_code in [500, 503, 429]:
                        time.sleep(2 * attempt)
                        continue
                    else:
                        break
                except Exception as e:
                    last_error = f"Network Exception: {e}"
                    time.sleep(2 * attempt)
                    continue

        self.diagnostics["AI_Status"] = f"🔴 {last_error}"
        return None

    def load_memory(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r") as f:
                    return json.load(f)
            except Exception: pass
        return {}

    def save_memory(self, memory):
        try:
            with open(MEMORY_FILE, "w") as f:
                json.dump(memory, f, indent=4)
        except Exception: pass

    def fetch_results_from_statarea(self, target_date):
        results = {}
        url = f"https://www.statarea.com/predictions/date/{target_date}/"
        try:
            r = tls_requests.get(url, impersonate="chrome124", timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.content, 'html.parser')
                for row in soup.find_all("div", class_="matchrow"):
                    text = row.get_text(separator=" ").upper()
                    padded_text = f" {text} "
                    if any(flag in padded_text for flag in [" FT ", "FINISHED", " AET ", " PEN "]):
                        home_elems = row.find_all("div", class_="name")
                        if len(home_elems) >= 2:
                            home = self.clean_team_name(home_elems[0].text)
                            away = self.clean_team_name(home_elems[1].text)
                        
                        score_match = re.search(r'\b(\d{1,2})\s*-\s*(\d{1,2})\b', text)
                        if score_match:
                            score = f"{score_match.group(1)}-{score_match.group(2)}"
                            results[f"{home} vs {away}"] = score
        except Exception: pass
        return results

    def settle_pending_tickets(self, memory):
        settled_reports = []
        needs_save = False

        dates_to_check = set()
        for date_str, payload in memory.items():
            tickets = payload if isinstance(payload, list) else payload.get("tickets", [])
            for t in tickets:
                if t.get("status") == "PENDING":
                    dates_to_check.add(date_str)

        if not dates_to_check: return []

        results_matrix = {}
        for d in dates_to_check:
            results_matrix.update(self.fetch_results_from_statarea(d))

        for date_str, payload in memory.items():
            tickets = payload if isinstance(payload, list) else payload.get("tickets", [])
            for t in tickets:
                if t.get("status") == "PENDING":
                    match_key = t["match"]
                    prediction = t["prediction"]

                    score = None
                    for res_key, res_score in results_matrix.items():
                        if res_key.lower() == match_key.lower():
                            score = res_score
                            break

                    if score:
                        try:
                            home_g, away_g = map(int, score.split("-"))
                            if home_g > away_g: actual = "1"
                            elif home_g == away_g: actual = "X"
                            else: actual = "2"

                            if prediction == actual: t["status"] = "WON 🟢"
                            else: t["status"] = "LOST 🔴"

                            t["score"] = score
                            needs_save = True

                            settled_reports.append(
                                f"• **{match_key}** ➔ **{t['status']}** (Score: {score})"
                            )
                        except Exception: pass

        if needs_save:
            self.save_memory(memory)

        return settled_reports

    def send_telegram_alert(self, msg):
        if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
            print("Telegram credentials missing; Telegram notification skipped.")
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        chunk_size = 4000
        msg_chunks = [msg[i:i+chunk_size] for i in range(0, len(msg), chunk_size)]
        
        for chunk in msg_chunks:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            try:
                r = requests.post(url, json=payload, timeout=15)
                if r.status_code != 200:
                    payload.pop("parse_mode", None)
                    r2 = requests.post(url, json=payload, timeout=15)
                    if r2.status_code != 200:
                        print(f"Telegram alert error: {r2.text}")
            except Exception as e:
                print(f"Telegram alert exception: {e}")
            time.sleep(1)

    async def run_pipeline(self):
        eat_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        today_date = eat_time.strftime('%Y-%m-%d')
        current_hour = eat_time.hour

        memory = self.load_memory()
        today_payload = memory.get(today_date)

        # STRICT LOCK LOGIC - SAVES API TOKENS
        is_already_locked = False
        if isinstance(today_payload, dict) and not FORCE_RUN:
            if today_payload.get("locked"):
                is_already_locked = True

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            self.diagnostics["Telegram"] = "🟡 NOT CONFIGURED (Secrets missing)"
        else:
            self.diagnostics["Telegram"] = "🟢 CONFIGURED"

        if SCRAPER_API_KEY:
            self.diagnostics["ScraperAPI"] = "🟢 CONFIGURED"
        else:
            self.diagnostics["ScraperAPI"] = "🟡 NOT CONFIGURED (Direct requests only)"

        if GEMINI_API_KEY:
            self.diagnostics["Gemini"] = "🟢 CONFIGURED"
        else:
            self.diagnostics["Gemini"] = "🟡 NOT CONFIGURED"

        if is_already_locked:
            print(f"🔒 Data for {today_date} is already securely locked. Bypassing scrapers to conserve ScraperAPI tokens.")
            daily_data = memory[today_date]
            agreed_matches = daily_data.get("agreed_matches", [])
            ai_optimized_message = daily_data.get("ai_optimized_message")
            req_threshold = daily_data.get("req_threshold", 3)
            self.diagnostics["Daily_Lock"] = f"🟢 CACHED (Tokens Saved for {today_date})"
        else:
            print(f"🔓 Scraping and generating fresh tickets for {today_date}...")
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                # Phase 1: run the original sources first so they establish
                # the canonical fixtures that Golsinyali can target.
                base_tasks = [
                    loop.run_in_executor(
                        pool,
                        self.fetch_and_scrape_sync,
                        n,
                        c,
                    )
                    for n, c in self.configs.items()
                    if n not in {"Golsinyali", "Expected90", "NVtips"}
                ]
                base_tasks.append(
                    loop.run_in_executor(
                        pool,
                        self.fetch_corners_sync,
                    )
                )
                await asyncio.gather(*base_tasks)

                # Phase 2: run special source adapters after the base
                # fixture matrix exists so they can target existing fixtures.
                if "Golsinyali" in self.configs:
                    await loop.run_in_executor(
                        pool,
                        self.fetch_and_scrape_sync,
                        "Golsinyali",
                        self.configs["Golsinyali"],
                    )

                if "Expected90" in self.configs:
                    await loop.run_in_executor(
                        pool,
                        self.fetch_and_scrape_sync,
                        "Expected90",
                        self.configs["Expected90"],
                    )

                if "SoccerAiTips" in self.configs:
                    await loop.run_in_executor(
                        pool,
                        self.fetch_and_scrape_sync,
                        "SoccerAiTips",
                        self.configs["SoccerAiTips"],
                    )

                if "NVtips" in self.configs:
                    await loop.run_in_executor(
                        pool,
                        self.fetch_and_scrape_sync,
                        "NVtips",
                        self.configs["NVtips"],
                    )

            agreed_matches, structured_tickets, ai_input_data, req_threshold = self.process_consensus_signals()

            active_corner_teams = []
            for match in self.master_matrix.keys():
                parts = match.split(" vs ")
                if len(parts) == 2:
                    h, a = parts[0].strip(), parts[1].strip()
                    if h in self.corner_stats and self.corner_stats[h] >= 9.5:
                        active_corner_teams.append({"match": match, "team": h, "avg_corners": self.corner_stats[h]})
                    if a in self.corner_stats and self.corner_stats[a] >= 9.5:
                        active_corner_teams.append({"match": match, "team": a, "avg_corners": self.corner_stats[a]})

            ai_optimized_message = None
            if ai_input_data or active_corner_teams or self.secondary_market_data:
                ai_optimized_message = self.ask_llm_to_optimize_tickets(
                    ai_input_data,
                    active_corner_teams,
                    self.secondary_market_data,
                )

            # Strict Lock Enforced here
            should_lock = (current_hour >= 5) and (ai_optimized_message is not None or not ai_input_data)

            memory[today_date] = {
                "locked": should_lock,
                "agreed_matches": agreed_matches,
                "ai_optimized_message": ai_optimized_message,
                "req_threshold": req_threshold,
                "tickets": structured_tickets
            }
            self.save_memory(memory)
            
            if should_lock:
                self.diagnostics["Daily_Lock"] = f"🟢 LOCKED NEW DATA FOR {today_date}"
            else:
                self.diagnostics["Daily_Lock"] = f"⏳ PREVIEW (Will Lock At 05:00 EAT) / AI RETRY PENDING"

        settled_reports = self.settle_pending_tickets(memory)

        # Always print diagnostics to GitHub Actions logs. This is important when
        # Telegram secrets are not configured yet, because otherwise scraper
        # status would only be visible through Telegram.
        print("\n==========================================")
        print("SCRAPER / ENGINE DIAGNOSTICS")
        print("==========================================")
        for site, status in self.diagnostics.items():
            print(f"↳ {site}: {status}")

        print("\n==========================================")
        print(f"MASTER MATRIX MATCHES: {len(self.master_matrix)}")
        if self.master_matrix:
            for match, listings in self.master_matrix.items():
                sources = ", ".join(
                    f"{site}={pick}" for site, pick in listings
                )
                print(f"• {match} -> {sources}")
        else:
            print("No matches entered the consensus matrix.")
        print("==========================================\n")

        print("==========================================")
        print(f"SECONDARY MARKET SIGNALS: {len(self.secondary_market_data)}")
        if self.secondary_market_data:
            for record in self.secondary_market_data:
                print(
                    f"• {record['match']} -> "
                    f"{record['market']}={record['selection']} "
                    f"({record.get('display_time') or 'time n/a'})"
                )
        else:
            print("No SoccerAiTips secondary-market signals collected.")
        print("==========================================\n")

        # Only send the Telegram alert if we actually scraped fresh data OR if we settled a ticket.
        # This prevents spamming your phone with exact duplicate tickets in the afternoon.
        if not is_already_locked or settled_reports:
            msg = f"🤝 **RAW CONSENSUS DATA ({req_threshold}+ SITES AGREEMENT)** 🤝\n\n"
            if not agreed_matches:
                msg += f"No matches found with {req_threshold}+ sites in agreement today.\n\n"
            else:
                for match in agreed_matches: msg += f"{match}\n"
                    
            if settled_reports:
                msg += "📊 **SETTLED RESULTS (Newly Finalized)** 📊\n\n"
                for rep in settled_reports: msg += f"{rep}\n"
                msg += "\n"

            msg += "⚙️ **SCRAPER STATUS** ⚙️\n"
            for site, status in self.diagnostics.items(): msg += f"↳ {site}: {status}\n"

            self.send_telegram_alert(msg)

            if ai_optimized_message and not is_already_locked:
                time.sleep(1.5)
                ticket_msg = f"🤖 **TITAN AI OPTIMIZED TICKETS** 🤖\n\n{ai_optimized_message}"
                self.send_telegram_alert(ticket_msg)

if __name__ == "__main__":
    live_configs = get_dynamic_configs()
    asyncio.run(ConsensusEngine(live_configs).run_pipeline())
