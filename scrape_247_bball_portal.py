"""
247Sports College Basketball Transfer Portal Scraper
Supports 2023-2026 class years.
Phase 1: Scroll-load players from rankings page.
Phase 2: Visit each player profile for portal entry + commit + draft from 247.
Phase 3: Cross-reference Wikipedia NBA Draft pages to fill/validate draft info.
"""

import asyncio
import csv
import json
import random
import re
import sys
from datetime import datetime
from playwright.async_api import async_playwright


def normalize_name(name):
    n = name.lower().strip()
    for suffix in [' jr.', ' jr', ' sr.', ' sr', ' iii', ' ii', ' iv', ' v']:
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
            break
    n = n.replace('.', '').replace('\u2019', "'").replace('\u2018', "'")
    n = ' '.join(n.split())
    return n


def build_name_variants(name):
    base = normalize_name(name)
    variants = {base}
    variants.add(base.replace('-', ' '))
    variants.add(base.replace('-', ''))
    variants.add(base.replace("'", ""))
    variants.add(base.replace("'", "").replace('-', ''))
    return variants


def match_player_to_draft(player_name, draft_lookup):
    variants = build_name_variants(player_name)
    for v in variants:
        if v in draft_lookup:
            return draft_lookup[v]
    # Last name only — only if unique match
    parts = normalize_name(player_name).split()
    if len(parts) >= 2:
        last = parts[-1]
        last_matches = [info for key, info in draft_lookup.items()
                       if ' ' in key and key.split()[-1] == last]
        if len(last_matches) == 1:
            return last_matches[0]
    return None


