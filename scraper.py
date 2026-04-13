import os
import re
from datetime import date, timedelta
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

SESSION_FILE = os.path.join(os.path.dirname(__file__), "session.json")

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
]
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_WEBDRIVER_MASK = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
)

# ── Activity feed extractor ───────────────────────────────────────────────────
# LinkedIn activity page (/recent-activity/all/) renders posts as:
#   <div class="feed-shared-update-v2 ..." role="article" data-urn="urn:li:activity:...">
#     <h2 class="visually-hidden">Feed post number N</h2>
#     ... post content ...
#   </div>
# DOM order = most recent first.
# The data-urn can be used to construct a permalink.
_JS_EXTRACT_POSTS = """
() => {
    const results = [];
    const seen = new Set();

    // Primary: .feed-shared-update-v2[data-urn] — LinkedIn's article container
    let feedItems = Array.from(
        document.querySelectorAll('.feed-shared-update-v2[data-urn]')
    );

    // Fallback A: aria-label on article (older LinkedIn builds)
    if (feedItems.length === 0) {
        feedItems = Array.from(
            document.querySelectorAll('[aria-label^="Feed post number"]')
        );
    }

    // Fallback B: keyboard-navigation hotkey items
    if (feedItems.length === 0) {
        feedItems = Array.from(
            document.querySelectorAll('[data-finite-scroll-hotkey-item]')
        );
    }

    feedItems.forEach((el, idx) => {
        // Strip the visually-hidden "Feed post number N" heading from text
        const raw = (el.innerText || '').trim();
        // Remove leading "Feed post number N" line
        let cleaned = raw.replace(/^Feed post number \\d+\\n?/i, '').trim();
        // Remove duplicate consecutive lines (LinkedIn renders visible + aria text twice)
        const deduped = [];
        const lines = cleaned.split('\\n');
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            // Skip if this line is identical to the previous non-empty line
            if (i > 0 && line && line === (deduped[deduped.length - 1] || '').trim()) continue;
            deduped.push(lines[i]);
        }
        const text = deduped.join('\\n').trim();
        if (text.length < 80 || text.length > 6000) return;
        const key = text.slice(0, 120);
        if (seen.has(key)) return;
        seen.add(key);

        // Relative timestamp (e.g. "3w", "1mo", "2d", "just now")
        const tsMatch = text.match(
            /\\b(\\d+[smhdw]|just now|\\d+\\s+(?:second|minute|hour|day|week|month|year)s?\\s+ago)\\b/i
        );
        const timestamp = tsMatch ? tsMatch[0] : `post ${idx + 1}`;

        // Post permalink — prefer data-urn (reliable), then any /feed/update/ href
        let postUrl = '';
        const urn = el.getAttribute('data-urn') || '';
        if (urn) {
            postUrl = 'https://www.linkedin.com/feed/update/' + encodeURIComponent(urn) + '/';
        } else {
            const candidates = el.querySelectorAll('a[href]');
            for (const a of candidates) {
                const href = a.href || '';
                if (href.includes('linkedin.com') &&
                    (href.includes('/feed/update/') || href.includes('/posts/'))) {
                    postUrl = href.split('?')[0];
                    break;
                }
            }
        }

        results.push({ positionIndex: idx, timestamp, postUrl, text: text.slice(0, 1200) });
    });

    return results.slice(0, 15);
}
"""

