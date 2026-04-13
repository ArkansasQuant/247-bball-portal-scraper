"""
247Sports 2025 Basketball Transfer Portal Top 250 Scraper
Phase 1: Scroll-load all players from the rankings page, collect profile URLs.
Phase 2: Visit each player profile, extract portal entry + commit dates from Timeline.
"""

import asyncio
import csv
import json
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

        # Scroll to load all players
        consecutive_no_change = 0
        prev_count = 0

        for attempt in range(80):
            # Count player links on page
            current_count = await page.evaluate("""
                () => document.querySelectorAll('a[href*="/player/"]').length
            """)
            print(f"  Scroll {attempt+1}: {current_count} player links found")

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

        # ── DIAGNOSTICS: Dump first 3 player-containing elements ──
        print("\n[Diagnostics] Dumping sample player elements...")
        diag = await page.evaluate("""
        () => {
            const links = document.querySelectorAll('a[href*="/player/"]');
            const samples = [];
            const seen = new Set();
            for (const link of links) {
                // Walk up to find the list item container
                let container = link.closest('li') || link.parentElement?.parentElement?.parentElement;
                if (!container || seen.has(container)) continue;
                seen.add(container);
                samples.push({
                    tagName: container.tagName,
                    className: container.className,
                    outerHTMLSnippet: container.outerHTML.substring(0, 2000),
                    textContentSnippet: container.textContent.substring(0, 500),
                    playerHref: link.href,
                    playerText: link.textContent.trim(),
                    childTags: Array.from(container.children).map(c => c.tagName + '.' + c.className.split(' ')[0]).join(', '),
                });
                if (samples.length >= 3) break;
            }
            return samples;
        }
        """)
        # Save diagnostics
        with open("diag_player_elements.json", "w") as f:
            json.dump(diag, f, indent=2)
        for i, s in enumerate(diag):
            print(f"\n  --- Sample {i+1} ---")
            print(f"  Tag: {s['tagName']}, Class: {s['className'][:80]}")
            print(f"  Player: {s['playerText']} -> {s['playerHref'][:80]}")
            print(f"  Children: {s['childTags'][:120]}")
            print(f"  Text (first 200): {s['textContentSnippet'][:200]}")

        # ── Extract player data ──
        # Strategy: find ALL <a> links to /player/ pages, then for each one walk up
        # to the container and extract data from siblings/nearby elements.
        print("\n[Phase 1] Extracting player list data...")
        players = await page.evaluate("""
        () => {
            const results = [];
            const links = document.querySelectorAll('a[href*="/player/"][href*="/college-"]');
            const seenUrls = new Set();

            for (const link of links) {
                const href = link.href;
                if (seenUrls.has(href)) continue;
                seenUrls.add(href);

                const name = link.textContent.trim();
                if (!name) continue;

                // Walk up to the containing li
                const li = link.closest('li');
                if (!li) continue;

                const text = li.textContent;

                // Rating: 0.XXXX
                const ratingMatch = text.match(/(0\\.\\d{4})/);
                const rating = ratingMatch ? ratingMatch[1] : '';

                // Position
                const posMatch = text.match(/\\b(PG|SG|CG|SF|PF|C)\\b/);
                const position = posMatch ? posMatch[1] : '';

                // Height / Weight: 6-9 / 230
                const hwMatch = text.match(/(\\d+-\\d+)\\s*\\/\\s*(\\d+)/);
                const height = hwMatch ? hwMatch[1] : '';
                const weight = hwMatch ? hwMatch[2] : '';

                // Stars: count star images/elements
                let stars = li.querySelectorAll('.star-rating-yellow, [class*="star-rating"]').length;
                if (stars === 0) {
                    // Count img alt or text references
                    const imgs = li.querySelectorAll('img[alt="Rating Star"], img[alt*="Rating Star"]');
                    stars = imgs.length;
                }
                if (stars === 0) {
                    stars = (text.match(/Rating Star/g) || []).length;
                }
                stars = Math.min(stars, 5);

                // Rank: look for a standalone number in the li
                // Could be in a span, div, or direct text before the player name
                let rank = '';
                // Method 1: Check for rank-specific elements
                const rankEl = li.querySelector('.rankings-page__rank, .rank, [class*="rank"]');
                if (rankEl) {
                    const rm = rankEl.textContent.trim().match(/(\\d{1,3})/);
                    if (rm) rank = rm[1];
                }
                // Method 2: The rank number appears as the first number in the li text
                if (!rank) {
                    const rm = text.trim().match(/^\\s*(\\d{1,3})\\b/);
                    if (rm) rank = rm[1];
                }
                // Method 3: Look through all child text nodes
                if (!rank) {
                    const walker = document.createTreeWalker(li, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        const m = t.match(/^(\\d{1,3})$/);
                        if (m && parseInt(m[1]) <= 300) {
                            rank = m[1];
                            break;
                        }
                    }
                }

                // Teams: links with /college/ and transferportal in href
                const teamLinks = li.querySelectorAll('a[href*="/college/"][href*="transferportal"]');
                let fromTeam = '', toTeam = '';
                const cleanTitle = (t) => {
                    if (!t) return '';
                    return t.replace(/View \\d{4} basketball transfer players for /gi, '').trim();
                };
                if (teamLinks.length >= 1) {
                    fromTeam = cleanTitle(teamLinks[0].getAttribute('title'));
                    // Fallback: try img alt inside the link
                    if (!fromTeam) {
                        const img = teamLinks[0].querySelector('img');
                        if (img) fromTeam = img.alt || '';
                    }
                }
                if (teamLinks.length >= 2) {
                    toTeam = cleanTitle(teamLinks[1].getAttribute('title'));
                    if (!toTeam) {
                        const img = teamLinks[1].querySelector('img');
                        if (img) toTeam = img.alt || '';
                    }
                }

                // Use index as fallback rank
                if (!rank) rank = String(results.length + 1);

                results.push({
                    rank, name, position, height, weight, stars,
                    rating, fromTeam, toTeam, profileUrl: href
                });
            }
            return results;
        }
        """)

        print(f"[Phase 1] Extracted {len(players)} players")

        # Diagnostic: print first 5
        for p in players[:5]:
            print(f"  #{p['rank']} {p['name']} ({p['position']}) {p['height']}/{p['weight']} "
                  f"rating={p['rating']} {p['fromTeam']} -> {p['toTeam']}")

        if len(players) == 0:
            print("\n[FATAL] Zero players extracted. Saving page HTML for debugging...")
            html = await page.content()
            with open("diag_full_page.html", "w") as f:
                f.write(html[:500000])
            await page.screenshot(path="diag_screenshot.png", full_page=False)
            await browser.close()
            # Still write empty CSV so artifact upload doesn't fail
            with open(output_file, "w", newline="") as f:
                csv.writer(f).writerow([
                    "Rank", "Player Name", "Position", "Height", "Weight",
                    "Stars", "247 Transfer Rating", "Portal Entry Date",
                    "Commit Date", "24/25 Team", "25/26 Team", "Profile URL"
                ])
            return

        # Cap to target count
        players = players[:target_count]

        # ── PHASE 2: Visit each profile to get timeline dates ──
        print(f"\n[Phase 2] Visiting {len(players)} player profiles for dates...")
        total = len(players)

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
                    try:
                        await page.evaluate("""
                            () => {
                                const el = document.querySelector('#timeline, section.timeline, .timeline');
                                if (el) el.scrollIntoView({behavior: 'instant'});
                            }
                        """)
                        await page.wait_for_timeout(800)
                    except:
                        pass

                    # Expand timeline if "See all" / "Load more" exists
                    try:
                        see_all = page.locator("a:has-text('See all'), a:has-text('Load more')").first
                        if await see_all.is_visible(timeout=1500):
                            await see_all.click()
                            await page.wait_for_timeout(1500)
                    except:
                        pass

                    # Extract dates from timeline h3/h4 pairs
                    dates = await page.evaluate("""
                    () => {
                        let portalEntry = '';
                        let commitDate = '';

                        // Get all elements with vertical-timeline-element in class
                        const elements = document.querySelectorAll(
                            '[class*="vertical-timeline-element"]'
                        );

                        for (const el of elements) {
                            const h3 = el.querySelector('h3');
                            const h4 = el.querySelector('h4');
                            if (!h3 || !h4) continue;

                            const h3Text = h3.textContent.trim();
                            const h4Text = h4.textContent.trim().toLowerCase();

                            // Date in h3: "Apr 5, 2025: Transfer" or "Mar 31, 2025: Transfer"
                            const dateMatch = h3Text.match(
                                /([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4})/
                            );
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

                        // Fallback: search timeline section text
                        if (!portalEntry || !commitDate) {
                            const section = document.querySelector(
                                '#timeline, section.timeline, .timeline, .timeline-body'
                            );
                            if (section) {
                                const text = section.textContent;
                                if (!portalEntry) {
                                    const m = text.match(
                                        /([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4}).*?(?:entered|enters)\\s+the\\s+transfer/
                                    );
                                    if (m) portalEntry = m[1];
                                }
                                if (!commitDate) {
                                    const m = text.match(
                                        /([A-Z][a-z]{2}\\s+\\d{1,2},\\s*\\d{4}).*?(?:commits? to|enrolls? at)/
                                    );
                                    if (m) commitDate = m[1];
                                }
                            }
                        }

                        // Debug info
                        const timelineEl = document.querySelector(
                            '#timeline, section.timeline, .timeline'
                        );
                        const timelineExists = !!timelineEl;
                        const timelineText = timelineEl
                            ? timelineEl.textContent.substring(0, 300) : 'NO TIMELINE FOUND';
                        const elemCount = elements.length;

                        return { portalEntry, commitDate, timelineExists, elemCount, timelineText };
                    }
                    """)

                    player["portalEntryDate"] = dates.get("portalEntry", "")
                    player["commitDate"] = dates.get("commitDate", "")

                    status = f"portal={player['portalEntryDate'] or 'MISS'} | commit={player['commitDate'] or 'MISS'}"
                    if i < 5:
                        status += f" | timeline={dates.get('timelineExists')} elems={dates.get('elemCount')}"
                        if not dates.get("portalEntry") and not dates.get("commitDate"):
                            status += f" | text={dates.get('timelineText', '')[:100]}"
                    print(status)
                    break  # success, exit retry loop

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
