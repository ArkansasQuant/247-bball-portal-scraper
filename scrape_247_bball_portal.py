"""
247Sports 2025 Basketball Transfer Portal Top 250 Scraper
Phase 1: Scroll-load players from rankings page using li.transfer-player elements.
Phase 2: Visit each player profile to get portal entry + commit dates from Timeline.

DOM structure (from diagnostics):
  li.transfer-player.is-ranked
    div.playerRank > span  (rank number)
    div.avatar > ... > a[href*="/player/"]  (profile link)
    h3 > a  (player name + profile link)
    div.starContainer  (SVG stars)
    div.rating  (0.9900)
    div.trend
    div.position  (PF)
    div.bio  (6-9 / 230)
    div.status  (Enrolled)
    div.statusDate  (may contain date)
    div.transfer-prediction  (team logos/links)
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

        # Dismiss popups
        for sel in ["button.close", ".modal-close", "[aria-label='Close']",
                     ".onesignal-popover-cancel-btn", "#onesignal-popover-cancel-btn"]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(300)
            except:
                pass

        # Scroll to load players — count using the REAL selector
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

            # Try Load More button
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

        # ── Extract using exact DOM structure from diagnostics ──
        print("[Phase 1] Extracting player data from li.transfer-player elements...")
        players = await page.evaluate("""
        () => {
            const results = [];
            const items = document.querySelectorAll('li.transfer-player');

            items.forEach((li) => {
                // Rank: div.playerRank > span
                const rankEl = li.querySelector('div.playerRank span, .playerRank span');
                const rank = rankEl ? rankEl.textContent.trim() : '';

                // Name + Profile URL: h3 > a
                const nameLink = li.querySelector('h3 a');
                const name = nameLink ? nameLink.textContent.trim() : '';
                const profileUrl = nameLink ? nameLink.href : '';

                // If no name from h3, try avatar link
                let finalName = name;
                if (!finalName) {
                    const avatarImg = li.querySelector('div.avatar img');
                    if (avatarImg) finalName = avatarImg.alt || '';
                }
                let finalUrl = profileUrl;
                if (!finalUrl) {
                    const avatarLink = li.querySelector('div.avatar a[href*="/player/"]');
                    if (avatarLink) finalUrl = avatarLink.href;
                }

                // Rating: div.rating
                const ratingEl = li.querySelector('div.rating');
                let rating = '';
                if (ratingEl) {
                    const m = ratingEl.textContent.match(/(0\\.\\d{4})/);
                    if (m) rating = m[1];
                }

                // Position: div.position
                const posEl = li.querySelector('div.position');
                let position = '';
                if (posEl) {
                    const m = posEl.textContent.match(/\\b(PG|SG|CG|SF|PF|C)\\b/);
                    if (m) position = m[1];
                }

                // Height / Weight: div.bio (e.g. "6-9 / 230")
                const bioEl = li.querySelector('div.bio');
                let height = '', weight = '';
                if (bioEl) {
                    const m = bioEl.textContent.match(/(\\d+-\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) { height = m[1]; weight = m[2]; }
                }

                // Stars: count SVGs in div.starContainer
                const starContainer = li.querySelector('div.starContainer');
                let stars = 0;
                if (starContainer) {
                    stars = starContainer.querySelectorAll('svg').length;
                }

                // Status: div.status
                const statusEl = li.querySelector('div.status');
                const status = statusEl ? statusEl.textContent.trim() : '';

                // StatusDate: div.statusDate (may have the date right on the list page)
                const statusDateEl = li.querySelector('div.statusDate');
                const statusDate = statusDateEl ? statusDateEl.textContent.trim() : '';

                // Teams: look in div.transfer-prediction for team links/images
                let fromTeam = '', toTeam = '';

                // From team: first team image/link before "transferred to"
                const teamImgs = li.querySelectorAll('a[href*="/college/"][href*="transferportal"] img');
                if (teamImgs.length >= 1) fromTeam = teamImgs[0].alt || '';
                if (teamImgs.length >= 2) toTeam = teamImgs[1].alt || '';

                // Fallback: use title attributes on team links
                if (!fromTeam || !toTeam) {
                    const teamLinks = li.querySelectorAll('a[href*="/college/"][href*="transferportal"]');
                    const cleanTitle = (t) => {
                        if (!t) return '';
                        return t.replace(/View \\d{4} basketball transfer players for /gi, '').trim();
                    };
                    if (teamLinks.length >= 1 && !fromTeam) fromTeam = cleanTitle(teamLinks[0].title);
                    if (teamLinks.length >= 2 && !toTeam) toTeam = cleanTitle(teamLinks[1].title);
                }

                if (finalName) {
                    results.push({
                        rank, name: finalName, position, height, weight,
                        stars, rating, status, statusDate,
                        fromTeam, toTeam, profileUrl: finalUrl
                    });
                }
            });

            return results;
        }
        """)

        print(f"[Phase 1] Extracted {len(players)} players")

        # Print first 5 for verification
        for p in players[:5]:
            print(f"  #{p['rank']} {p['name']} ({p['position']}) {p['height']}/{p['weight']} "
                  f"rating={p['rating']} stars={p['stars']} status={p['status']} "
                  f"statusDate={p['statusDate']} {p['fromTeam']} -> {p['toTeam']}")

        if len(players) == 0:
            print("\n[FATAL] Zero players extracted. Saving debug info...")
            html = await page.content()
            with open("diag_full_page.html", "w") as f:
                f.write(html[:500000])
            await page.screenshot(path="diag_screenshot.png", full_page=False)

            # Extra diag: dump all li classes
            classes = await page.evaluate("""
                () => {
                    const lis = document.querySelectorAll('li');
                    const classMap = {};
                    lis.forEach(li => {
                        const c = li.className || '(none)';
                        classMap[c] = (classMap[c] || 0) + 1;
                    });
                    return classMap;
                }
            """)
            print("[Diag] li class distribution:")
            for cls, cnt in sorted(classes.items(), key=lambda x: -x[1])[:20]:
                print(f"  {cnt}x  class='{cls}'")

            with open(output_file, "w", newline="") as f:
                csv.writer(f).writerow([
                    "Rank", "Player Name", "Position", "Height", "Weight",
                    "Stars", "247 Transfer Rating", "Portal Entry Date",
                    "Commit Date", "24/25 Team", "25/26 Team", "Profile URL"
                ])
            await browser.close()
            return

        # Cap to target count
        players = players[:target_count]

        # ── PHASE 2: Visit each profile for timeline dates ──
        print(f"\n[Phase 2] Visiting {len(players)} player profiles for dates...")
        total = len(players)

        # Save first profile HTML for debugging
        save_first_profile_debug = True

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
                    await page.wait_for_timeout(random.uniform(1500, 2500))

                    # Scroll to timeline section
                    await page.evaluate("""
                        () => {
                            const el = document.querySelector('#timeline, section.timeline, .timeline');
                            if (el) el.scrollIntoView({behavior: 'instant'});
                        }
                    """)
                    await page.wait_for_timeout(800)

                    # Expand timeline if needed
                    try:
                        see_all = page.locator("a:has-text('See all'), a:has-text('Load more')").first
                        if await see_all.is_visible(timeout=1500):
                            await see_all.click()
                            await page.wait_for_timeout(1500)
                    except:
                        pass

                    # Debug: save first profile's timeline HTML
                    if save_first_profile_debug:
                        save_first_profile_debug = False
                        debug_html = await page.evaluate("""
                        () => {
                            const timeline = document.querySelector('#timeline, section.timeline, .timeline');
                            if (timeline) return timeline.outerHTML.substring(0, 5000);
                            // If no timeline, dump all section/div IDs and classes
                            const sections = document.querySelectorAll('section, div[id]');
                            const info = [];
                            sections.forEach(s => {
                                info.push({tag: s.tagName, id: s.id, class: s.className.substring(0,80)});
                            });
                            return 'NO TIMELINE. Sections: ' + JSON.stringify(info.slice(0, 30));
                        }
                        """)
                        with open("diag_first_profile_timeline.html", "w") as f:
                            f.write(debug_html)
                        print(f"\n    [Debug] Saved first profile timeline HTML ({len(debug_html)} chars)")

                    # Extract dates from timeline
                    dates = await page.evaluate("""
                    () => {
                        let portalEntry = '';
                        let commitDate = '';

                        // Method 1: vertical-timeline-element containers with h3+h4
                        const elements = document.querySelectorAll('[class*="vertical-timeline-element"]');
                        for (const el of elements) {
                            const h3 = el.querySelector('h3');
                            const h4 = el.querySelector('h4');
                            if (!h3 || !h4) continue;

                            const h3Text = h3.textContent.trim();
                            const h4Text = h4.textContent.trim().toLowerCase();

                            const dateMatch = h3Text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);
                            if (!dateMatch) continue;
                            const dateStr = dateMatch[1];

                            if (h4Text.includes('entered the transfer portal') ||
                                h4Text.includes('enters the transfer portal')) {
                                portalEntry = dateStr;
                            }
                            if (h4Text.includes('commits to') ||
                                h4Text.includes('committed to') ||
                                h4Text.includes('signs with') ||
                                h4Text.includes('enrolls at')) {
                                if (!commitDate) commitDate = dateStr;
                            }
                        }

                        // Method 2: search full timeline text
                        if (!portalEntry || !commitDate) {
                            const section = document.querySelector('#timeline, section.timeline, .timeline, .timeline-body');
                            if (section) {
                                const text = section.textContent;
                                if (!portalEntry) {
                                    const m = text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4}).*?(?:entered|enters)\\s+the\\s+transfer/);
                                    if (m) portalEntry = m[1];
                                }
                                if (!commitDate) {
                                    const m = text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4}).*?(?:commits? to|enrolls? at)/);
                                    if (m) commitDate = m[1];
                                }
                            }
                        }

                        // Method 3: scan ALL h3/h4 pairs on the page (timeline might not be in expected container)
                        if (!portalEntry || !commitDate) {
                            const allH3 = document.querySelectorAll('h3');
                            for (const h3 of allH3) {
                                const h4 = h3.parentElement?.querySelector('h4') ||
                                           h3.nextElementSibling;
                                if (!h4 || h4.tagName !== 'H4') continue;
                                const h3Text = h3.textContent.trim();
                                const h4Text = h4.textContent.trim().toLowerCase();
                                const dateMatch = h3Text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);
                                if (!dateMatch) continue;
                                const dateStr = dateMatch[1];
                                if (!portalEntry && (h4Text.includes('entered the transfer portal') ||
                                    h4Text.includes('enters the transfer portal'))) {
                                    portalEntry = dateStr;
                                }
                                if (!commitDate && (h4Text.includes('commits to') ||
                                    h4Text.includes('enrolls at'))) {
                                    commitDate = dateStr;
                                }
                            }
                        }

                        const timelineEl = document.querySelector('#timeline, section.timeline, .timeline');
                        const elemCount = elements.length;
                        return {
                            portalEntry, commitDate,
                            timelineExists: !!timelineEl,
                            elemCount
                        };
                    }
                    """)

                    player["portalEntryDate"] = dates.get("portalEntry", "")
                    player["commitDate"] = dates.get("commitDate", "")

                    pe = player['portalEntryDate'] or 'MISS'
                    cd = player['commitDate'] or 'MISS'
                    extra = f" | timeline={dates.get('timelineExists')} elems={dates.get('elemCount')}" if i < 5 else ""
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

            # Cooldown every 10 players
            if i % 10 == 9:
                delay = random.uniform(2, 5)
                print(f"  (cooldown {delay:.1f}s)")
                await page.wait_for_timeout(delay * 1000)

        # ── QA Summary ──
        total_players = len(players)
        has_portal = sum(1 for p in players if p.get("portalEntryDate"))
        has_commit = sum(1 for p in players if p.get("commitDate"))
        has_both = sum(1 for p in players if p.get("portalEntryDate") and p.get("commitDate"))
        print(f"\n[QA] {total_players} players total")
        print(f"[QA] Portal entry date: {has_portal}/{total_players} ({100*has_portal/max(total_players,1):.0f}%)")
        print(f"[QA] Commit date: {has_commit}/{total_players} ({100*has_commit/max(total_players,1):.0f}%)")
        print(f"[QA] Both dates: {has_both}/{total_players}")
        missing_portal = [p['name'] for p in players if not p.get("portalEntryDate")][:10]
        if missing_portal:
            print(f"[QA] Missing portal date (first 10): {', '.join(missing_portal)}")

        # ── Write CSV ──
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
                    p.get("rank", ""),
                    p.get("name", ""),
                    p.get("position", ""),
                    p.get("height", ""),
                    p.get("weight", ""),
                    p.get("stars", ""),
                    p.get("rating", ""),
                    p.get("portalEntryDate", ""),
                    p.get("commitDate", ""),
                    p.get("fromTeam", ""),
                    p.get("toTeam", ""),
                    p.get("profileUrl", ""),
                ])

        print("Done!")
        await browser.close()


if __name__ == "__main__":
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    asyncio.run(scrape_transfer_portal(target_count=target))