# ── Employer + tenure extractor ───────────────────────────────────────────────
# LinkedIn has two experience entry formats:
#
# FORMAT A — single role at a company:
#   Job Title
#   Company Name · Employment Type     ← has ·, not a date
#   Mar 2025 - Present · 1 mo          ← date line (has year or "Present")
#
# LinkedIn 2025 DOM structure uses componentkey="entity-collection-item-..." divs,
# not <ul>/<li>. Each top-level div contains one job entry with text like:
#
#   Title
#   Company · Employment type   (· = middle dot U+00B7)
#   Apr 2025 - Present · 1 yr 1 mo
#   Location (optional)
#   Description...
#
# Or for grouped roles (multiple titles at same company):
#   Title 1
#   Company
#   Nov 2019 - May 2025 · 5 yrs 7 mos
#   ...
_JS_EXTRACT_EMPLOYERS = """
() => {
    // Find Experience section by heading text
    let expSection = null;
    for (const section of document.querySelectorAll('section')) {
        const h2 = section.querySelector('h2');
        if (h2 && /experience/i.test(h2.innerText || '')) {
            expSection = section;
            break;
        }
    }
    if (!expSection) return [];

    const hasYear    = s => /\\b(19|20)\\d{2}\\b/.test(s) || /present/i.test(s);
    const isDurOnly  = s => /\\d+\\s*(yr|mo)/i.test(s) && !hasYear(s);
    const isDateLine = s => hasYear(s) && /\\d{4}/.test(s);
    // Location lines: "City, State · Remote" — have commas AND middle dots
    const isLocation = s => s.includes(',') && (s.includes('\\u00b7') || s.includes('·'));
    // Employment-type line: "Company · Full-time" — has dot but NO comma and NO year
    const isCompanyType = s => {
        const hasDot = s.includes('\\u00b7') || s.includes('·');
        return hasDot && !hasYear(s) && !s.includes(',') && !/^\\d/.test(s.trim());
    };

    // Top-level entity items only (not nested sub-roles)
    const allItems = Array.from(
        expSection.querySelectorAll('[componentkey*="entity-collection-item"]')
    );
    const topItems = allItems.filter(el => {
        let parent = el.parentElement;
        while (parent && parent !== expSection) {
            if ((parent.getAttribute('componentkey') || '').includes('entity-collection-item'))
                return false;
            parent = parent.parentElement;
        }
        return true;
    });

    const results = [];
    const seen = new Set();

    topItems.forEach(item => {
        const rawLines = (item.innerText || '')
            .split('\\n').map(l => l.trim()).filter(Boolean);
        if (rawLines.length < 2) return;

        let company = '';
        let tenure  = '';

        // ── Format B (grouped roles): line[1] is duration-only ──────────────
        // e.g. "Capital One", "1 yr 2 mos", "San Francisco · Remote", "Title 1", "Jul 2025 - Present"
        if (isDurOnly(rawLines[1])) {
            company = rawLines[0];
            // Collect ALL date lines; we want earliest start → latest end
            const dateLines = rawLines.slice(2).filter(isDateLine);
            if (dateLines.length === 1) {
                tenure = dateLines[0].split('\\u00b7')[0].split('·')[0].trim();
            } else if (dateLines.length > 1) {
                // Most-recent role (first date line) has the latest end date
                // Oldest role (last date line) has the earliest start date
                // Strip duration suffix (after ·) from end date
                const rawEnd     = (dateLines[0].split('-')[1] || '').split('\\u00b7')[0].split('·')[0];
                const oldestStart = dateLines[dateLines.length - 1].split('-')[0];
                tenure = (oldestStart.trim() + ' - ' + rawEnd.trim()).replace(/\\s+/g, ' ');
            }

        // ── Format A (single role with "Company · Type" line) ───────────────
        } else {
            let compIdx = -1;
            for (let i = 0; i < Math.min(5, rawLines.length); i++) {
                if (isCompanyType(rawLines[i])) { compIdx = i; break; }
            }

            if (compIdx >= 0) {
                const sep = rawLines[compIdx].includes('\\u00b7') ? '\\u00b7' : '·';
                company = rawLines[compIdx].split(sep)[0].trim();
                for (let j = compIdx + 1; j < Math.min(compIdx + 4, rawLines.length); j++) {
                    if (isDateLine(rawLines[j])) {
                        tenure = rawLines[j].split('\\u00b7')[0].split('·')[0].trim();
                        break;
                    }
                }
            } else {
                // Format A without dot: Title (line 0), Company (line 1, no dot, not date/dur)
                const l1 = rawLines[1] || '';
                if (!isDateLine(l1) && !isDurOnly(l1) && !isLocation(l1) &&
                    l1.length > 0 && l1.length < 100) {
                    company = l1;
                }
                for (let i = 1; i < Math.min(6, rawLines.length); i++) {
                    if (isDateLine(rawLines[i])) {
                        tenure = rawLines[i].split('\\u00b7')[0].split('·')[0].trim();
                        break;
                    }
                }
            }
        }

        if (!company || company.length < 2 || company.length > 100) return;
        if (seen.has(company)) return;
        if (/^\\d/.test(company)) return;
        if (/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/.test(company)) return;

        seen.add(company);
        results.push({ company, tenure });
    });

    return results;
}
"""

# ── Groups extractor ─────────────────────────────────────────────────────────
_JS_EXTRACT_GROUPS = """
() => {
    let groupsSection = null;
    for (const section of document.querySelectorAll('section')) {
        const h2 = section.querySelector('h2');
        if (h2 && /groups/i.test(h2.innerText || '')) {
            groupsSection = section;
            break;
        }
    }
    if (!groupsSection) return [];

    const groups = [];
    const seen = new Set();

    // Group names appear as link text or span text within the section
    groupsSection.querySelectorAll('a, span[aria-hidden="true"]').forEach(el => {
        const text = (el.innerText || el.textContent || '').trim();
        if (text.length > 2 && text.length < 120 && !seen.has(text)) {
            seen.add(text);
            groups.push(text);
        }
    });

    return groups.slice(0, 20);
}
"""


