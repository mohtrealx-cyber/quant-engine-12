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
        if not home or not away or not raw_prediction: return
        normalized_pick = self.normalize_prediction(raw_prediction)
        if not normalized_pick: return

        raw_match_key = f"{self.clean_team_name(home)} vs {self.clean_team_name(away)}"
        final_key = raw_match_key

        for existing_key in self.master_matrix.keys():
            similarity = difflib.SequenceMatcher(None, raw_match_key.lower(), existing_key.lower()).ratio()
            if similarity >= 0.75: 
                final_key = existing_key
                break

        if final_key not in self.master_matrix: self.master_matrix[final_key] = []

        existing_sites = [entry[0] for entry in self.master_matrix[final_key]]
        if site_name not in existing_sites:
            self.master_matrix[final_key].append((site_name, normalized_pick))

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

    def fetch_and_scrape_sync(self, site_name, cfg):
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

        all_scrapers = ["Statarea", "Vitibet", "PredictZ", "WinDrawWin", "SoccerVista"]
        
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

    def ask_llm_to_optimize_tickets(self, ai_input_data, active_corner_teams):
        api_key = (GEMINI_API_KEY or "").strip()
        if not api_key:
            self.diagnostics["AI_Status"] = "🔴 Missing GEMINI_API_KEY"
            return None

        models_to_try = self.get_available_gemini_models(api_key)

        prompt = f"""
        You are Titan, an elite quantitative sports betting AI Portfolio Manager.
        Your objective is to analyze the following raw consensus data and corner statistics, 
        and construct highly optimized, risk-mitigated betting tickets.

        === RAW CONSENSUS DATA ===
        {json.dumps(ai_input_data, indent=2)}

        === HIGH-PROBABILITY CORNER STATISTICS ===
        {json.dumps(active_corner_teams, indent=2)}

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
                tasks = [loop.run_in_executor(pool, self.fetch_and_scrape_sync, n, c) for n, c in self.configs.items()]
                tasks.append(loop.run_in_executor(pool, self.fetch_corners_sync))
                await asyncio.gather(*tasks)

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
            if ai_input_data or active_corner_teams:
                ai_optimized_message = self.ask_llm_to_optimize_tickets(ai_input_data, active_corner_teams)

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
