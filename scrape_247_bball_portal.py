"""
247Sports 2025 Basketball Transfer Portal Top 250 Scraper
Phase 1: Scroll-load players from rankings page using li.transfer-player elements.
Phase 2: Visit each player profile, wait for section.timeline to load,
         extract portal entry + commit dates from the 2025 year section only.

Timeline DOM structure (from browser inspection):
  section.timeline
    div#timeline
    div.timeline-body
      h4  "2026"                          (year header - direct child of timeline-body)
      div.vertical-timeline              (events for 2026)
      h4  "2025"                          (year header)
      div.vertical-timeline              (events for 2025 - THIS IS WHAT WE WANT)
        div.vertical-timeline-element
          h3  "Mar 31, 2025: Transfer"   (date + type)
          h4  "Player entered the transfer portal"  (description)
        div.vertical-timeline-element
          h3  "Apr 5, 2025: Transfer"
          h4  "Player commits to Michigan Wolverines"
      h4  "2023"
      div.vertical-timeline              (events for 2023 - skip)
"""

import asyncio
import csv
import json
import random
import sys
from playwright.async_api import async_playwright


async def scrape_transfer_portal(target_count=250, output_file="transfer_portal_top_250.csv"):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        # ── PHASE 1: Load rankings page ──
        url = "https://247sports.com/season/2025-basketball/TransferPortalTop/"
        print(f"[Phase 1] Navigating to {url}")
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
                    const m = bioEl.textContent.match(/(\\d+-\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) { height = m[1]; weight = m[2]; }
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

                    # Full scroll to bottom to trigger lazy-load of timeline
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)

                    # Wait for section.timeline to appear
                    timeline_loaded = False
                    try:
                        await page.wait_for_selector("section.timeline", timeout=6000)
                        timeline_loaded = True
                    except:
                        # Retry: scroll top then bottom again
                        await page.evaluate("window.scrollTo(0, 0)")
                        await page.wait_for_timeout(500)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(3000)
                        try:
                            await page.wait_for_selector("section.timeline", timeout=5000)
                            timeline_loaded = True
                        except:
                            pass

                    if not timeline_loaded:
                        # Last resort: step-scroll
                        height = await page.evaluate("() => document.body.scrollHeight")
                        for y in range(0, height, 400):
                            await page.evaluate(f"window.scrollTo(0, {y})")
                            await page.wait_for_timeout(250)
                        await page.wait_for_timeout(2000)
                        try:
                            await page.wait_for_selector("section.timeline", timeout=3000)
                            timeline_loaded = True
                        except:
                            pass

                    # Expand "See all X entries"
                    if timeline_loaded:
                        try:
                            see_all = page.locator(
                                "a:has-text('See all'), a:has-text('Load more')"
                            ).first
                            if await see_all.is_visible(timeout=1500):
                                await see_all.click()
                                await page.wait_for_timeout(2000)
                        except:
                            pass

                    # Debug first profile
                    if save_debug:
                        save_debug = False
                        debug_info = await page.evaluate("""
                        () => {
                            const section = document.querySelector('section.timeline');
                            if (!section) return 'NO section.timeline found on page';
                            const body = section.querySelector('.timeline-body');
                            if (!body) return 'section.timeline exists but no .timeline-body child';
                            const yearH4s = body.querySelectorAll(':scope > h4');
                            const years = Array.from(yearH4s).map(h => h.textContent.trim());
                            const allElems = section.querySelectorAll('[class*="vertical-timeline-element"]');
                            return JSON.stringify({
                                years: years,
                                totalTimelineElements: allElems.length,
                                bodySnippet: body.innerHTML.substring(0, 3000)
                            }, null, 2);
                        }
                        """)
                        with open("diag_first_profile_timeline.html", "w") as f:
                            f.write(debug_info)
                        print(f"\n    [Debug] Saved profile debug ({len(debug_info)} chars)")

                    # ── Extract dates from 2025 year section ONLY ──
                    dates = await page.evaluate("""
                    () => {
                        let portalEntry = '';
                        let commitDate = '';

                        const timelineBody = document.querySelector('.timeline-body');
                        if (!timelineBody) {
                            return { portalEntry: '', commitDate: '', debug: 'no timeline-body' };
                        }

                        // Find year headers (direct child h4 of timeline-body)
                        const yearHeaders = timelineBody.querySelectorAll(':scope > h4');
                        let targetTimeline = null;
                        const allYears = Array.from(yearHeaders).map(h => h.textContent.trim());

                        for (const h4 of yearHeaders) {
                            if (h4.textContent.trim() === '2025') {
                                targetTimeline = h4.nextElementSibling;
                                break;
                            }
                        }

                        if (!targetTimeline) {
                            return {
                                portalEntry: '', commitDate: '',
                                debug: 'years=[' + allYears.join(',') + '] no 2025 section'
                            };
                        }

                        // Get events from 2025 section only
                        const elements = targetTimeline.querySelectorAll(
                            '[class*="vertical-timeline-element"]'
                        );

                        for (const el of elements) {
                            const h3 = el.querySelector('h3');
                            const h4 = el.querySelector('h4');
                            if (!h3 || !h4) continue;

                            const h3Text = h3.textContent.trim();
                            const h4Text = h4.textContent.trim().toLowerCase();

                            const dateMatch = h3Text.match(
                                /([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/
                            );
                            if (!dateMatch) continue;
                            const dateStr = dateMatch[1];

                            if (!portalEntry &&
                                (h4Text.includes('entered the transfer portal') ||
                                 h4Text.includes('enters the transfer portal'))) {
                                portalEntry = dateStr;
                            }

                            if (!commitDate &&
                                (h4Text.includes('commits to') ||
                                 h4Text.includes('committed to') ||
                                 h4Text.includes('signs with') ||
                                 h4Text.includes('enrolls at') ||
                                 h4Text.includes('transfers to'))) {
                                commitDate = dateStr;
                            }

                            if (portalEntry && commitDate) break;
                        }

                        return {
                            portalEntry, commitDate,
                            debug: 'years=[' + allYears.join(',') + '] 2025_elems=' + elements.length
                        };
                    }
                    """)

                    player["portalEntryDate"] = dates.get("portalEntry", "")
                    player["commitDate"] = dates.get("commitDate", "")

                    pe = player['portalEntryDate'] or 'MISS'
                    cd = player['commitDate'] or 'MISS'
                    extra = f" | {dates.get('debug','')}" if i < 10 else ""
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
        missing = [p['name'] for p in players if not p.get("portalEntryDate")][:10]
        if missing:
            print(f"[QA] Missing portal date (first 10): {', '.join(missing)}")

        # ── CSV ──
        print(f"\n[Output] Writing {len(players)} rows to {output_file}")
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Rank", "Player Name", "Position", "Height", "Weight",
                "Stars", "247 Transfer Rating", "Portal Entry Date",
                "Commit Date", "24/25 Team", "25/26 Team", "Profile URL"
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
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    asyncio.run(scrape_transfer_portal(target_count=target))