def session_exists() -> bool:
    return os.path.exists(SESSION_FILE)


async def get_logged_in_url() -> "str | None":
    """Return the LinkedIn profile URL of the logged-in user, or None if not logged in.

    Navigates to the feed and extracts the first /in/ link, which LinkedIn
    always places as the user's own profile link in the left-rail.
    """
    if not session_exists():
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            ctx = await browser.new_context(
                user_agent=_USER_AGENT,
                storage_state=SESSION_FILE,
            )
            await ctx.add_init_script(_WEBDRIVER_MASK)
            page = await ctx.new_page()
            await page.goto("https://www.linkedin.com/feed/", wait_until="load", timeout=25000)
            url = await page.evaluate("""
                () => {
                    for (const a of document.querySelectorAll('a[href*="/in/"]')) {
                        const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                        if (href.includes('/in/') && !href.includes('/in/search')) {
                            return href;
                        }
                    }
                    return null;
                }
            """)
            await browser.close()
            if url and "/in/" in url:
                return url
    except Exception:
        pass
    return None


async def save_session() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=_LAUNCH_ARGS)
        context = await browser.new_context(user_agent=_USER_AGENT)
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login")
        await page.wait_for_function(
            """() => {
                const u = window.location.href;
                return u.includes('linkedin.com') &&
                       !u.includes('/login') &&
                       !u.includes('authwall') &&
                       !u.includes('/signup') &&
                       !u.includes('/checkpoint');
            }""",
            timeout=300_000,
        )
        await context.storage_state(path=SESSION_FILE)
        await browser.close()


async def scrape_profile(url: str) -> dict:
    url = url.rstrip("/").split("?")[0]
    activity_url = f"{url}/recent-activity/all/"

    result = {
        "url": url,
        "name": "",
        "headline": "",
        "meta_description": "",
        "full_text": "",
        "external_links": [],
        "employers": [],   # list of {company, tenure}
        "groups": [],
        "recent_activity": "",
        "error": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)

        ctx_kwargs = dict(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        if session_exists():
            ctx_kwargs["storage_state"] = SESSION_FILE

        context = await browser.new_context(**ctx_kwargs)
        await context.add_init_script(_WEBDRIVER_MASK)
        page = await context.new_page()

        try:
            # ── Profile page ──────────────────────────────────────────────
            # Use "load" so all scripts run before we inspect the DOM.
            await page.goto(url, wait_until="load", timeout=45000)
            # Wait for LinkedIn's React to render the Experience heading.
            try:
                await page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('h2,h3')).some("
                    "  el => /experience/i.test(el.innerText || '')"
                    ")",
                    timeout=12000,
                )
            except Exception:
                await page.wait_for_timeout(5000)

            await _scroll(page)

            if _is_auth_wall(page.url):
                result["error"] = (
                    "LinkedIn requires login to view this profile. "
                    "Make sure the profile is public."
                )
                result["name"] = _name_from_url(url)
            else:
                html = await page.content()
                title = await page.title()
                meta = ""
                try:
                    meta = await page.get_attribute('meta[name="description"]', "content") or ""
                except Exception:
                    pass
                result.update(_parse_profile(html, title, meta, url))

                # Expand hidden experience entries before extracting
                await _expand_experience(page)

                # Employers — JS on fully-rendered DOM, then text fallback
                try:
                    employers = await page.evaluate(_JS_EXTRACT_EMPLOYERS)
                    result["employers"] = employers or _employers_from_text(result.get("full_text", ""))
                except Exception:
                    result["employers"] = _employers_from_text(result.get("full_text", ""))

                # Groups
                try:
                    result["groups"] = await page.evaluate(_JS_EXTRACT_GROUPS) or []
                except Exception:
                    result["groups"] = []

            # ── Activity page ─────────────────────────────────────────────
            await page.goto(activity_url, wait_until="load", timeout=45000)
            # Wait until the feed renders. Use wait_for_selector (avoids CSP eval block).
            try:
                await page.wait_for_selector(
                    ".feed-shared-update-v2[data-urn]",
                    timeout=20000,
                )
            except Exception:
                await page.wait_for_timeout(6000)

            if _is_auth_wall(page.url):
                result["recent_activity"] = (
                    "Recent activity not accessible — profile may require login."
                )
            else:
                await _scroll_activity(page)
                try:
                    posts = await page.evaluate(_JS_EXTRACT_POSTS)
                except Exception:
                    posts = []

                if posts:
                    result["recent_activity"] = _format_posts(posts, owner_name=result.get("name", ""))
                else:
                    activity_html = await page.content()
                    result["recent_activity"] = _parse_activity_html(activity_html)

        except Exception as exc:
            result["error"] = str(exc)
        finally:
            await browser.close()

    return result