async def fetch_draft_data(page, draft_year):
    url = f"https://en.wikipedia.org/wiki/{draft_year}_NBA_draft"
    print(f"  Fetching {url}...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        players = await page.evaluate("""
        () => {
            const results = [];
            const tables = document.querySelectorAll('table.wikitable');
            for (const table of tables) {
                const headers = Array.from(table.querySelectorAll('th'))
                    .map(th => th.textContent.trim().toLowerCase());
                const hasPickCol = headers.some(h => h.includes('pick') || h === '#');
                const hasPlayerCol = headers.some(h => h.includes('player') || h.includes('name'));
                if (!hasPickCol || !hasPlayerCol) continue;

                let pickIdx=-1, playerIdx=-1, teamIdx=-1;
                headers.forEach((h, i) => {
                    if (pickIdx===-1 && (h==='pick' || h==='#' || h==='no.' || h==='no')) pickIdx=i;
                    if (playerIdx===-1 && (h.includes('player') || h.includes('name'))) playerIdx=i;
                    if (teamIdx===-1 && h.includes('team')) teamIdx=i;
                });
                if (playerIdx===-1) continue;

                let currentRound = '';
                let prev = table.previousElementSibling;
                for (let j=0; j<5 && prev; j++) {
                    const text = prev.textContent.trim().toLowerCase();
                    if (text.includes('first round')) { currentRound='1'; break; }
                    if (text.includes('second round')) { currentRound='2'; break; }
                    prev = prev.previousElementSibling;
                }

                for (const row of table.querySelectorAll('tr')) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 2) continue;
                    const get = (idx) => idx>=0 && idx<cells.length ? cells[idx].textContent.trim().replace(/\\[.*?\\]/g,'') : '';
                    let pick = get(pickIdx);
                    let player = get(playerIdx).replace(/\\s*\\(.*?\\)/g,'').replace(/[*\u2020\u2021#^~]/g,'').trim();
                    let team = get(teamIdx);
                    let round = currentRound;
                    if (!round) { const pn=parseInt(pick); if(pn&&pn<=30) round='1'; else if(pn&&pn>30) round='2'; }
                    if (player && player.length > 2)
                        results.push({ pick, player, team, round });
                }
            }
            return results;
        }
        """)
        print(f"  Found {len(players)} draft picks from {draft_year}")
        return players
    except Exception as e:
        print(f"  Error fetching {draft_year} draft: {e}")
        return []


async def scrape_transfer_portal(class_year=2025, target_count=250, output_file=None):
    if output_file is None:
        output_file = f"transfer_portal_top_{class_year}.csv"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        # ── PHASE 1: Load rankings page ──
        url = f"https://247sports.com/season/{class_year}-basketball/TransferPortalTop/"
        print(f"[Phase 1] Navigating to {url}")
        print(f"[Config] Class year: {class_year}, target: {target_count}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        for sel in ["button.close",".modal-close","[aria-label='Close']",".onesignal-popover-cancel-btn"]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000): await btn.click(); await page.wait_for_timeout(300)
            except: pass

        consecutive_no_change = 0
        prev_count = 0
        for attempt in range(80):
            current_count = await page.evaluate("() => document.querySelectorAll('li.transfer-player').length")
            print(f"  Scroll {attempt+1}: {current_count} transfer-player elements")
            if current_count >= target_count:
                print(f"  Reached target ({target_count})"); break
            if current_count == prev_count:
                consecutive_no_change += 1
                if consecutive_no_change >= 10:
                    print(f"  Stalled at {current_count}"); break
            else: consecutive_no_change = 0
            prev_count = current_count
            try:
                lm = page.locator("a:has-text('Load More'), button:has-text('Load More')").first
                if await lm.is_visible(timeout=800): await lm.click(); await page.wait_for_timeout(random.uniform(2000,4000)); continue
            except: pass
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.uniform(2000, 3500))

        print("[Phase 1] Extracting player data...")
        players = await page.evaluate("""
        () => {
            const results = [];
            document.querySelectorAll('li.transfer-player').forEach(li => {
                const rankEl = li.querySelector('.playerRank span');
                const rank = rankEl ? rankEl.textContent.trim() : '';
                const nameLink = li.querySelector('h3 a');
                let name = nameLink ? nameLink.textContent.trim() : '';
                let profileUrl = nameLink ? nameLink.href : '';
                if (!name) { const img = li.querySelector('div.avatar img'); if (img) name = img.alt||''; }
                if (!profileUrl) { const a = li.querySelector('div.avatar a[href*="/player/"]'); if (a) profileUrl = a.href; }
                const ratingEl = li.querySelector('div.rating');
                let rating = ''; if (ratingEl) { const m = ratingEl.textContent.match(/(0\\.\\d{4})/); if (m) rating = m[1]; }
                const posEl = li.querySelector('div.position');
                let position = ''; if (posEl) { const m = posEl.textContent.match(/\\b(PG|SG|CG|SF|PF|C)\\b/); if (m) position = m[1]; }
                const bioEl = li.querySelector('div.bio');
                let height='', weight='';
                if (bioEl) { const m = bioEl.textContent.match(/(\\d+)-(\\d+)\\s*\\/\\s*(\\d+)/); if (m) { height=m[1]+"'"+m[2]; weight=m[3]; } }
                const sc = li.querySelector('div.starContainer');
                let stars = sc ? sc.querySelectorAll('svg').length : 0;
                let fromTeam='', toTeam='';
                const ti = li.querySelectorAll('a[href*="/college/"][href*="transferportal"] img');
                if (ti.length>=1) fromTeam=ti[0].alt||''; if (ti.length>=2) toTeam=ti[1].alt||'';
                if (!fromTeam||!toTeam) {
                    const tl = li.querySelectorAll('a[href*="/college/"][href*="transferportal"]');
                    const c = t => t ? t.replace(/View \\d{4} basketball transfer players for /gi,'').trim() : '';
                    if (tl.length>=1&&!fromTeam) fromTeam=c(tl[0].title); if (tl.length>=2&&!toTeam) toTeam=c(tl[1].title);
                }
                if (name) results.push({rank,name,position,height,weight,stars,rating,fromTeam,toTeam,profileUrl});
            });
            return results;
        }
        """)

        print(f"[Phase 1] Extracted {len(players)} players")
        for p in players[:5]:
            print(f"  #{p['rank']} {p['name']} ({p['position']}) {p['height']}/{p['weight']} "
                  f"rating={p['rating']} {p['fromTeam']} -> {p['toTeam']}")

        if len(players) == 0:
            print("[FATAL] Zero players.")
            with open(output_file,"w",newline="") as f:
                csv.writer(f).writerow(["Rank","Player Name","Position","Height","Weight","Stars",
                    "247 Transfer Rating","Portal Entry Date","Commit Date","From Team","To Team",
                    "Draft Date","Draft Team","Draft Round","Draft Pick","Profile URL"])
            await browser.close(); return

        players = players[:target_count]

        # ── PHASE 2: Visit profiles ──
        print(f"\n[Phase 2] Visiting {len(players)} player profiles...")
        total = len(players)
        save_debug = True

        for i, player in enumerate(players):
            profile_url = player.get("profileUrl","")
            if not profile_url:
                for k in ["portalEntryDate","commitDate","draftDate247","draftTeam247"]: player[k]=""
                continue

            retries = 0
            while retries <= 2:
                try:
                    print(f"  [{i+1}/{total}] {player['name']}...", end=" ", flush=True)
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)

                    if save_debug:
                        save_debug = False
                        di = await page.evaluate("() => { const s=document.querySelector('script#timelineJson'); return s ? 'FOUND: '+s.textContent.substring(0,3000) : 'NONE'; }")
                        with open("diag_first_profile_timeline.html","w") as f: f.write(di)

                    result = await page.evaluate("""
                    (args) => {
                        const cy=args.classYear, py=args.priorYear;
                        let pe='',cd='',dd='',dt='',method='';
                        const s = document.querySelector('script#timelineJson');
                        if (!s) return {pe:'',cd:'',dd:'',dt:'',method:'no_json'};
                        try {
                            const raw=JSON.parse(s.textContent);
                            let evts=[]; if(Array.isArray(raw)&&raw.length>0&&raw[0].timeLineData) evts=raw[0].timeLineData;
                            method='json_'+evts.length;
                            for (const e of evts) {
                                const yr=String(e.year||''); if(yr!==cy&&!(e.date||'').includes(cy)) continue;
                                const b=(e.body||'').toLowerCase();
                                if(!pe&&(b.includes('entered the transfer portal')||b.includes('enters the transfer portal'))) pe=e.date||'';
                                if(!cd&&(b.includes('transfers to')||b.includes('commits to')||b.includes('enrolls at'))) cd=e.date||'';
                                if(pe&&cd) break;
                            }
                            if(!pe) { for(const e of evts) { const yr=String(e.year||''); if(yr!==py&&!(e.date||'').includes(py)) continue;
                                const b=(e.body||'').toLowerCase(); if(b.includes('entered the transfer portal')) { pe=e.date||''; method+='+prior'; break; } } }
                            for (const e of evts) { if((e.event||'').toLowerCase()==='draft') {
                                dd=e.date||''; const tm=(e.body||'').toLowerCase().match(/^(.+?)\\s+draft\\s+/);
                                if(tm) dt=tm[1].trim().replace(/\\b\\w/g,c=>c.toUpperCase()); method+='+d247'; break; } }
                        } catch(e) { method='json_err'; }
                        return {pe,cd,dd,dt,method};
                    }
                    """, {"classYear":str(class_year),"priorYear":str(class_year-1)})

                    player["portalEntryDate"]=result.get("pe",""); player["commitDate"]=result.get("cd","")
                    player["draftDate247"]=result.get("dd",""); player["draftTeam247"]=result.get("dt","")
                    method=result.get("method","")

                    if not player["portalEntryDate"] or not player["commitDate"]:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(2000)
                        try:
                            await page.wait_for_selector("section.timeline", timeout=5000)
                            try:
                                sa=page.locator("a:has-text('See all'),a:has-text('Load more')").first
                                if await sa.is_visible(timeout=1500): await sa.click(); await page.wait_for_timeout(2000)
                            except: pass
                            dom=await page.evaluate("""(a)=>{let pe='',cd='';const tb=document.querySelector('.timeline-body');
                                if(!tb) return{pe,cd};const yhs=tb.querySelectorAll(':scope>h4');
                                const scan=(yr)=>{for(const yh of yhs){if(yh.textContent.trim()!==yr)continue;const tl=yh.nextElementSibling;
                                if(!tl)continue;for(const el of tl.querySelectorAll('[class*="vertical-timeline-element"]')){
                                const h3=el.querySelector('h3'),h4=el.querySelector('h4');if(!h3||!h4)continue;
                                const dm=h3.textContent.trim().match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);if(!dm)continue;
                                const t=h4.textContent.trim().toLowerCase();
                                if(!pe&&t.includes('entered the transfer portal'))pe=dm[1];
                                if(!cd&&(t.includes('transfers to')||t.includes('commits to')||t.includes('enrolls at')))cd=dm[1];}break;}};
                                scan(a.classYear);if(!pe)scan(a.priorYear);return{pe,cd};}""",
                                {"classYear":str(class_year),"priorYear":str(class_year-1)})
                            if not player["portalEntryDate"] and dom.get("pe"): player["portalEntryDate"]=dom["pe"]; method+="+dom_pe"
                            if not player["commitDate"] and dom.get("cd"): player["commitDate"]=dom["cd"]; method+="+dom_cd"
                        except: pass

                    pe=player['portalEntryDate'] or 'MISS'; cd=player['commitDate'] or 'MISS'
                    d247=f" | 247draft={player['draftDate247']}" if player['draftDate247'] else ""
                    extra=f" | {method}" if i<10 else ""
                    print(f"portal={pe} | commit={cd}{d247}{extra}")
                    break
                except Exception as e:
                    retries+=1
                    if retries<=2: print(f"RETRY: {e}"); await page.wait_for_timeout(3000)
                    else: print(f"FAILED: {e}"); player["portalEntryDate"]=""; player["commitDate"]=""; player["draftDate247"]=""; player["draftTeam247"]=""

            if i%10==9:
                d=random.uniform(2,5); print(f"  (cooldown {d:.1f}s)"); await page.wait_for_timeout(d*1000)

        # ── PHASE 3: Wikipedia NBA Draft cross-reference ──
        draft_years = [class_year+1, class_year+2]
        current_year = datetime.now().year
        draft_years = [y for y in draft_years if y <= current_year]

        print(f"\n[Phase 3] Cross-referencing NBA Draft from Wikipedia...")
        print(f"  Draft years to check: {draft_years}")

        all_picks = []
        for dy in draft_years:
            picks = await fetch_draft_data(page, dy)
            for pk in picks: pk['draft_year'] = dy
            all_picks.extend(picks)

        print(f"  Total draft picks loaded: {len(all_picks)}")

        # Build lookup
        draft_lookup = {}
        for dp in all_picks:
            info = {'pick':dp['pick'],'team':dp['team'],'round':dp['round'],
                    'original_name':dp['player'],'draft_year':dp['draft_year']}
            for v in build_name_variants(dp['player']):
                if len(v) > 2: draft_lookup[v] = info

        print(f"  Lookup entries: {len(draft_lookup)}")

        wiki_new = 0; wiki_validated = 0
        for player in players:
            match = match_player_to_draft(player['name'], draft_lookup)
            if match:
                player["draftTeam"] = match['team']
                player["draftRound"] = match['round']
                player["draftPick"] = match['pick']
                if player.get("draftDate247"):
                    player["draftDate"] = player["draftDate247"]
                    wiki_validated += 1
                else:
                    player["draftDate"] = ""
                    wiki_new += 1
                tag = 'validated' if player.get("draftDate247") else 'NEW'
                print(f"  [Wiki] #{player['rank']} {player['name']} -> "
                      f"{match['team']} Rd{match['round']} Pick#{match['pick']} ({tag})")
            else:
                player["draftDate"] = player.get("draftDate247","")
                player["draftTeam"] = player.get("draftTeam247","")
                player["draftRound"] = ""
                player["draftPick"] = ""

        print(f"\n  Wikipedia matches: {wiki_new+wiki_validated}")
        print(f"  New (not in 247): {wiki_new}")
        print(f"  Validated (also in 247): {wiki_validated}")

        # ── QA ──
        total_players = len(players)
        has_pe = sum(1 for p in players if p.get("portalEntryDate"))
        has_cd = sum(1 for p in players if p.get("commitDate"))
        has_draft = sum(1 for p in players if p.get("draftTeam"))
        print(f"\n[QA] {total_players} players total")
        print(f"[QA] Portal entry: {has_pe}/{total_players} ({100*has_pe/max(total_players,1):.0f}%)")
        print(f"[QA] Commit date: {has_cd}/{total_players} ({100*has_cd/max(total_players,1):.0f}%)")
        print(f"[QA] Drafted: {has_draft}/{total_players}")
        drafted = [p for p in players if p.get("draftTeam")]
        if drafted:
            print(f"[QA] Drafted players ({len(drafted)}):")
            for d in drafted:
                print(f"  #{d['rank']} {d['name']} -> {d['draftTeam']} Rd{d.get('draftRound','')} Pick#{d.get('draftPick','')} ({d.get('draftDate','')})")

        # ── CSV ──
        ps = f"{class_year-1}/{str(class_year)[-2:]}"
        ns = f"{class_year}/{str(class_year+1)[-2:]}"
        print(f"\n[Output] Writing {len(players)} rows to {output_file}")
        with open(output_file,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Rank","Player Name","Position","Height","Weight","Stars",
                "247 Transfer Rating","Portal Entry Date","Commit Date",
                f"{ps} Team",f"{ns} Team","Draft Date","Draft Team","Draft Round","Draft Pick","Profile URL"])
            for p in players:
                w.writerow([p.get("rank",""),p.get("name",""),p.get("position",""),
                    p.get("height",""),p.get("weight",""),p.get("stars",""),
                    p.get("rating",""),p.get("portalEntryDate",""),p.get("commitDate",""),
                    p.get("fromTeam",""),p.get("toTeam",""),p.get("draftDate",""),
                    p.get("draftTeam",""),p.get("draftRound",""),p.get("draftPick",""),
                    p.get("profileUrl","")])

        print("Done!")
        await browser.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("player_count", type=int, nargs="?", default=250)
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()
    asyncio.run(scrape_transfer_portal(class_year=args.year, target_count=args.player_count))
