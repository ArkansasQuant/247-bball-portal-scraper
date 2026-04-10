"""
247Sports 2025 Basketball Transfer Portal Top 250 Scraper
Uses Playwright to scroll-load all players and extract data.
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        url = "https://247sports.com/season/2025-basketball/TransferPortalTop/"
        print(f"Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Dismiss any modals/popups
        try:
            close_btn = page.locator("button.close, .modal-close, [aria-label='Close']").first
            if await close_btn.is_visible(timeout=2000):
                await close_btn.click()
        except:
            pass

        # Scroll to load all players
        consecutive_no_change = 0
        prev_count = 0
        max_attempts = 60

        for attempt in range(max_attempts):
            player_items = page.locator("ul.transfer-portal-list > li, .transfer-portal-rows li.transfer-portal-row, .rankings-page__list-container li")
            current_count = await player_items.count()

            if current_count == 0:
                # Try alternate selector
                player_items = page.locator("li:has(.rankings-page__name-link), li:has(a[href*='/player/'])")
                current_count = await player_items.count()

            print(f"Attempt {attempt+1}: {current_count} players loaded")

            if current_count >= target_count:
                print(f"Reached target of {target_count}")
                break

            if current_count == prev_count:
                consecutive_no_change += 1
                if consecutive_no_change >= 8:
                    print(f"No new players after {consecutive_no_change} scroll attempts. Stopping at {current_count}.")
                    break
            else:
                consecutive_no_change = 0

            prev_count = current_count

            # Try clicking "Load More" button if it exists
            try:
                load_more = page.locator("a:has-text('Load More'), button:has-text('Load More'), .load-more-btn").first
                if await load_more.is_visible(timeout=1000):
                    await load_more.click()
                    await page.wait_for_timeout(random.uniform(1500, 3000))
                    continue
            except:
                pass

            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.uniform(1500, 3000))

        # Now extract all player data
        print("Extracting player data...")

        # Get the full page HTML for parsing
        content = await page.content()

        # Use page.evaluate to extract structured data from the DOM
        players = await page.evaluate("""
        () => {
            const results = [];
            // Find all player list items - they have rank numbers
            const items = document.querySelectorAll('.transfer-portal-rows li, ul.rankings-page__list-container > li, .portal-list > li');

            // If the above doesn't work, try a more generic approach
            let playerElements = items.length > 0 ? items : document.querySelectorAll('li');

            // Filter to only player entries (ones with player links)
            const filtered = [];
            playerElements.forEach(li => {
                const playerLink = li.querySelector('a[href*="/player/"]');
                const hasRating = li.textContent.includes('0.9') || li.textContent.includes('0.8') || li.textContent.includes('0.7');
                if (playerLink && hasRating) {
                    filtered.push(li);
                }
            });

            filtered.forEach((li, idx) => {
                try {
                    // Rank - look for the rank number
                    const rankEl = li.querySelector('.rank, .rankings-page__rank, .rank-column');
                    let rank = rankEl ? rankEl.textContent.trim() : '';
                    if (!rank) {
                        // Try to find a standalone number at the beginning
                        const allText = li.textContent.trim();
                        const rankMatch = allText.match(/^\\s*(\\d{1,3})\\s/);
                        rank = rankMatch ? rankMatch[1] : String(idx + 1);
                    }

                    // Player name
                    const nameLink = li.querySelector('a[href*="/player/"]');
                    const name = nameLink ? nameLink.textContent.trim() : '';

                    // Rating (0.XXXX)
                    const ratingMatch = li.textContent.match(/(0\\.\\d{4})/);
                    const rating = ratingMatch ? ratingMatch[1] : '';

                    // Position
                    const posMatch = li.textContent.match(/\\b(PG|SG|CG|SF|PF|C)\\b/);
                    const position = posMatch ? posMatch[1] : '';

                    // Height/Weight - look for pattern like 6-9 / 230
                    const hwMatch = li.textContent.match(/(\\d+-\\d+)\\s*\\/\\s*(\\d+)/);
                    const height = hwMatch ? hwMatch[1] : '';
                    const weight = hwMatch ? hwMatch[2] : '';

                    // Stars - count star elements or rating images
                    const starEls = li.querySelectorAll('.rankings-page__star, .star-rating-yellow, img[alt*="Star"]');
                    let stars = starEls.length;
                    if (stars === 0) {
                        // Count "Rating Star" text occurrences
                        const starCount = (li.textContent.match(/Rating Star/g) || []).length;
                        stars = Math.min(starCount, 5);
                    }

                    // Status and date
                    const statusEl = li.querySelector('.status, .transfer-status');
                    let status = '';
                    if (statusEl) {
                        status = statusEl.textContent.trim();
                    } else {
                        // Look for date pattern or status text
                        const dateMatch = li.textContent.match(/(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})/);
                        const statusMatch = li.textContent.match(/\\b(Enrolled|Committed|Available|Withdrawn|Entered)\\b/);
                        if (dateMatch) {
                            status = dateMatch[1];
                        } else if (statusMatch) {
                            status = statusMatch[1];
                        }
                    }

                    // Teams - look for school name links
                    const teamLinks = li.querySelectorAll('a[href*="/college/"][href*="transferportal"]');
                    const teamImgs = li.querySelectorAll('img[alt]');
                    let fromTeam = '';
                    let toTeam = '';

                    if (teamLinks.length >= 2) {
                        fromTeam = teamLinks[0].getAttribute('title') || '';
                        toTeam = teamLinks[1].getAttribute('title') || '';
                        // Clean up title text
                        fromTeam = fromTeam.replace(/View \\d+ basketball transfer players for /i, '').trim();
                        toTeam = toTeam.replace(/View \\d+ basketball transfer players for /i, '').trim();
                    }
                    if (!fromTeam && teamLinks.length >= 1) {
                        fromTeam = teamLinks[0].getAttribute('title') || '';
                        fromTeam = fromTeam.replace(/View \\d+ basketball transfer players for /i, '').trim();
                    }

                    // Also try to get date from any date-related elements
                    const allSpans = li.querySelectorAll('span, div');
                    let dateStr = '';
                    allSpans.forEach(el => {
                        const dm = el.textContent.trim().match(/^(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})$/);
                        if (dm) dateStr = dm[1];
                    });
                    if (dateStr && !status.match(/\\d/)) {
                        status = dateStr;
                    }

                    if (name) {
                        results.push({
                            rank: rank,
                            name: name,
                            position: position,
                            height: height,
                            weight: weight,
                            stars: stars,
                            rating: rating,
                            status: status,
                            fromTeam: fromTeam,
                            toTeam: toTeam
                        });
                    }
                } catch(e) {
                    // skip this player
                }
            });
            return results;
        }
        """)

        print(f"Extracted {len(players)} players")

        # If the DOM extraction didn't get dates, try clicking into individual player status areas
        # or look for date in aria/data attributes
        if players and not any(re.search(r'\d+/\d+/\d+', p.get('status', '')) for p in players[:5]):
            print("Dates not found in initial extraction, checking for date elements...")
            # Try alternate date extraction
            dates = await page.evaluate("""
            () => {
                const items = document.querySelectorAll('li');
                const dates = [];
                items.forEach(li => {
                    const playerLink = li.querySelector('a[href*="/player/"]');
                    if (!playerLink) return;
                    // Check for date in data attributes
                    const allEls = li.querySelectorAll('*');
                    let found = '';
                    allEls.forEach(el => {
                        // Check data attributes
                        for (const attr of el.attributes) {
                            const dm = attr.value.match(/(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})/);
                            if (dm) found = dm[1];
                        }
                        // Check title attributes
                        const title = el.getAttribute('title') || '';
                        const tm = title.match(/(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})/);
                        if (tm) found = tm[1];
                    });
                    dates.push(found);
                });
                return dates;
            }
            """)
            # Merge dates if found
            if dates:
                for i, d in enumerate(dates):
                    if i < len(players) and d:
                        players[i]['status'] = d

        # Debug: take a screenshot and save HTML snippet for inspection
        await page.screenshot(path="debug_screenshot.png", full_page=False)

        # Also grab the first player's outer HTML for debugging status/date
        debug_html = await page.evaluate("""
        () => {
            const items = document.querySelectorAll('li');
            for (const li of items) {
                if (li.querySelector('a[href*="/player/"]') && li.textContent.includes('0.9')) {
                    return li.outerHTML.substring(0, 3000);
                }
            }
            return 'No player found';
        }
        """)
        with open("debug_first_player.html", "w") as f:
            f.write(debug_html)
        print("Saved debug HTML of first player element")

        # Write CSV
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Rank", "Player Name", "Position", "Height", "Weight", "Stars", "247 Transfer Rating", "Status", "24/25 Team", "25/26 Team"])
            for p in players:
                writer.writerow([
                    p.get("rank", ""),
                    p.get("name", ""),
                    p.get("position", ""),
                    p.get("height", ""),
                    p.get("weight", ""),
                    p.get("stars", ""),
                    p.get("rating", ""),
                    p.get("status", ""),
                    p.get("fromTeam", ""),
                    p.get("toTeam", ""),
                ])

        print(f"Wrote {len(players)} rows to {output_file}")
        await browser.close()


if __name__ == "__main__":
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    asyncio.run(scrape_transfer_portal(target_count=target))