# ── Scroll helpers ────────────────────────────────────────────────────────────

async def _scroll(page, steps: int = 20) -> None:
    """Scroll the profile page gradually so Intersection Observers fire for each section.

    LinkedIn sets body { overflow: hidden } and scrolls via main#workspace.
    We must scroll that container, not window/body.
    """
    scroll_js = """
    (stepFraction) => {
        const main = document.querySelector('main') || document.documentElement;
        const total = main.scrollHeight;
        main.scrollTo({ top: Math.round(total * stepFraction), behavior: 'instant' });
        return total;
    }
    """
    for i in range(1, steps + 1):
        await page.evaluate(scroll_js, i / steps)
        await page.wait_for_timeout(400)
    # Pause at bottom so lazy sections can finish rendering
    await page.wait_for_timeout(1200)
    # Return to top
    await page.evaluate("(document.querySelector('main') || document.documentElement).scrollTo(0, 0)")
    await page.wait_for_timeout(400)


async def _scroll_activity(page, max_steps: int = 10) -> None:
    """Scroll the activity feed to load more posts."""
    for _ in range(max_steps):
        prev_height = await page.evaluate("""
            () => {
                const main = document.querySelector('main') || document.documentElement;
                return main.scrollHeight;
            }
        """)
        await page.evaluate("""
            () => {
                const main = document.querySelector('main') || document.documentElement;
                main.scrollTo({ top: main.scrollHeight, behavior: 'instant' });
            }
        """)
        await page.wait_for_timeout(1800)
        curr_height = await page.evaluate("""
            () => {
                const main = document.querySelector('main') || document.documentElement;
                return main.scrollHeight;
            }
        """)
        if curr_height == prev_height:
            break
    # Back to top
    await page.evaluate("(document.querySelector('main') || document.documentElement).scrollTo(0, 0)")
    await page.wait_for_timeout(400)


