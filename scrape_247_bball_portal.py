"""
247Sports College Basketball Transfer Portal Scraper
Supports 2023-2026 class years.
Phase 1: Scroll-load players from rankings page using li.transfer-player elements.
Phase 2: Visit each player profile, parse script#timelineJson for dates.
         Falls back to DOM parsing if JSON not found.
"""

import asyncio
import csv
import json
import random
import sys
from playwright.async_api import async_playwright


async def scrape_transfer_portal(class_year=2025, target_count=250,
                                  output_file=None):
    if output_file is None:
        output_file = f"transfer_portal_top_{class_year}.csv"

    # For a given class year, portal entries + commits can span two calendar years
    # e.g. 2025 class: portal entries from ~Mar 2024 - May 2025, commits through ~Jun 2025
    valid_years = [str(class_year), str(class_year - 1)]

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
        print(f"[Config] Class year: {class_year}, valid date years: {valid_years}, target: {target_count}")
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
                        // Output as 6'9 to prevent Excel date interpretation
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
                    "24/25 Team","25/26 Team","Profile URL"
                ])
            await browser.close()
            return

        players = players[:target_count]

        # ── PHASE 2: Visit profiles for timeline dates ──
        print(f"\n[Phase 2] Visiting {len(players)} player profiles for dates...")
        total = len(players)
        save_debug = True

        for i, player in enumerate(players):
            profile_url = player.get("profileUrl", "")
            if not profile_url:
                player["portalEntryDate"] = ""
                player["commitDate"] = ""
                continue

            retries = 0
            max_retries = 2
            while retries <= max_retries:
                try:
                    print(f"  [{i+1}/{total}] {player['name']}...", end=" ", flush=True)
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)

                    # Debug first profile
                    if save_debug:
                        save_debug = False
                        debug_info = await page.evaluate("""
                        () => {
                            const script = document.querySelector('script#timelineJson');
                            if (script) {
                                return 'FOUND script#timelineJson: ' + script.textContent.substring(0, 5000);
                            }
                            const allScripts = document.querySelectorAll('script[id]');
                            const ids = Array.from(allScripts).map(s => s.id);
                            return 'NO script#timelineJson. Script IDs: ' + ids.join(', ');
                        }
                        """)
                        with open("diag_first_profile_timeline.html", "w") as f:
                            f.write(debug_info)
                        print(f"\n    [Debug] Saved ({len(debug_info)} chars)")

                    # ── Extract dates: JSON primary, DOM fallback ──
                    dates = await page.evaluate("""
                    (validYears) => {
                        let portalEntry = '';
                        let commitDate = '';
                        let method = '';

                        const cleanDate = (d) => {
                            if (!d) return '';
                            const m = d.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);
                            if (m) return m[1];
                            try {
                                const dt = new Date(d);
                                if (!isNaN(dt)) {
                                    const months = ['Jan','Feb','Mar','Apr','May','Jun',
                                                  'Jul','Aug','Sep','Oct','Nov','Dec'];
                                    return months[dt.getMonth()] + ' ' + dt.getDate() + ', ' + dt.getFullYear();
                                }
                            } catch(e) {}
                            return d;
                        };

                        // === METHOD 1: Parse script#timelineJson ===
                        const script = document.querySelector('script#timelineJson');
                        if (script) {
                            try {
                                const data = JSON.parse(script.textContent);
                                method = 'json';

                                let flatEvents = [];
                                if (Array.isArray(data)) {
                                    flatEvents = data;
                                } else if (typeof data === 'object') {
                                    // Try common nested structures
                                    if (data.Events) flatEvents = data.Events;
                                    else if (data.events) flatEvents = data.events;
                                    else if (data.items) flatEvents = data.items;
                                    else if (data.TimelineEvents) flatEvents = data.TimelineEvents;
                                    else {
                                        // Flatten all arrays in the object
                                        for (const key of Object.keys(data)) {
                                            if (Array.isArray(data[key])) {
                                                flatEvents = flatEvents.concat(data[key]);
                                            }
                                        }
                                    }
                                }

                                method += '_events=' + flatEvents.length;

                                // Log first event structure for debugging
                                if (flatEvents.length > 0) {
                                    method += '_keys=' + Object.keys(flatEvents[0]).join('|');
                                }

                                for (const evt of flatEvents) {
                                    const desc = (evt.Description || evt.description || evt.headline ||
                                                  evt.title || evt.Title || evt.text ||
                                                  evt.Headline || evt.Action || evt.action || '').toLowerCase();

                                    const dateStr = evt.DateString || evt.dateString || evt.Date ||
                                                    evt.date || evt.FormattedDate || evt.formattedDate ||
                                                    evt.EventDate || evt.eventDate || '';

                                    const year = String(evt.Year || evt.year || '');

                                    // Check if this event is in valid years for this class
                                    let isValidYear = false;
                                    for (const vy of validYears) {
                                        if (year === vy || dateStr.includes(vy)) {
                                            isValidYear = true;
                                            break;
                                        }
                                    }
                                    if (!isValidYear) continue;

                                    if (!portalEntry &&
                                        (desc.includes('entered the transfer portal') ||
                                         desc.includes('enters the transfer portal'))) {
                                        portalEntry = cleanDate(dateStr);
                                    }

                                    if (!commitDate &&
                                        (desc.includes('commits to') ||
                                         desc.includes('committed to') ||
                                         desc.includes('signs with') ||
                                         desc.includes('enrolls at') ||
                                         desc.includes('transfers to'))) {
                                        commitDate = cleanDate(dateStr);
                                    }

                                    if (portalEntry && commitDate) break;
                                }
                            } catch(e) {
                                method = 'json_error:' + e.message.substring(0, 50);
                            }
                        }

                        // === METHOD 2 FALLBACK: DOM 2025 year section ===
                        if (!portalEntry || !commitDate) {
                            // Scroll to trigger lazy-load
                            window.scrollTo(0, document.body.scrollHeight);

                            const timelineBody = document.querySelector('.timeline-body');
                            if (timelineBody) {
                                const yearHeaders = timelineBody.querySelectorAll(':scope > h4');

                                for (const yearH4 of yearHeaders) {
                                    const yearText = yearH4.textContent.trim();
                                    if (!validYears.includes(yearText)) continue;

                                    const timeline = yearH4.nextElementSibling;
                                    if (!timeline) continue;

                                    const elements = timeline.querySelectorAll(
                                        '[class*="vertical-timeline-element"]'
                                    );
                                    for (const el of elements) {
                                        const h3 = el.querySelector('h3');
                                        const h4el = el.querySelector('h4');
                                        if (!h3 || !h4el) continue;
                                        const h3Text = h3.textContent.trim();
                                        const h4Text = h4el.textContent.trim().toLowerCase();
                                        const dateMatch = h3Text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);
                                        if (!dateMatch) continue;
                                        const dateStr = dateMatch[1];
                                        if (!portalEntry &&
                                            (h4Text.includes('entered the transfer portal') ||
                                             h4Text.includes('enters the transfer portal')))
                                            portalEntry = dateStr;
                                        if (!commitDate &&
                                            (h4Text.includes('commits to') ||
                                             h4Text.includes('enrolls at') ||
                                             h4Text.includes('transfers to')))
                                            commitDate = dateStr;
                                        if (portalEntry && commitDate) break;
                                    }
                                    if (portalEntry && commitDate) break;
                                }
                                method += '+dom';
                            } else {
                                method += '+no_dom';
                            }
                        }

                        if (!method) method = 'nothing_found';
                        return { portalEntry, commitDate, method };
                    }
                    """, valid_years)

                    player["portalEntryDate"] = dates.get("portalEntry", "")
                    player["commitDate"] = dates.get("commitDate", "")

                    pe = player['portalEntryDate'] or 'MISS'
                    cd = player['commitDate'] or 'MISS'
                    extra = f" | {dates.get('method','')}" if i < 15 else ""
                    print(f"portal={pe} | commit={cd}{extra}")
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

            if i % 10 == 9:
                delay = random.uniform(2, 5)
                print(f"  (cooldown {delay:.1f}s)")
                await page.wait_for_timeout(delay * 1000)

        # ── QA ──
        total_players = len(players)
        has_portal = sum(1 for p in players if p.get("portalEntryDate"))
        has_commit = sum(1 for p in players if p.get("commitDate"))
        has_both = sum(1 for p in players if p.get("portalEntryDate") and p.get("commitDate"))
        print(f"\n[QA] {total_players} players total")
        print(f"[QA] Portal entry date: {has_portal}/{total_players} ({100*has_portal/max(total_players,1):.0f}%)")
        print(f"[QA] Commit date: {has_commit}/{total_players} ({100*has_commit/max(total_players,1):.0f}%)")
        print(f"[QA] Both dates: {has_both}/{total_players}")
        missing = [p['name'] for p in players if not p.get("portalEntryDate")][:15]
        if missing:
            print(f"[QA] Missing portal date (first 15): {', '.join(missing)}")
        missing_c = [p['name'] for p in players if not p.get("commitDate")][:15]
        if missing_c:
            print(f"[QA] Missing commit date (first 15): {', '.join(missing_c)}")

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
                "Profile URL"
            ])
            for p in players:
                writer.writerow([
                    p.get("rank",""), p.get("name",""), p.get("position",""),
                    p.get("height",""), p.get("weight",""), p.get("stars",""),
                    p.get("rating",""), p.get("portalEntryDate",""),
                    p.get("commitDate",""), p.get("fromTeam",""),
                    p.get("toTeam",""), p.get("profileUrl",""),
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
