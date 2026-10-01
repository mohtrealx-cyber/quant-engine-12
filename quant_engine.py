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

# KEEPING THIS TRUE FOR THE TEST RUN
FORCE_RUN = True 

MEMORY_FILE = "pending_tickets.json"

def get_dynamic_configs():
    eat_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    today_date = eat_time.strftime('%Y-%m-%d')
    cb = int(time.time())

    return {
        "Statarea": {"url": f"https://www.statarea.com/predictions/date/{today_date}/"},
        "Vitibet": {"url": f"https://www.vitibet.com/index.php?clanek=quicktips&sekce=fotbal&lang=en&cb={cb}"},
        "Zulubet": {"url": "https://www.zulubet.com/"},
        "BetClan": {"url": "https://www.betclan.com/todays-football-predictions/"}
    }

class ConsensusEngine:
    def __init__(self, configs):
        self.configs = configs
        self.master_matrix = {}
        self.corner_stats = {}
        self.diagnostics = {}

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
            
        # BetClan uses full team names for predictions, so if the text is longer than 2 characters, 
        # it will be handled by the QA logger which checks if it matches home or away team.
        return raw_text 

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

        # Custom normalization for BetClan which outputs the winning team's name
        normalized_pick = self.normalize_prediction(raw_prediction)
        
        # If the raw prediction is a string longer than 2 chars, check if it matches home/away
        if normalized_pick and len(normalized_pick) > 2:
            if normalized_pick.lower() in home.lower() or home.lower() in normalized_pick.lower():
                normalized_pick = "1"
            elif normalized_pick.lower() in away.lower() or away.lower() in normalized_pick.lower():
                normalized_pick = "2"
            elif "draw" in normalized_pick.lower():
                normalized_pick = "X"
            else:
                return None # Could not resolve text prediction

        if not normalized_pick or normalized_pick not in ["1", "X", "2"]:
            return None

        raw_match_key = f"{self.clean_team_name(home)} vs {self.clean_team_name(away)}"
        final_key = raw_match_key

        for existing_key in self.master_matrix.keys():
            similarity = difflib.SequenceMatcher(None, raw_match_key.lower(), existing_key.lower()).ratio()
            if similarity >= 0.75:
                final_key = existing_key
                break

        if final_key not in self.master_matrix:
            self.master_matrix[final_key] = []

        existing_sites = [entry[0] for entry in self.master_matrix[final_key]]
        if site_name not in existing_sites:
            self.master_matrix[final_key].append((site_name, normalized_pick))

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
        req_timeout = 30 
        last_status = "TIMEOUT"
        target_url = cfg["url"]

        # Rotating TLS Arsenal to pierce Cloudflare completely independent of ScraperAPI
        tls_profiles = ["chrome124", "safari15_3", "chrome120", "safari17_0"]

        for attempt in range(1, max_attempts + 1):
            try:
                current_profile = tls_profiles[(attempt - 1) % len(tls_profiles)]
                r = tls_requests.get(target_url, impersonate=current_profile, timeout=req_timeout)
                last_status = r.status_code

                if r.status_code == 200:
                    challenge_phrases = [
                        "just a moment", "cf-browser-verification", "checking your browser", 
                        "turnstile", "ray id", "security check", "verify you are human", 
                        "cloudflare", "ddos protection"
                    ]
                    
                    if any(phrase in r.text.lower() for phrase in challenge_phrases) and len(r.text) < 150000:
                        if attempt < max_attempts:
                            time.sleep(2 * attempt)
                            continue
                        self.diagnostics[site_name] = "🟡 BLOCKED (Cloudflare Challenge)"
                        return

                    soup = BeautifulSoup(r.content, 'html.parser')
                    rows = []
                    
                    if site_name in ["Zulubet", "BetClan"]:
                        rows = soup.find_all("tr")
                        if not rows or len(rows) < 5:
                            raw_rows = soup.find_all("div", class_=re.compile(r'(predict|match|row|fixture|item)', re.I))
                            rows = [row for row in raw_rows if len(row.find_all('a')) >= 2 or len(row.find_all('div')) >= 2]
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

                            if site_name == "BetClan":
                                tds = row.find_all("td")
                                if len(tds) >= 4:
                                    home = tds[1].text.strip()
                                    away = tds[2].text.strip()
                                    pick = tds[3].text.strip()
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
                                    home = home_elems[0].text
                                    away = home_elems[1].text
                                pick_elem = row.find("div", class_="type1")
                                if pick_elem: pick = pick_elem.text
                            elif site_name == "Vitibet":
                                home_elems = row.find_all("span", class_="livescore-team-name")
                                if len(home_elems) >= 2:
                                    home = home_elems[0].text
                                    away = home_elems[1].text
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
        agreed_matches = []
        structured_tickets = []
        ai_input_data = []

        all_scrapers = ["Statarea", "Vitibet", "Zulubet", "BetClan"]
        required_consensus = 3 

        for match, listings in self.master_matrix.items():
            prediction_weights = {}
            sites_backing = {}
            for site, pick in listings:
                prediction_weights[pick] = prediction_weights.get(pick, 0) + 1
                sites_backing.setdefault(pick, []).append(site)

            if not prediction_weights:
                continue

            top_pick = max(prediction_weights, key=prediction_weights.get)
            
            if prediction_weights[top_pick] >= required_consensus:
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

                agreed_matches.append(match_text)
                structured_tickets.append({"match": match, "prediction": top_pick, "status": "PENDING", "score": "-"})
                
                ai_input_data.append({
                    "match": match, 
                    "consensus_pick": top_pick, 
                    "agreement_count": len(backing_sites_list),
                    "contradictions": contradictions
                })

        return agreed_matches, structured_tickets, ai_input_data, required_consensus

    def build_algorithmic_ticket(self, data, active_corner_teams):
        self.diagnostics["QuantEngine"] = "🟢 2-Ticket Engine Generated"
        
        def get_score(match_data):
            backing_count = match_data.get("agreement_count", 0)
            contradiction_count = len(match_data.get("contradictions", []))
            return (backing_count, -contradiction_count)

        sorted_matches = sorted(data, key=get_score, reverse=True)
        
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

        is_already_locked = False
        if isinstance(today_payload, dict) and not FORCE_RUN:
            if today_payload.get("locked"):
                is_already_locked = True

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: self.diagnostics["Telegram"] = "🟡 NOT CONFIGURED"
        else: self.diagnostics["Telegram"] = "🟢 CONFIGURED"

        if is_already_locked:
            print(f"🔒 Data for {today_date} is securely locked. Bypassing scrapers.")
            daily_data = memory[today_date]
            agreed_matches = daily_data.get("agreed_matches", [])
            algorithmic_message = daily_data.get("ai_optimized_message")
            req_threshold = daily_data.get("req_threshold", 3)
            self.diagnostics["DailyLock"] = f"🟢 CACHED (Tokens Saved for {today_date})"
        else:
            print(f"🔓 Scraping and generating fresh Algorithmic Tickets for {today_date}...")
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                base_tasks = [
                    loop.run_in_executor(pool, self.fetch_and_scrape_sync, n, c)
                    for n, c in self.configs.items()
                ]
                base_tasks.append(loop.run_in_executor(pool, self.fetch_corners_sync))
                await asyncio.gather(*base_tasks)

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

            algorithmic_message = None
            if ai_input_data or active_corner_teams:
                algorithmic_message = self.build_algorithmic_ticket(ai_input_data, active_corner_teams)

            should_lock = (current_hour >= 5) and (algorithmic_message is not None or not ai_input_data)

            memory[today_date] = {
                "locked": should_lock,
                "agreed_matches": agreed_matches,
                "ai_optimized_message": algorithmic_message,
                "req_threshold": req_threshold,
                "tickets": structured_tickets
            }
            
            if not FORCE_RUN: self.save_memory(memory)
            
            if should_lock: self.diagnostics["DailyLock"] = f"🟢 LOCKED NEW DATA FOR {today_date}"
            else: self.diagnostics["DailyLock"] = f"⏳ PREVIEW (Will Lock At 05:00 EAT)"

        settled_reports = self.settle_pending_tickets(memory)

        if not is_already_locked or settled_reports:
            msg = "🤝 **RAW CONSENSUS DATA (3+ SITES AGREEMENT)** 🤝\n\n"
            if agreed_matches:
                for match in agreed_matches:
                    msg += f"{match}\n"
            else:
                msg += "No matches found with required agreement today.\n\n"

            if settled_reports:
                msg += "📊 **SETTLED RESULTS** 📊\n\n"
                for rep in settled_reports: msg += f"{rep}\n"
                msg += "\n"

            msg += "⚙️ **SCRAPER STATUS** ⚙️\n"
            essential_keys = ["Telegram", "Statarea", "Vitibet", "Zulubet", "BetClan", "CornersEngine", "QuantEngine", "DailyLock"]
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
