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
# CONFIGURATION & SECURE ROUTING
# ==============================================================================
TELEGRAM_TOKEN = os.environ.get("QUANT_TELEGRAM_TOKEN") or os.environ.get("TRACKER_TRACKER_TELEGRAM_TOKEN") or os.environ.get("TRACKER_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("QUANT_TELEGRAM_CHAT_ID") or os.environ.get("TRACKER_TRACKER_TELEGRAM_CHAT_ID") or os.environ.get("TRACKER_TELEGRAM_CHAT_ID")
SCRAPER_API_KEY = (os.environ.get("SCRAPER_API_KEY") or "").strip()

# KEEPING THIS TRUE TO FORCE A FRESH SWEEP
FORCE_RUN = True 

MEMORY_FILE = "pending_tickets.json"

def get_dynamic_configs():
    eat_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    today_date = eat_time.strftime('%Y-%m-%d')
    cb = int(time.time())

    return {
        "Statarea": {
            "url": f"https://www.statarea.com/predictions/date/{today_date}/",
            "fallback_url": None,
            "row_selector": "div", "row_class": "matchrow",
            "home_selector": "div", "home_class": "name", "home_index": 0,
            "away_selector": "div", "away_class": "name", "away_index": 1,
            "pick_selector": "div", "pick_class": "type1", "pick_index": 0,
            "use_scraperapi": False  
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
        "Zulubet": {  
            "url": "https://www.zulubet.com/",
            "fallback_url": "http://www.zulubet.com/",
            "row_selector": "tr", "row_class": "",
            "home_selector": "", "home_class": "", "home_index": 0,
            "away_selector": "", "away_class": "", "away_index": 0,
            "pick_selector": "", "pick_class": "", "pick_index": 0,
            "use_scraperapi": False 
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
        self.secondary_market_data = []
        self.diagnostics = {}
        self.golsinyali_session = tls_requests.Session(impersonate="chrome124")

    def check_scraperapi_balance(self):
        if not SCRAPER_API_KEY: 
            return
        try:
            r = requests.get(f"http://api.scraperapi.com/account?api_key={SCRAPER_API_KEY}", timeout=15)
            if r.status_code == 200:
                data = r.json()
                limit = data.get("requestLimit", 1)
                used = data.get("requestCount", 0)
                remaining = limit - used
                self.diagnostics["ScraperAPICredits"] = f"🟢 OK ({remaining:,} remaining)"
            else:
                self.diagnostics["ScraperAPICredits"] = "🔴 FAILED"
        except Exception:
            self.diagnostics["ScraperAPICredits"] = "🔴 OFFLINE"

    def normalize_prediction(self, raw_text):
        text = str(raw_text).strip().lower()
        if text in ["home", "home win", "h", "1"]: return "1"
        if text in ["draw", "x", "0", "d"]: return "X"
        if text in ["away", "away win", "a", "2"]: return "2"

        if len(text) > 0:
            char = text[0]
            if char in ["1", "h"]: return "1"
            if char in ["x", "0", "d"]: return "X"
            if char in ["2", "a"]: return "2"
        return None

    def clean_team_name(self, name):
        cleaned = re.sub(r'(?i)\b(match preview|preview|results?)\b', '', str(name))
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned.title()

    def is_match_active_or_played(self, row):
        try:
            text = row.get_text(separator=" ").upper()
            padded_text = f" {text} "
            status_flags = [" FT ", " HT ", "CANC", "POSTP", "FINISHED", " LIVE ", "AET ", "PEN ", "DELAYED"]
            for flag in status_flags:
                if flag in padded_text:
                    return True
        except:
            pass
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
            similarity = difflib.SequenceMatcher(None, raw_match_key.lower(), existing_key.lower()).ratio()
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
            "normalized_pick": normalized_pick,
        }

    # ==========================================================================
    # SECONDARY SCRAPERS (Golsinyali, Expected90, SoccerAiTips, NVtips)
    # ==========================================================================

    def fetch_golsinyali_sync(self):
        try:
            url = "https://www.golsinyali.com/en/predictions"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
            r = self.golsinyali_session.get(url, headers=headers, timeout=25)
            if r.status_code != 200:
                self.diagnostics["Golsinyali"] = f"🔴 FAILED (HTTP {r.status_code})"
                return

            soup = BeautifulSoup(r.content, "html.parser")
            candidates = []
            for a in soup.find_all("a", href=True):
                if "/en/match/" in a['href']:
                    full_url = f"https://www.golsinyali.com{a['href']}"
                    if full_url not in [c[0] for c in candidates]:
                        candidates.append((full_url, a.get_text(" ", strip=True)))

            candidates = candidates[:15] # Limit depth to be fast
            valid_count, matched_count, new_count = 0, 0, 0
            
            for link, anchor in candidates:
                try:
                    mr = self.golsinyali_session.get(link, headers=headers, timeout=15)
                    msoup = BeautifulSoup(mr.content, "html.parser")
                    scripts = msoup.find_all("script", type="application/ld+json")
                    sports_event = None
                    for s in scripts:
                        if s.string and "SportsEvent" in s.string:
                            try:
                                data = json.loads(s.string)
                                if isinstance(data, list):
                                    sports_event = next((d for d in data if d.get("@type") == "SportsEvent"), None)
                                elif isinstance(data, dict):
                                    if data.get("@type") == "SportsEvent": sports_event = data
                                    elif "@graph" in data:
                                        sports_event = next((g for g in data["@graph"] if g.get("@type") == "SportsEvent"), None)
                            except: pass
                        if sports_event: break

                    if sports_event:
                        h_team = sports_event.get("homeTeam", {}).get("name")
                        a_team = sports_event.get("awayTeam", {}).get("name")
                        desc = str(sports_event.get("description", "")).lower()
                        
                        pick = None
                        if "home win" in desc or "ms1" in desc or "pick:1" in desc.replace(" ",""): pick = "1"
                        elif "away win" in desc or "ms2" in desc or "pick:2" in desc.replace(" ",""): pick = "2"
                        elif "draw" in desc or "msx" in desc or "pick:x" in desc.replace(" ",""): pick = "X"
                        
                        if not pick and "balanced match" in desc:
                            probs = re.search(r"\((\d+(?:\.\d+)?)%\s*-\s*(\d+(?:\.\d+)?)%\s*-\s*(\d+(?:\.\d+)?)%\)", desc)
                            if probs:
                                vals = {"1": float(probs.group(1)), "X": float(probs.group(2)), "2": float(probs.group(3))}
                                pick = max(vals, key=vals.get)

                        if h_team and a_team and pick:
                            res = self.log_prediction_qa("Golsinyali", h_team, a_team, pick)
                            if res:
                                valid_count += 1
                                if res["matched_existing_fixture"]: matched_count += 1
                                else: new_count += 1
                except:
                    continue

            if valid_count > 0:
                self.diagnostics["Golsinyali"] = f"🟢 OK ({valid_count} Today | {matched_count} Matched | {new_count} New)"
            else:
                self.diagnostics["Golsinyali"] = "🟡 NO PREDICTIONS"
        except Exception as e:
            self.diagnostics["Golsinyali"] = f"🔴 FAILED ({type(e).__name__})"

    def fetch_expected90_sync(self):
        try:
            url = "https://www.90predict.com/football-predictions-today"
            r = tls_requests.get(url, impersonate="chrome124", timeout=25)
            if r.status_code != 200:
                self.diagnostics["Expected90"] = f"🔴 FAILED (HTTP {r.status_code})"
                return

            soup = BeautifulSoup(r.content, "html.parser")
            links = []
            for a in soup.find_all("a", href=True):
                if "-vs-" in a['href']:
                    href = a['href']
                    full_url = href if href.startswith("http") else f"https://www.90predict.com{href if href.startswith('/') else '/' + href}"
                    if full_url not in links: links.append(full_url)

            links = links[:15]
            valid_count, matched_count, new_count = 0, 0, 0

            for link in links:
                try:
                    mr = tls_requests.get(link, impersonate="chrome124", timeout=15)
                    msoup = BeautifulSoup(mr.content, "html.parser")
                    scripts = msoup.find_all("script", type="application/ld+json")
                    sports_event = None
                    for s in scripts:
                        if s.string and "SportsEvent" in s.string:
                            try:
                                data = json.loads(s.string)
                                if isinstance(data, list): sports_event = next((d for d in data if d.get("@type") == "SportsEvent"), None)
                                elif isinstance(data, dict):
                                    if data.get("@type") == "SportsEvent": sports_event = data
                                    elif "@graph" in data: sports_event = next((g for g in data["@graph"] if g.get("@type") == "SportsEvent"), None)
                            except: pass
                        if sports_event: break

                    if sports_event:
                        h_team = sports_event.get("homeTeam", {}).get("name")
                        a_team = sports_event.get("awayTeam", {}).get("name")
                        desc = str(sports_event.get("description", ""))
                        
                        pattern = re.compile(r"(?P<home>[A-Za-z][^,%]*?)\s+(?P<home_pct>\d+(?:\.\d+)?)%\s*,\s*draw\s+(?P<draw_pct>\d+(?:\.\d+)?)%\s*,\s*(?P<away>[A-Za-z][^,%]*?)\s+(?P<away_pct>\d+(?:\.\d+)?)%", re.IGNORECASE)
                        match = pattern.search(desc)
                        if match and h_team and a_team:
                            vals = {"1": float(match.group("home_pct")), "X": float(match.group("draw_pct")), "2": float(match.group("away_pct"))}
                            pick = max(vals, key=vals.get)
                            res = self.log_prediction_qa("Expected90", h_team, a_team, pick)
                            if res:
                                valid_count += 1
                                if res["matched_existing_fixture"]: matched_count += 1
                                else: new_count += 1
                except:
                    continue
            
            if valid_count > 0:
                self.diagnostics["Expected90"] = f"🟢 OK ({valid_count} Today | {matched_count} Matched | {new_count} New)"
            else:
                self.diagnostics["Expected90"] = "🟡 NO PREDICTIONS"
        except Exception as e:
            self.diagnostics["Expected90"] = f"🔴 FAILED ({type(e).__name__})"

    def fetch_nvtips_sync(self):
        try:
            today = datetime.datetime.utcnow()
            url = f"https://nvtips.com/?d={today.day}&m={today.month}&y={today.year}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code != 200:
                self.diagnostics["NVtips"] = f"🔴 FAILED (HTTP {r.status_code})"
                return

            soup = BeautifulSoup(r.content, "html.parser")
            rows = soup.select("div.nv-row")
            valid_count, matched_count, new_count = 0, 0, 0

            for row in rows:
                try:
                    team_nodes = row.select(".nv-team-name")
                    teams = [re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() for node in team_nodes]
                    if len(teams) < 2: continue
                    home, away = teams[0], teams[1]

                    data_search = row.get("data-search", "").lower()
                    score_pred = re.search(r"\b([1x2])\s+(\d+)\s*-\s*(\d+)\b", data_search, re.IGNORECASE)
                    pick = score_pred.group(1).upper() if score_pred else None

                    if not pick:
                        row_text = row.get_text(" ", strip=True)
                        vis_match = re.search(r"\b([1x2])\b\s+\d+\s*-\s*\d+\b", row_text, re.IGNORECASE)
                        if vis_match: pick = vis_match.group(1).upper()

                    if home and away and pick:
                        res = self.log_prediction_qa("NVtips", home, away, pick)
                        if res:
                            valid_count += 1
                            if res["matched_existing_fixture"]: matched_count += 1
                            else: new_count += 1
                except:
                    continue
            
            if valid_count > 0:
                self.diagnostics["NVtips"] = f"🟢 OK ({valid_count} Today | {matched_count} Matched | {new_count} New)"
            else:
                self.diagnostics["NVtips"] = "🟡 NO PREDICTIONS"
        except Exception as e:
            self.diagnostics["NVtips"] = f"🔴 FAILED ({type(e).__name__})"

    def fetch_socceraitips_sync(self):
        try:
            url = "https://www.socceraitips.com/api/daily-parlay"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
            r = requests.get(url, params={"locale": "en"}, headers=headers, timeout=25)
            if r.status_code != 200:
                self.diagnostics["SoccerAiTips"] = f"🔴 FAILED (HTTP {r.status_code})"
                return
            
            payload = r.json()
            matches = payload.get("data", {}).get("matches", [])
            valid_count, matched_count = 0, 0

            for match in matches:
                try:
                    home = match.get("home_team")
                    away = match.get("away_team")
                    bet_type = str(match.get("bet_type", "")).lower()
                    pred_display = str(match.get("prediction_display", "")).upper()
                    
                    market, selection = None, None
                    if bet_type == "over_2_5" and pred_display == "OVER 2.5":
                        market, selection = "OVER_2.5", "OVER_2.5"
                    elif bet_type in ["kg_var", "btts"] and pred_display == "BTTS":
                        market, selection = "BTTS", "BTTS_YES"

                    if market and home and away:
                        raw_key = f"{self.clean_team_name(home)} vs {self.clean_team_name(away)}"
                        best_key, matched = raw_key, False
                        for ek in self.master_matrix.keys():
                            if difflib.SequenceMatcher(None, raw_key.lower(), ek.lower()).ratio() > 0.75:
                                best_key, matched = ek, True
                                break
                        
                        self.secondary_market_data.append({
                            "match": best_key, "market": market, "selection": selection
                        })
                        valid_count += 1
                        if matched: matched_count += 1
                except:
                    continue

            if valid_count > 0:
                self.diagnostics["SoccerAiTips"] = f"🟢 OK ({valid_count} Secondary | {matched_count} Matched)"
            else:
                self.diagnostics["SoccerAiTips"] = "🟡 NO SECONDARY MARKETS"
        except Exception as e:
            self.diagnostics["SoccerAiTips"] = f"🔴 FAILED ({type(e).__name__})"

    # ==========================================================================
    # CORE ENGINE SCRAPERS
    # ==========================================================================

    def fetch_corners_sync(self):
        url = "https://www.totalcorner.com/match/today"
        for attempt in range(1, 4):
            try:
                r = tls_requests.get(url, impersonate="chrome124", timeout=25)
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
                                        
                    self.diagnostics["CornersEngine"] = f"🟢 OK ({valid_corners} High-Corner Teams)"
                    return
                elif r.status_code in [403, 500, 502, 503, 504, 429]:
                    time.sleep(2 * attempt)
                    continue
                else:
                    self.diagnostics["CornersEngine"] = f"🔴 FAILED (HTTP {r.status_code})"
                    return
            except Exception:
                time.sleep(2 * attempt)
                continue

        self.diagnostics["CornersEngine"] = "🔴 TIMEOUT/ERROR"

    def fetch_and_scrape_sync(self, site_name, cfg):
        max_attempts = 4
        req_timeout = 90 
        last_status = "TIMEOUT"
        target_url = cfg["url"]

        for attempt in range(1, max_attempts + 1):
            try:
                active_url = cfg.get("fallback_url") if (attempt >= 3 and cfg.get("fallback_url")) else target_url
                r = None

                # EXACT TITAN 1 WATERFALL (With ScraperAPI)
                if site_name == "WinDrawWin":
                    if attempt == 1 and SCRAPER_API_KEY:
                        r = requests.get("http://api.scraperapi.com/", params={"api_key": SCRAPER_API_KEY, "url": active_url, "premium": "true", "country_code": "uk"}, timeout=req_timeout)
                    elif attempt == 2 and SCRAPER_API_KEY:
                        r = requests.get("http://api.scraperapi.com/", params={"api_key": SCRAPER_API_KEY, "url": active_url, "premium": "true", "country_code": "us", "antibot": "true"}, timeout=req_timeout)
                    elif attempt == 3:
                        r = tls_requests.get(active_url, impersonate="chrome124", timeout=req_timeout)
                    elif attempt == 4 and SCRAPER_API_KEY:
                        r = requests.get("http://api.scraperapi.com/", params={"api_key": SCRAPER_API_KEY, "url": active_url, "premium": "true"}, timeout=req_timeout)
                elif site_name == "SoccerVista":
                    if attempt <= 3 and SCRAPER_API_KEY:
                        r = requests.get("http://api.scraperapi.com/", params={"api_key": SCRAPER_API_KEY, "url": active_url, "premium": "true", "render": "true"}, timeout=req_timeout)
                    else:
                        r = tls_requests.get(active_url, impersonate="chrome124", timeout=req_timeout)
                else:
                    use_proxy = cfg.get("use_scraperapi") and bool(SCRAPER_API_KEY)
                    if use_proxy:
                        r = requests.get("http://api.scraperapi.com/", params={"api_key": SCRAPER_API_KEY, "url": active_url, "premium": "true"}, timeout=req_timeout)
                    else:
                        r = tls_requests.get(active_url, impersonate="safari17_0", timeout=30)

                last_status = r.status_code if r else "TIMEOUT"

                if r and r.status_code == 200:
                    challenge_phrases = ["just a moment", "cf-browser-verification", "checking your browser", "turnstile", "ray id", "security check", "verify you are human", "cloudflare", "ddos protection"]
                    
                    if any(phrase in r.text.lower() for phrase in challenge_phrases) and len(r.text) < 150000:
                        if attempt < max_attempts:
                            time.sleep(2 * attempt)
                            continue
                        self.diagnostics[site_name] = "🟡 BLOCKED (Cloudflare Challenge)"
                        return

                    soup = BeautifulSoup(r.content, 'html.parser')
                    rows = []
                    
                    if site_name == "WinDrawWin":
                        rows = soup.find_all("div", class_=re.compile(r"(wttr|wtrow|match-row|pr-match)", re.I))
                        if not rows:
                            rows = soup.find_all("tr")
                    elif site_name in ["SoccerVista", "Zulubet"]:
                        rows = soup.find_all("tr")
                        if not rows or len(rows) < 5:
                            raw_rows = soup.find_all("div", class_=re.compile(r'(predict|match|row|fixture|item)', re.I))
                            rows = [rw for rw in raw_rows if len(rw.find_all('a')) >= 2 or len(rw.find_all('div')) >= 2]
                    elif site_name == "Statarea":
                        rows = soup.find_all("div", class_="matchrow")
                    elif site_name == "Vitibet":
                        rows = soup.find_all("a", class_="livescore-match-row")

                    if not rows:
                        if attempt < max_attempts:
                            time.sleep(2 * attempt)
                            continue
                        self.diagnostics[site_name] = f"🟡 BLOCKED/EMPTY (0 parsed)"
                        return

                    valid_count = 0
                    skipped_count = 0

                    for row in rows:
                        try:
                            if self.is_match_active_or_played(row):
                                skipped_count += 1
                                continue

                            home, away, pick = None, None, None

                            if site_name == "WinDrawWin":
                                h_elem = row.find(class_=re.compile(r'(wttmobh|team1|h$|home)', re.I))
                                a_elem = row.find(class_=re.compile(r'(wttmoba|team2|a$|away)', re.I))
                                p_elem = row.find(class_=re.compile(r'(wtoddsdesc|mobpred|prd|pred|pick|tip|prediction)', re.I))

                                if h_elem and a_elem:
                                    home, away = h_elem.text, a_elem.text
                                    if p_elem: pick = p_elem.text
                                    
                                if not home or not away:
                                    links = row.find_all("a")
                                    if len(links) >= 2:
                                        home, away = links[0].text.strip(), links[1].text.strip()
                                        p_div = row.find(class_=re.compile(r'(prd|pred|odds)', re.I))
                                        if p_div and self.normalize_prediction(p_div.text): pick = p_div.text
                                        else:
                                            for text_chunk in row.stripped_strings:
                                                if text_chunk.strip().upper() in ["1", "X", "2", "HOME", "DRAW", "AWAY"]:
                                                    pick = text_chunk.strip()
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
                                            
                                if not home or not away:
                                    text_chunks = [t.strip() for t in row.stripped_strings if t.strip()]
                                    for chunk in text_chunks:
                                        if " v " in chunk.lower() or " vs " in chunk.lower():
                                            parts = re.split(r'(?i)\s+v\s+|\s+vs\s+', chunk, maxsplit=1)
                                            if len(parts) == 2:
                                                home, away = parts[0].strip(), parts[1].strip()
                                        elif chunk in ["1", "X", "2", "1X", "X2", "12"]:
                                            pick = chunk

                            elif site_name == "Zulubet":
                                text_chunks = [t.strip() for t in row.stripped_strings if t.strip()]
                                for chunk in text_chunks:
                                    if (" - " in chunk or " vs " in chunk.lower()) and len(chunk) > 5 and not re.search(r'\d+:\d+', chunk):
                                        parts = re.split(r'\s+-\s+|\s+(?i)vs\s+', chunk, maxsplit=1)
                                        if len(parts) == 2:
                                            home, away = parts[0].strip(), parts[1].strip()
                                    elif chunk.upper() in ["1", "X", "2", "1X", "X2", "12"]:
                                        pick = chunk.upper()
                                        
                            elif site_name == "Statarea":
                                home_elems = row.find_all("div", class_="name")
                                if len(home_elems) >= 2:
                                    home, away = home_elems[0].text, home_elems[1].text
                                pick_elem = row.find("div", class_="type1")
                                if pick_elem: pick = pick_elem.text
                                
                            elif site_name == "Vitibet":
                                home_elems = row.find_all("span", class_="livescore-team-name")
                                if len(home_elems) >= 2:
                                    home, away = home_elems[0].text, home_elems[1].text
                                pick_elem = row.find("span", class_="tip-indicator-circle")
                                if pick_elem: pick = pick_elem.text

                            home_str = self.clean_team_name(home) if home else ""
                            away_str = self.clean_team_name(away) if away else ""

                            if not home_str and away_str and re.search(r'(?i)\s+vs?\s+', away_str):
                                parts = re.split(r'(?i)\s+vs?\s+', away_str, maxsplit=1)
                                home_str, away_str = self.clean_team_name(parts[0]), self.clean_team_name(parts[1])

                            if not away_str and home_str and re.search(r'(?i)\s+vs?\s+', home_str):
                                parts = re.split(r'(?i)\s+vs?\s+', home_str, maxsplit=1)
                                home_str, away_str = self.clean_team_name(parts[0]), self.clean_team_name(parts[1])

                            if home_str and away_str and pick:
                                self.log_prediction_qa(site_name, home_str, away_str, pick)
                                valid_count += 1

                        except Exception:
                            continue

                    if valid_count > 0 or skipped_count > 0:
                        self.diagnostics[site_name] = f"🟢 OK ({valid_count} Upcoming | {skipped_count} Played)"
                        return

                time.sleep(2 * attempt)
            except Exception:
                time.sleep(2 * attempt)

        self.diagnostics[site_name] = f"🔴 FAILED (HTTP {last_status})"

    def process_consensus_signals(self):
        core_matches = []
        fallback_matches = []
        structured_tickets = []
        core_data = []
        fallback_data = []

        all_scrapers = ["Statarea", "Vitibet", "Zulubet", "WinDrawWin", "SoccerVista", "Golsinyali", "Expected90", "NVtips"]
        required_consensus = 4 
        fallback_consensus = 3

        for match, listings in self.master_matrix.items():
            prediction_weights = {}
            sites_backing = {}
            for site, pick in listings:
                prediction_weights[pick] = prediction_weights.get(pick, 0) + 1
                sites_backing.setdefault(pick, []).append(site)

            if not prediction_weights:
                continue

            top_pick = max(prediction_weights, key=prediction_weights.get)
            top_count = prediction_weights[top_pick]
            
            if top_count >= fallback_consensus:
                backing_sites_list = sites_backing[top_pick]
                backing_sites_str = " + ".join(backing_sites_list)
                contradictions = []

                match_text = f"• **{match}** ➔ {top_pick}\n  ↳ ✅ Backed by: `{backing_sites_str}`\n"

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

                record = {
                    "match": match, 
                    "consensus_pick": top_pick, 
                    "agreement_count": len(backing_sites_list),
                    "contradictions": contradictions
                }

                if top_count >= required_consensus:
                    core_matches.append(match_text)
                    core_data.append(record)
                    structured_tickets.append({"match": match, "prediction": top_pick, "status": "PENDING", "score": "-"})
                else:
                    fallback_matches.append(match_text)
                    fallback_data.append(record)

        return core_matches, fallback_matches, structured_tickets, core_data, fallback_data

    def build_algorithmic_ticket(self, core_data, fallback_data, active_corner_teams):
        self.diagnostics["QuantEngine"] = "🟢 2-Ticket Engine Generated"
        
        combined_data = core_data + fallback_data

        def get_score(match_data):
            return (match_data.get("agreement_count", 0), -len(match_data.get("contradictions", [])))

        sorted_matches = sorted(combined_data, key=get_score, reverse=True)
        
        main_candidates = [m for m in sorted_matches if m['consensus_pick'] in ["1", "2"]]
        draw_candidates = [m for m in sorted_matches if m['consensus_pick'] == "X"]
        
        final_main_picks = []
        for m in main_candidates:
            pick_str = f"{m['match']} ➔ {m['consensus_pick']}"
            if pick_str not in final_main_picks:
                final_main_picks.append(pick_str)
            if len(final_main_picks) == 6:
                break
                
        if len(final_main_picks) < 6:
            for corner in active_corner_teams:
                corner_pick = f"{corner['match']} ➔ Over 8.5 Corners (Avg: {corner['avg_corners']})"
                if corner_pick not in final_main_picks:
                    final_main_picks.append(corner_pick)
                if len(final_main_picks) == 6:
                    break
        
        reserve_picks = []
        for d in draw_candidates:
            pick_str = f"{d['match']} ➔ {d['consensus_pick']}"
            if pick_str not in reserve_picks and pick_str not in final_main_picks:
                reserve_picks.append(pick_str)
            if len(reserve_picks) == 2:
                break
                
        if len(reserve_picks) < 2:
            leftovers = [f"{m['match']} ➔ {m['consensus_pick']}" for m in main_candidates]
            for pick_str in leftovers:
                if pick_str not in final_main_picks and pick_str not in reserve_picks:
                    reserve_picks.append(pick_str)
                if len(reserve_picks) == 2:
                    break
                    
        if len(reserve_picks) < 2:
            for corner in active_corner_teams:
                corner_pick = f"{corner['match']} ➔ Over 8.5 Corners (Avg: {corner['avg_corners']})"
                if corner_pick not in final_main_picks and corner_pick not in reserve_picks:
                    reserve_picks.append(corner_pick)
                if len(reserve_picks) == 2:
                    break

        if len(final_main_picks) < 3:
            return "No high-conviction matches found today to safely build tickets."
            
        ticket1_mains = final_main_picks[:3]
        ticket2_mains = final_main_picks[3:6]
        
        reserve1 = reserve_picks[0] if len(reserve_picks) > 0 else None
        reserve2 = reserve_picks[1] if len(reserve_picks) > 1 else None
        
        ticket_text = "🤖 **TITAN ALGORITHMIC TICKETS** 🤖\n\n"
        
        ticket_text += "🛡️ **Ticket 1: Premium Slip (50% of Daily Stake)**\n"
        for pick in ticket1_mains: ticket_text += f"• {pick}\n"
        if reserve1: ticket_text += f"🔄 [RESERVE PICK]: {reserve1}\n"
            
        if ticket2_mains:
            ticket_text += "\n🛡️ **Ticket 2: Premium Slip (50% of Daily Stake)**\n"
            for pick in ticket2_mains: ticket_text += f"• {pick}\n"
            if reserve2: ticket_text += f"🔄 [RESERVE PICK]: {reserve2}\n"
                
        return ticket_text

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
                            settled_reports.append(f"• **{match_key}** ➔ **{t['status']}** (Score: {score})")
                        except Exception: pass

        if needs_save: self.save_memory(memory)
        return settled_reports

    def send_telegram_alert(self, msg):
        if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
            print("Telegram credentials missing.")
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
            except Exception as e:
                print(f"Telegram alert exception: {e}")
            time.sleep(1)

    async def run_pipeline(self):
        eat_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        today_date = eat_time.strftime('%Y-%m-%d')
        current_hour = eat_time.hour

        memory = self.load_memory()
        today_payload = memory.get(today_date)

        is_already_locked = False
        if isinstance(today_payload, dict) and not FORCE_RUN:
            if today_payload.get("locked"):
                is_already_locked = True

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: self.diagnostics["Telegram"] = "🟡 NOT CONFIGURED"
        else: self.diagnostics["Telegram"] = "🟢 CONFIGURED"

        if is_already_locked:
            print(f"🔒 Data for {today_date} is securely locked. Bypassing scrapers.")
            daily_data = memory[today_date]
            agreed_matches = daily_data.get("core_matches_4plus") or daily_data.get("agreed_matches", [])
            algorithmic_message = daily_data.get("ai_optimized_message")
            self.diagnostics["DailyLock"] = f"🟢 CACHED (Tokens Saved for {today_date})"
        else:
            print(f"🔓 Scraping and generating fresh Algorithmic Tickets for {today_date}...")
            
            self.check_scraperapi_balance()
            
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                base_tasks = [
                    loop.run_in_executor(pool, self.fetch_and_scrape_sync, n, c)
                    for n, c in self.configs.items()
                ]
                base_tasks.append(loop.run_in_executor(pool, self.fetch_corners_sync))
                base_tasks.append(loop.run_in_executor(pool, self.fetch_golsinyali_sync))
                base_tasks.append(loop.run_in_executor(pool, self.fetch_expected90_sync))
                base_tasks.append(loop.run_in_executor(pool, self.fetch_socceraitips_sync))
                base_tasks.append(loop.run_in_executor(pool, self.fetch_nvtips_sync))
                
                await asyncio.gather(*base_tasks)

            (
                core_matches,
                fallback_matches,
                structured_tickets,
                core_ai_input_data,
                fallback_ai_input_data,
                req_threshold,
                fallback_threshold,
                fallback_active,
            ) = self.process_consensus_signals()

            agreed_matches = core_matches if core_matches else fallback_matches

            active_corner_teams = []
            for match in self.master_matrix.keys():
                parts = match.split(" vs ")
                if len(parts) == 2:
                    h, a = parts[0].strip(), parts[1].strip()
                    if h in self.corner_stats and self.corner_stats[h] >= 9.5:
                        active_corner_teams.append({"match": match, "team": h, "avg_corners": self.corner_stats[h]})
                    if a in self.corner_stats and self.corner_stats[a] >= 9.5:
                        active_corner_teams.append({"match": match, "team": a, "avg_corners": self.corner_stats[a]})

            algorithmic_message = None
            if core_ai_input_data or fallback_ai_input_data or active_corner_teams:
                algorithmic_message = self.build_algorithmic_ticket(core_ai_input_data, fallback_ai_input_data, active_corner_teams)

            should_lock = (current_hour >= 5) and (algorithmic_message is not None or not (core_ai_input_data or fallback_ai_input_data))

            memory[today_date] = {
                "locked": should_lock,
                "agreed_matches": agreed_matches,
                "ai_optimized_message": algorithmic_message,
                "tickets": structured_tickets
            }
            
            if not FORCE_RUN: self.save_memory(memory)
            
            if should_lock: self.diagnostics["DailyLock"] = f"🟢 LOCKED NEW DATA FOR {today_date}"
            else: self.diagnostics["DailyLock"] = f"⏳ PREVIEW (Will Lock At 05:00 EAT)"

        settled_reports = self.settle_pending_tickets(memory)

        if not is_already_locked or settled_reports:
            msg = "🤝 **RAW CONSENSUS DATA** 🤝\n\n"
            if agreed_matches:
                for match in agreed_matches:
                    msg += f"{match}\n"
            else:
                msg += "No matches found with required agreement today.\n\n"

            if settled_reports:
                msg += "📊 **SETTLED RESULTS** 📊\n\n"
                for rep in settled_reports: msg += f"{rep}\n"
                msg += "\n"

            msg += "⚙️️ **SCRAPER STATUS** ⚙️\n"
            essential_keys = [
                "Telegram", "ScraperAPICredits", "Statarea", "Vitibet", 
                "Zulubet", "WinDrawWin", "SoccerVista", "Golsinyali", 
                "Expected90", "SoccerAiTips", "NVtips", "CornersEngine", 
                "QuantEngine", "DailyLock"
            ]
            for k in essential_keys:
                if k in self.diagnostics:
                    msg += f"↳ {k}: {self.diagnostics[k]}\n"
            
            self.send_telegram_alert(msg)

            if algorithmic_message and not is_already_locked:
                time.sleep(1.5)
                self.send_telegram_alert(algorithmic_message)

if __name__ == "__main__":
    live_configs = get_dynamic_configs()
    asyncio.run(ConsensusEngine(live_configs).run_pipeline())
