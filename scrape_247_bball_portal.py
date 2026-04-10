"""
247Sports 2025 Basketball Transfer Portal Top 250 Scraper
Phase 1: Scroll-load all players from the rankings page, collect profile URLs.
Phase 2: Visit each player profile, extract portal entry date + commit date from Timeline.
"""

import asyncio
import csv
import random
import re
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

        # ── PHASE 1: Load rankings page and scroll to get all players ──
        url = "https://247sports.com/season/2025-basketball/TransferPortalTop/"
        print(f"[Phase 1] Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # Dismiss popups
        try:
            for sel in ["button.close", ".modal-close", "[aria-label='Close']", ".onesignal-popover-cancel-btn"]:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await page.wait_for_timeout(500)
        except:
            pass

        # Scroll to load all players
        consecutive_no_change = 0
        prev_count = 0

        for attempt in range(80):
            current_count = await page.locator("li:has(a[href*='/player/'])").count()
            print(f"  Scroll {attempt+1}: {current_count} players loaded")

            if current_count >= target_count:
                print(f"  Reached target ({target_count})")
                break

            if current_count == prev_count:
                consecutive_no_change += 1
                if consecutive_no_change >= 10:
                    print(f"  Stalled at {current_count} players after {consecutive_no_change} attempts")
                    break
            else:
                consecutive_no_change = 0
            prev_count = current_count

            # Try Load More button
            try:
                load_more = page.locator("a:has-text('Load More'), button:has-text('Load More')").first
                if await load_more.is_visible(timeout=1000):
                    await load_more.click()
                    await page.wait_for_timeout(random.uniform(2000, 4000))
                    continue
            except:
                pass

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.uniform(2000, 3500))

        # ── Extract basic info + profile URLs from the list page ──
        print("[Phase 1] Extracting player list data...")
        players = await page.evaluate("""
        () => {
            const results = [];
            const items = document.querySelectorAll('li');
            items.forEach(li => {
                const playerLink = li.querySelector('a[href*="/player/"]');
                if (!playerLink) return;
                const hasRating = li.textContent.match(/(0\\.\\d{4})/);
                if (!hasRating) return;

                const allText = li.textContent.trim();
                const rankMatch = allText.match(/^\\s*(\\d{1,3})\\s/);
                const rank = rankMatch ? rankMatch[1] : '';
                const name = playerLink.textContent.trim();
                const profileUrl = playerLink.href;
                const rating = hasRating[1];
                const posMatch = allText.match(/\\b(PG|SG|CG|SF|PF|C)\\b/);
                const position = posMatch ? posMatch[1] : '';
                const hwMatch = allText.match(/(\\d+-\\d+)\\s*\\/\\s*(\\d+)/);
                const height = hwMatch ? hwMatch[1] : '';
                const weight = hwMatch ? hwMatch[2] : '';
                const starCount = (allText.match(/Rating Star/g) || []).length;
                const stars = Math.min(starCount, 5);

                const teamLinks = li.querySelectorAll('a[href*="/college/"][href*="transferportal"]');
                let fromTeam = '', toTeam = '';
                const cleanTitle = (t) => t.replace(/View \\d{4} basketball transfer players for /i, '').trim();
                if (teamLinks.length >= 1) fromTeam = cleanTitle(teamLinks[0].getAttribute('title') || '');
                if (teamLinks.length >= 2) toTeam = cleanTitle(teamLinks[1].getAttribute('title') || '');

                if (name && rank) {
                    results.push({ rank, name, position, height, weight, stars, rating, fromTeam, toTeam, profileUrl });
                }
            });
            return results;
        }
        """)

        print(f"[Phase 1] Got {len(players)} players from list page")

        # ── PHASE 2: Visit each profile to get timeline dates ──
        print("[Phase 2] Visiting player profiles for dates...")
        total = len(players)

        for i, player in enumerate(players):
            profile_url = player.get("profileUrl", "")
            if not profile_url:
                player["portalEntryDate"] = ""
                player["commitDate"] = ""
                continue

            try:
                print(f"  [{i+1}/{total}] {player['name']}...", end=" ", flush=True)
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(random.uniform(1500, 3000))

                # Scroll timeline into view
                try:
                    timeline = page.locator("section.timeline, #timeline, .vertical-timeline").first
                    await timeline.scroll_into_view_if_needed(timeout=3000)
                    await page.wait_for_timeout(500)
                except:
                    pass

                # Click "See all X entries" or "Load more" to expand full timeline
                try:
                    see_all = page.locator("a:has-text('See all'), a:has-text('Load more')").first
                    if await see_all.is_visible(timeout=1500):
                        await see_all.click()
                        await page.wait_for_timeout(2000)
                except:
                    pass

                # Extract dates from timeline h3/h4 pairs
                dates = await page.evaluate("""
                () => {
                    let portalEntry = '';
                    let commitDate = '';

                    // Find all timeline event containers
                    const elements = document.querySelectorAll('[class*="vertical-timeline-element"]');

                    for (const el of elements) {
                        const h3 = el.querySelector('h3');
                        const h4 = el.querySelector('h4');
                        if (!h3 || !h4) continue;

                        const h3Text = h3.textContent.trim();
                        const h4Text = h4.textContent.trim().toLowerCase();

                        // Date format in h3: "Apr 5, 2025: Transfer" or "Mar 31, 2025: Transfer"
                        const dateMatch = h3Text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/);
                        if (!dateMatch) continue;
                        const dateStr = dateMatch[1];

                        if (h4Text.includes('entered the transfer portal') ||
                            h4Text.includes('enters the transfer portal') ||
                            h4Text.includes('enter the transfer portal')) {
                            portalEntry = dateStr;
                        }

                        if (h4Text.includes('commits to') ||
                            h4Text.includes('committed to') ||
                            h4Text.includes('signs with') ||
                            h4Text.includes('enrolls at')) {
                            if (!commitDate) commitDate = dateStr;
                        }
                    }

                    // Fallback: parse full timeline text
                    if (!portalEntry || !commitDate) {
                        const section = document.querySelector('#timeline, section.timeline, .timeline');
                        if (section) {
                            const text = section.textContent;
                            if (!portalEntry) {
                                const m = text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4}).*?(?:entered|enters)\\s+the\\s+transfer\\s+portal/);
                                if (m) portalEntry = m[1];
                            }
                            if (!commitDate) {
                                const m = text.match(/([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4}).*?(?:commits? to|enrolls? at)/);
                                if (m) commitDate = m[1];
                            }
                        }
                    }

                    return { portalEntry, commitDate };
                }
                """)

                player["portalEntryDate"] = dates.get("portalEntry", "")
                player["commitDate"] = dates.get("commitDate", "")
                print(f"portal={player['portalEntryDate']} | commit={player['commitDate']}")

            except Exception as e:
                print(f"ERROR: {e}")
                player["portalEntryDate"] = ""
                player["commitDate"] = ""
                continue

            # Cooldown every 10 players
            if i % 10 == 9:
                delay = random.uniform(3, 6)
                print(f"  (cooldown {delay:.1f}s)")
                await page.wait_for_timeout(delay * 1000)

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
