"""
247Sports College Basketball Transfer Portal Scraper
Supports 2023-2026 class years.
Phase 1: Scroll-load players from rankings page using li.transfer-player elements.
Phase 2: Visit each player profile, parse script#timelineJson for dates + draft info.
         Falls back to DOM parsing if JSON not found.

JSON event structure:
  {date, year, event, body, institution, color, img, moreLink, loaded}
  event types: "Transfer", "Draft", "Enrolled", "Signed", "Commit", "Offer", etc.
  body is TRUNCATED with "..." — cannot rely on body containing full text like "No. 15"

Draft detection: use evt.event === "Draft" (JSON) or h3 contains ": Draft" (DOM)
"""

import asyncio
import csv
import json
import random
import sys
from playwright.async_api import async_playwright


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

        for sel in ["button.close", ".modal-close", "[aria-label='Close']",
                     ".onesignal-popover-cancel-btn"]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(300)
            except:
                pass

        consecutive_no_change = 0
        prev_count = 0

        for attempt in range(80):
            current_count = await page.evaluate(
                "() => document.querySelectorAll('li.transfer-player').length"
            )
            print(f"  Scroll {attempt+1}: {current_count} transfer-player elements")

            if current_count >= target_count:
                print(f"  Reached target ({target_count})")
                break

            if current_count == prev_count:
                consecutive_no_change += 1
                if consecutive_no_change >= 10:
                    print(f"  Stalled at {current_count} after {consecutive_no_change} attempts")
                    break
            else:
                consecutive_no_change = 0
            prev_count = current_count

            try:
                load_more = page.locator("a:has-text('Load More'), button:has-text('Load More')").first
                if await load_more.is_visible(timeout=800):
                    await load_more.click()
                    await page.wait_for_timeout(random.uniform(2000, 4000))
                    continue
            except:
                pass

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.uniform(2000, 3500))

        # ── Extract from li.transfer-player ──
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
                if (!name) {
                    const img = li.querySelector('div.avatar img');
                    if (img) name = img.alt || '';
                }
                if (!profileUrl) {
                    const a = li.querySelector('div.avatar a[href*="/player/"]');
                    if (a) profileUrl = a.href;
                }

                const ratingEl = li.querySelector('div.rating');
                let rating = '';
                if (ratingEl) {
                    const m = ratingEl.textContent.match(/(0\\.\\d{4})/);
                    if (m) rating = m[1];
                }

                const posEl = li.querySelector('div.position');
                let position = '';
                if (posEl) {
                    const m = posEl.textContent.match(/\\b(PG|SG|CG|SF|PF|C)\\b/);
                    if (m) position = m[1];
                }

                const bioEl = li.querySelector('div.bio');
                let height = '', weight = '';
                if (bioEl) {
                    const m = bioEl.textContent.match(/(\\d+)-(\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) {
                        height = m[1] + "'" + m[2];
                        weight = m[3];
                    }
                }

                const starContainer = li.querySelector('div.starContainer');
                let stars = starContainer ? starContainer.querySelectorAll('svg').length : 0;

                let fromTeam = '', toTeam = '';
                const teamImgs = li.querySelectorAll('a[href*="/college/"][href*="transferportal"] img');
                if (teamImgs.length >= 1) fromTeam = teamImgs[0].alt || '';
                if (teamImgs.length >= 2) toTeam = teamImgs[1].alt || '';
                if (!fromTeam || !toTeam) {
                    const teamLinks = li.querySelectorAll('a[href*="/college/"][href*="transferportal"]');
                    const clean = t => t ? t.replace(/View \\d{4} basketball transfer players for /gi, '').trim() : '';
                    if (teamLinks.length >= 1 && !fromTeam) fromTeam = clean(teamLinks[0].title);
                    if (teamLinks.length >= 2 && !toTeam) toTeam = clean(teamLinks[1].title);
                }

                if (name) {
                    results.push({ rank, name, position, height, weight, stars, rating, fromTeam, toTeam, profileUrl });
                }
            });
            return results;
        }
        """)

        print(f"[Phase 1] Extracted {len(players)} players")
        for p in players[:5]:
            print(f"  #{p['rank']} {p['name']} ({p['position']}) {p['height']}/{p['weight']} "
                  f"rating={p['rating']} stars={p['stars']} {p['fromTeam']} -> {p['toTeam']}")

        if len(players) == 0:
            print("[FATAL] Zero players extracted.")
            await page.screenshot(path="diag_screenshot.png", full_page=False)
            with open(output_file, "w", newline="") as f:
                csv.writer(f).writerow([
                    "Rank","Player Name","Position","Height","Weight","Stars",
                    "247 Transfer Rating","Portal Entry Date","Commit Date",
                    "From Team","To Team","Draft Date","Draft Team","Pick #",
                    "Profile URL"
                ])
            await browser.close()
            return

        players = players[:target_count]

        # ── PHASE 2: Visit profiles for timeline dates + draft ──
        print(f"\n[Phase 2] Visiting {len(players)} player profiles for dates + draft info...")
        total = len(players)
        save_debug = True

        for i, player in enumerate(players):
            profile_url = player.get("profileUrl", "")
            if not profile_url:
                player["portalEntryDate"] = ""
                player["commitDate"] = ""
                player["draftDate"] = ""
                player["draftTeam"] = ""
                player["draftPick"] = ""
                continue

            retries = 0
            max_retries = 2
            while retries <= max_retries:
                try:
                    print(f"  [{i+1}/{total}] {player['name']}...", end=" ", flush=True)
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)

                    if save_debug:
                        save_debug = False
                        debug_info = await page.evaluate("""
                        () => {
                            const script = document.querySelector('script#timelineJson');
                            if (script) {
                                return 'FOUND script#timelineJson: ' + script.textContent.substring(0, 5000);
                            }
                            return 'NO script#timelineJson found';
                        }
                        """)
                        with open("diag_first_profile_timeline.html", "w") as f:
                            f.write(debug_info)
                        print(f"\n    [Debug] Saved ({len(debug_info)} chars)")

                    # ── Step 1: Parse JSON for portal entry + commit + draft ──
                    json_dates = await page.evaluate("""
                    (args) => {
                        const classYear = args.classYear;
                        const priorYear = args.priorYear;
                        let portalEntry = '';
                        let commitDate = '';
                        let draftDate = '';
                        let draftTeam = '';
                        let draftPick = '';
                        let method = '';
                        let jsonTruncated = false;

                        const isPortalEvent = (body) =>
                            body.includes('entered the transfer portal') ||
                            body.includes('enters the transfer portal');

                        const isCommitEvent = (body) =>
                            body.includes('transfers to') ||
                            body.includes('commits to') ||
                            body.includes('committed to') ||
                            body.includes('signs with') ||
                            body.includes('enrolls at');

                        const parseDraftFromBody = (body) => {
                            let team = '';
                            let pick = '';
                            // body: "los angeles lakers draft dalton knecht with the..."
                            // or:   "miami heat draft kel'el ware with the no. 15 pick..."
                            const teamMatch = body.match(/^(.+?)\\s+draft\\s+/i);
                            if (teamMatch) {
                                team = teamMatch[1].trim()
                                    .replace(/\\b\\w/g, c => c.toUpperCase());
                            }
                            const pickMatch = body.match(/no\\.\\s*(\\d+)/i);
                            if (pickMatch) pick = pickMatch[1];
                            return { team, pick };
                        };

                        const script = document.querySelector('script#timelineJson');
                        if (script) {
                            try {
                                const raw = JSON.parse(script.textContent);
                                let events = [];
                                let totalCount = 0;

                                if (Array.isArray(raw) && raw.length > 0 && raw[0].timeLineData) {
                                    events = raw[0].timeLineData;
                                    totalCount = raw[0].count || events.length;
                                    method = 'json';
                                }

                                method += '_loaded=' + events.length + '_of_' + totalCount;
                                jsonTruncated = (totalCount > events.length);

                                // PASS 1: classYear for portal entry + commit
                                for (const evt of events) {
                                    const yr = String(evt.year || '');
                                    if (yr !== classYear && !(evt.date || '').includes(classYear)) continue;
                                    const body = (evt.body || '').toLowerCase();
                                    if (!portalEntry && isPortalEvent(body))
                                        portalEntry = evt.date || '';
                                    if (!commitDate && isCommitEvent(body))
                                        commitDate = evt.date || '';
                                    if (portalEntry && commitDate) break;
                                }

                                // PASS 2: priorYear for portal entry ONLY
                                if (!portalEntry) {
                                    for (const evt of events) {
                                        const yr = String(evt.year || '');
                                        if (yr !== priorYear && !(evt.date || '').includes(priorYear)) continue;
                                        const body = (evt.body || '').toLowerCase();
                                        if (isPortalEvent(body)) {
                                            portalEntry = evt.date || '';
                                            method += '+prior_entry';
                                            break;
                                        }
                                    }
                                }

                                // PASS 3: Draft — use evt.event === "Draft" (not body text!)
                                for (const evt of events) {
                                    if ((evt.event || '').toLowerCase() === 'draft') {
                                        draftDate = evt.date || '';
                                        const info = parseDraftFromBody((evt.body || '').toLowerCase());
                                        draftTeam = info.team;
                                        draftPick = info.pick;
                                        method += '+json_draft';
                                        break;
                                    }
                                }

                            } catch(e) {
                                method = 'json_error:' + e.message.substring(0, 50);
                            }
                        } else {
                            method = 'no_json';
                        }

                        return {
                            portalEntry, commitDate, draftDate, draftTeam, draftPick,
                            method, jsonTruncated
                        };
                    }
                    """, {"classYear": str(class_year), "priorYear": str(class_year - 1)})

                    player["portalEntryDate"] = json_dates.get("portalEntry", "")
                    player["commitDate"] = json_dates.get("commitDate", "")
                    player["draftDate"] = json_dates.get("draftDate", "")
                    player["draftTeam"] = json_dates.get("draftTeam", "")
                    player["draftPick"] = json_dates.get("draftPick", "")
                    method = json_dates.get("method", "")
                    json_truncated = json_dates.get("jsonTruncated", False)

                    # ── Step 2: DOM fallback for anything still missing ──
                    needs_dom = (not player["portalEntryDate"] or
                                 not player["commitDate"] or
                                 (not player["draftDate"] and json_truncated))

                    if needs_dom:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(2000)

                        try:
                            await page.wait_for_selector("section.timeline", timeout=5000)
                        except:
                            await page.evaluate("window.scrollTo(0, 0)")
                            await page.wait_for_timeout(300)
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await page.wait_for_timeout(2500)

                        # Click "See all" to expand full timeline
                        try:
                            see_all = page.locator(
                                "a:has-text('See all'), a:has-text('Load more')"
                            ).first
                            if await see_all.is_visible(timeout=2000):
                                await see_all.click()
                                await page.wait_for_timeout(2500)
                        except:
                            pass

                        dom_dates = await page.evaluate("""
                        (args) => {
                            const classYear = args.classYear;
                            const priorYear = args.priorYear;
                            let portalEntry = '';
                            let commitDate = '';
                            let draftDate = '';
                            let draftTeam = '';
                            let draftPick = '';

                            const isPortalEvent = (t) =>
                                t.includes('entered the transfer portal') ||
                                t.includes('enters the transfer portal');
                            const isCommitEvent = (t) =>
                                t.includes('transfers to') ||
                                t.includes('commits to') ||
                                t.includes('enrolls at');

                            const timelineBody = document.querySelector('.timeline-body');
                            if (!timelineBody) return {portalEntry:'',commitDate:'',draftDate:'',draftTeam:'',draftPick:''};

                            const yearHeaders = timelineBody.querySelectorAll(':scope > h4');

                            const scanYear = (yearText) => {
                                let pe='', cd='', dd='', dt='', dp='';
                                for (const yh of yearHeaders) {
                                    if (yh.textContent.trim() !== yearText) continue;
                                    const timeline = yh.nextElementSibling;
                                    if (!timeline) continue;
                                    const elems = timeline.querySelectorAll('[class*="vertical-timeline-element"]');
                                    for (const el of elems) {
                                        const h3 = el.querySelector('h3');
                                        const h4el = el.querySelector('h4');
                                        if (!h3 || !h4el) continue;
                                        const h3Text = h3.textContent.trim();
                                        const h4Text = h4el.textContent.trim().toLowerCase();

                                        const dateMatch = h3Text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);
                                        if (!dateMatch) continue;
                                        const d = dateMatch[1];

                                        if (!pe && isPortalEvent(h4Text)) pe = d;
                                        if (!cd && isCommitEvent(h4Text)) cd = d;

                                        // Draft: check h3 for ": Draft" event type
                                        if (!dd && h3Text.toLowerCase().includes(': draft')) {
                                            dd = d;
                                            const tm = h4Text.match(/^(.+?)\\s+draft\\s+/);
                                            if (tm) dt = tm[1].trim().replace(/\\b\\w/g, c => c.toUpperCase());
                                            const pm = h4Text.match(/no\\.\\s*(\\d+)/);
                                            if (pm) dp = pm[1];
                                        }
                                    }
                                    break;
                                }
                                return { pe, cd, dd, dt, dp };
                            };

                            // Portal + commit from classYear
                            const r1 = scanYear(classYear);
                            portalEntry = r1.pe;
                            commitDate = r1.cd;

                            // Portal fallback from priorYear
                            if (!portalEntry) {
                                const r2 = scanYear(priorYear);
                                portalEntry = r2.pe;
                            }

                            // Draft from ALL years (most recent first)
                            if (!draftDate) {
                                const allYears = Array.from(yearHeaders).map(h => h.textContent.trim());
                                for (const yr of allYears) {
                                    const r = scanYear(yr);
                                    if (r.dd) {
                                        draftDate = r.dd;
                                        draftTeam = r.dt;
                                        draftPick = r.dp;
                                        break;
                                    }
                                }
                            }

                            return { portalEntry, commitDate, draftDate, draftTeam, draftPick };
                        }
                        """, {"classYear": str(class_year), "priorYear": str(class_year - 1)})

                        if not player["portalEntryDate"] and dom_dates.get("portalEntry"):
                            player["portalEntryDate"] = dom_dates["portalEntry"]
                            method += "+dom_pe"
                        if not player["commitDate"] and dom_dates.get("commitDate"):
                            player["commitDate"] = dom_dates["commitDate"]
                            method += "+dom_cd"
                        if not player["draftDate"] and dom_dates.get("draftDate"):
                            player["draftDate"] = dom_dates["draftDate"]
                            player["draftTeam"] = dom_dates.get("draftTeam", "")
                            player["draftPick"] = dom_dates.get("draftPick", "")
                            method += "+dom_draft"

                    pe = player['portalEntryDate'] or 'MISS'
                    cd = player['commitDate'] or 'MISS'
                    draft = ""
                    if player['draftDate']:
                        draft = f" | DRAFT: {player['draftTeam']} #{player['draftPick']} ({player['draftDate']})"
                    extra = f" | {method}" if i < 15 else ""
                    print(f"portal={pe} | commit={cd}{draft}{extra}")
                    break

                except Exception as e:
                    retries += 1
                    if retries <= max_retries:
                        print(f"RETRY ({retries}/{max_retries}): {e}")
                        await page.wait_for_timeout(3000)
                    else:
                        print(f"FAILED: {e}")
                        player["portalEntryDate"] = ""
                        player["commitDate"] = ""
                        player["draftDate"] = ""
                        player["draftTeam"] = ""
                        player["draftPick"] = ""

            if i % 10 == 9:
                delay = random.uniform(2, 5)
                print(f"  (cooldown {delay:.1f}s)")
                await page.wait_for_timeout(delay * 1000)

        # ── QA ──
        total_players = len(players)
        has_portal = sum(1 for p in players if p.get("portalEntryDate"))
        has_commit = sum(1 for p in players if p.get("commitDate"))
        has_both = sum(1 for p in players if p.get("portalEntryDate") and p.get("commitDate"))
        has_draft = sum(1 for p in players if p.get("draftDate"))
        print(f"\n[QA] {total_players} players total")
        print(f"[QA] Portal entry date: {has_portal}/{total_players} ({100*has_portal/max(total_players,1):.0f}%)")
        print(f"[QA] Commit date: {has_commit}/{total_players} ({100*has_commit/max(total_players,1):.0f}%)")
        print(f"[QA] Both dates: {has_both}/{total_players}")
        print(f"[QA] Drafted: {has_draft}/{total_players}")
        missing = [p['name'] for p in players if not p.get("portalEntryDate")][:15]
        if missing:
            print(f"[QA] Missing portal date (first 15): {', '.join(missing)}")
        drafted = [f"{p['name']} -> {p['draftTeam']} #{p['draftPick']}" for p in players if p.get("draftDate")]
        if drafted:
            print(f"[QA] Drafted players ({len(drafted)}):")
            for d in drafted[:20]:
                print(f"  {d}")

        # ── CSV ──
        prev_season = f"{class_year - 1}/{str(class_year)[-2:]}"
        next_season = f"{class_year}/{str(class_year + 1)[-2:]}"

        print(f"\n[Output] Writing {len(players)} rows to {output_file}")
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Rank", "Player Name", "Position", "Height", "Weight",
                "Stars", "247 Transfer Rating", "Portal Entry Date",
                "Commit Date", f"{prev_season} Team", f"{next_season} Team",
                "Draft Date", "Draft Team", "Pick #", "Profile URL"
            ])
            for p in players:
                writer.writerow([
                    p.get("rank",""), p.get("name",""), p.get("position",""),
                    p.get("height",""), p.get("weight",""), p.get("stars",""),
                    p.get("rating",""), p.get("portalEntryDate",""),
                    p.get("commitDate",""), p.get("fromTeam",""),
                    p.get("toTeam",""), p.get("draftDate",""),
                    p.get("draftTeam",""), p.get("draftPick",""),
                    p.get("profileUrl",""),
                ])

        print("Done!")
        await browser.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("player_count", type=int, nargs="?", default=250)
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()
    asyncio.run(scrape_transfer_portal(
        class_year=args.year,
        target_count=args.player_count
    ))