async def _expand_experience(page) -> None:
    """Click 'Show all X experiences' so the full experience list renders."""
    try:
        clicked = await page.evaluate("""
            () => {
                for (const el of document.querySelectorAll('a, button, span')) {
                    const txt = (el.innerText || '').toLowerCase().trim();
                    if (txt.startsWith('show all') && txt.includes('experience')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if clicked:
            await page.wait_for_timeout(2500)
    except Exception:
        pass


# ── Misc helpers ──────────────────────────────────────────────────────────────

def _is_auth_wall(url: str) -> bool:
    return any(k in url for k in ("login", "authwall", "signup", "checkpoint"))


def _name_from_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    return " ".join(w.capitalize() for w in slug.split("-"))


def _resolve_timestamp(ts: str, today: date) -> str:
    """Convert a LinkedIn relative timestamp to an approximate absolute date string.

    LinkedIn uses: "Xs" seconds, "Xm" minutes, "Xh" hours, "Xd" days,
    "Xw" weeks, "Xmo" months, "Xyr" years, "just now".
    Returns a human-readable date like "Apr 12, 2026" or "~Mar 2026".
    """
    ts = ts.strip().lower()
    if not ts or ts.startswith("post "):
        return ts

    # "just now" / very recent
    if "just now" in ts or ts in ("now",):
        return today.strftime("%b %d, %Y")

    # Match patterns like "3w", "1mo", "2yr", "4d", "5h", "30m", "10s"
    m = re.match(r"(\d+)\s*(s|m(?:o|in)?|h(?:r)?|d|w|y(?:r)?)", ts)
    if not m:
        # Already looks like an absolute date or unrecognised — return as-is
        return ts

    n, unit = int(m.group(1)), m.group(2)

    if unit == "s" or unit == "m" or unit == "min" or unit == "h" or unit == "hr":
        # Same day
        return today.strftime("%b %d, %Y")
    elif unit == "d":
        d = today - timedelta(days=n)
        return d.strftime("%b %d, %Y")
    elif unit == "w":
        d = today - timedelta(weeks=n)
        return d.strftime("%b %d, %Y")
    elif unit in ("mo", "mo"):
        # Approximate month subtraction
        month = today.month - n
        year  = today.year + month // 12
        month = month % 12 or 12
        if month <= 0:
            month += 12
            year  -= 1
        try:
            d = today.replace(year=year, month=month)
        except ValueError:
            import calendar
            d = today.replace(year=year, month=month,
                              day=min(today.day, calendar.monthrange(year, month)[1]))
        return d.strftime("%b %Y")
    elif unit in ("y", "yr"):
        try:
            d = today.replace(year=today.year - n)
        except ValueError:
            d = today.replace(year=today.year - n, day=28)
        return d.strftime("%b %Y")

    return ts


def _format_posts(posts: list[dict], owner_name: str = "") -> str:
    """Format scraped posts for the LLM prompt.

    Timestamps are resolved to absolute dates (e.g. "5mo" → "Nov 2025") so the
    LLM receives unambiguous dates it cannot misinterpret as future.
    Each post is labelled with the owner_name so the LLM cannot confuse whose
    activity it is reading.
    """
    lines = []
    seen: set[str] = set()
    today = date.today()
    for post in posts:
        key = post["text"][:120]
        if key in seen:
            continue
        seen.add(key)
        raw_ts  = post.get("timestamp") or f"post {post.get('positionIndex', '?') + 1}"
        ts      = _resolve_timestamp(raw_ts, today)
        url     = post.get("postUrl", "")
        owner   = f" | by {owner_name}" if owner_name else ""
        url_str = f"\nURL: {url}" if url else ""
        lines.append(f"[{ts}{owner}]{url_str}\n{post['text'][:1000]}")
    return "\n\n---\n\n".join(lines) if lines else "No recent activity found."


def _employers_from_text(full_text: str) -> list[dict]:
    """Fallback: parse employer + tenure from the Experience section text."""
    exp_match = re.search(
        r"Experience\n(.*?)(?:\n(?:Education|Skills|Licenses|Volunteer|Groups"
        r"|Languages|Recommendations|Accomplishments|Projects)\n|\Z)",
        full_text,
        re.DOTALL,
    )
    if not exp_match:
        return []

    results: list[dict] = []
    seen: set[str] = set()
    lines = exp_match.group(1).split("\n")

    for i, line in enumerate(lines):
        line = line.strip()
        if "·" not in line:
            continue
        company = line.split("·")[0].strip()
        if (
            len(company) < 2
            or len(company) > 80
            or company in seen
            or re.match(r"^\d", company)
            or re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", company)
        ):
            continue

        tenure = ""
        for j in range(i + 1, min(i + 5, len(lines))):
            nxt = lines[j].strip()
            if re.search(r"\d{4}", nxt) or re.search(r"present", nxt, re.IGNORECASE):
                tenure = nxt.split("·")[0].strip()
                break

        seen.add(company)
        results.append({"company": company, "tenure": tenure})

    return results[:20]


def _parse_profile(html: str, title: str, meta: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    name, headline = "", ""
    clean = title.split(" | ")[0].strip() if " | " in title else title.strip()
    if " - " in clean:
        name, headline = clean.split(" - ", 1)
    else:
        name = clean

    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if (
            href.startswith("http")
            and "linkedin.com" not in href
            and "javascript:" not in href
            and len(href) < 200
        ):
            links.append(href)
    links = list(dict.fromkeys(links))[:8]

    full_text = soup.get_text(separator="\n", strip=True)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)

    return {
        "name": name.strip(),
        "headline": headline.strip(),
        "meta_description": meta,
        "full_text": full_text[:8000],
        "external_links": links,
    }


def _parse_activity_html(html: str) -> str:
    """Last-resort fallback: BeautifulSoup parse of serialised activity HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()

    posts: list[tuple[str, str]] = []
    seen: set[str] = set()

    for time_el in soup.find_all("time", datetime=True):
        iso_ts = time_el.get("datetime", "")
        display = time_el.get_text(strip=True) or iso_ts
        node = time_el
        for _ in range(20):
            node = node.parent
            if node is None:
                break
            text = re.sub(r"\s+", " ", node.get_text(separator=" ", strip=True))
            if 150 < len(text) < 3000:
                key = text[:120]
                if key not in seen:
                    seen.add(key)
                    posts.append((iso_ts, display, text))
                break

    if posts:
        posts.sort(key=lambda x: x[0] or "0000-00-00", reverse=True)
        lines = [f"[{d}]\n{t[:700]}" for _, d, t in posts[:12]]
        return "\n\n---\n\n".join(lines)

    raw = soup.get_text(separator="\n", strip=True)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw[:4000] if raw.strip() else "No recent activity found."
