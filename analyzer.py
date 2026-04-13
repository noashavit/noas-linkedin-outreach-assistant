from datetime import date as _date

def build_prompt(origin: dict, dest: dict) -> str:
    today        = _date.today().strftime("%B %d, %Y")  # e.g. "April 12, 2026"
    origin_name  = origin.get("name") or "the sender"
    dest_name    = dest.get("name") or "the recipient"
    dest_links   = dest.get("external_links") or []
    origin_links = origin.get("external_links") or []

    shared_employers = _list_shared_employers(origin, dest)
    shared_groups    = _list_shared_groups(origin, dest)
    overlap_block    = _overlap_alert(shared_employers, shared_groups, origin_name, dest_name)

    def fmt_employers(employers: list[dict]) -> str:
        if not employers:
            return "not extracted"
        parts = []
        for e in employers:
            t = e.get("tenure", "")
            parts.append(f"{e['company']} ({t})" if t else e["company"])
        return ", ".join(parts)

    def section(label: str, content, cap: int = 4000) -> str:
        return f"{label}:\n{(content or 'Not available')[:cap]}"

    def profile_text(raw) -> str:
        """Strip the Featured section entirely from profile full_text.

        Featured posts are pinned by the user and may be years old. They
        consistently cause the LLM to treat old content as current activity.
        Remove the entire Featured block so only stable profile data reaches
        the LLM (headline, bio, experience, skills, education, etc.).
        """
        import re as _re
        text = (raw or "").strip()
        if not text:
            return "Not available"
        # Remove the Featured block: from 'Featured' heading to the next section
        text = _re.sub(
            r"\bFeatured\b.*?(?=\b(?:Activity|Experience|Education|Skills|"
            r"Recommendations|Licenses|Volunteer|Groups|Languages|Interests|"
            r"Honors|Awards|Projects|Publications)\b)",
            "",
            text,
            flags=_re.DOTALL | _re.IGNORECASE,
        )
        return text.strip()[:4000]

    links_line = (
        f"\nExternal links on their profile: {', '.join(dest_links)}"
        if dest_links else ""
    )

    prompt = f"""ABSOLUTE RULE — READ THIS FIRST, NEVER VIOLATE IT:
You may ONLY state facts that are explicitly written in the profile data provided below.
Do NOT invent, guess, infer, or add ANY detail that is not present verbatim in the data.
This applies to: employer names, company names, job titles, tenure dates, post content,
group memberships, skills, locations, or any other fact.
If something is not in the data, omit it entirely. Never fabricate.
Violation of this rule produces dangerously wrong output.

---

You are a business development expert helping someone connect with new leads on LinkedIn or via email — either for themselves or on behalf of others. Your job is to find the most relevant, timely information about the target that the sender can use to break the ice and have a genuinely useful conversation, online or in person. The ultimate goal is a personalized message that shows real research was done, creates an immediate sense of relevance, and meaningfully increases the chances of getting a meeting or having a great one.

TODAY'S DATE: {today}
Post timestamps use LinkedIn's relative format ("1d", "3w", "2mo"). Subtract the time period from today — e.g. '6mo' means 6 months before today, not after.

Focus first on where the two profiles overlap and on the most recent activity the destination profile made on LinkedIn — that signals what is currently top of mind. Deprioritize old featured posts (6+ months old) entirely; they are background context at best.

{overlap_block}CRITICAL — TWO SEPARATE PEOPLE, TWO SEPARATE ACTIVITY FEEDS:
The prompt contains activity for TWO different people. Each post is labelled "by {origin_name}" or "by {dest_name}".
- DESTINATION INSIGHTS must ONLY discuss posts labelled "by {dest_name}". Never cite a post labelled "by {origin_name}" in this section.
- URLs must match the correct person. Never use a URL from {origin_name}'s activity when writing about {dest_name}, or vice versa.

LinkedIn outreach rules:
- Lead with a genuine observation or connection point — never a request.
- If {dest_name} posted something recently, reference THAT specifically — it outranks shared history.
- Mirror {origin_name}'s authentic voice — vocabulary, cadence, formality level.
- LinkedIn notes: 300 characters max.
- Emails: 3-5 sentences, subject line under 60 characters.
- Banned phrases: "I noticed", "I came across", "I hope this finds you well", "touching base", "synergize", "circle back", "leverage", "reaching out".

RECENCY RULE — apply strictly, no exceptions:
  1. Posts {dest_name} wrote or shared in the last 30 days — always the primary hook
  2. Posts {dest_name} wrote or shared in the last 6 months
  3. Shared employer (current) or direct LinkedIn engagement between both users
  4. Shared employer (past), education, groups
  5. Thematic overlap from profile text — background signal only, never the lead

FEATURED POST RULE:
  LinkedIn "Featured" posts are pinned by the user and may be years old.
  Compare each post's relative timestamp against today ({today}).
  - Only reference posts from within the last 6 months as evidence of current focus.
  - Posts older than 6 months are background context at most — do not lead with them.
  - If no posts are within 6 months, say so explicitly rather than referencing old content.

The activity sections below are listed most-recent-first.
Each post includes a URL — when you reference a specific post in DESTINATION INSIGHTS or CONNECTION POINTS, include that URL in parentheses so the reader can verify it.
Do NOT include URLs in the draft messages themselves (too long).
Read the DESTINATION activity section first before forming any angle.

STRICT FACT RULE — no exceptions:
Only state facts that appear VERBATIM in the profile data below.
Do NOT invent, infer, or embellish any employer name, date, tenure, job title, or company.
If a fact is not explicitly listed below, do not mention it.

---

ORIGIN USER — {origin_name} (sending the message)
Headline: {origin.get('headline', '')}
Bio: {origin.get('meta_description', '')}
Employers (use ONLY these — do not add or invent any others): {fmt_employers(origin.get('employers', []))}
Groups: {', '.join(origin.get('groups', [])) or 'none found'}

{profile_text(origin.get('full_text'))}

{section('Recent LinkedIn activity (most-recent first)', origin.get('recent_activity'), 2500)}

{('External links: ' + ', '.join(origin_links)) if origin_links else ''}

---

DESTINATION USER — {dest_name} (receiving the message)
Headline: {dest.get('headline', '')}
Bio: {dest.get('meta_description', '')}
Employers (use ONLY these — do not add or invent any others): {fmt_employers(dest.get('employers', []))}
Groups: {', '.join(dest.get('groups', [])) or 'none found'}

{profile_text(dest.get('full_text'))}

⬇  RECENT ACTIVITY — READ THIS FIRST — most-recent post listed first  ⬇
The MOST RECENT post is the #1 hook for outreach. Lead with it in DESTINATION INSIGHTS.
Ignore Featured/pinned profile posts — use only what is in this activity feed.
{(dest.get('recent_activity') or 'No recent activity found.')[:2500]}
{links_line}

---

Output your response using EXACTLY these section headers (keep the ## prefix):

## TONE ANALYSIS
Describe {origin_name}'s communication style: vocabulary, sentence structure, formality, distinctive phrases. Quote specific language if visible.

## DESTINATION INSIGHTS
What is {dest_name} focused on RIGHT NOW? Start with the single most-recent post — what was it about, when was it, what does it signal? Then note any persistent themes. Surface external links and what they reveal.

## OVERLAP
{_overlap_section_instruction(shared_employers, shared_groups, origin_name, dest_name)}

## CONNECTION POINTS
Write the actual connection points below — do NOT repeat these instructions.
Focus entirely on {dest_name} (the recipient). List what {dest_name} has done, posted,
or is working on that creates a natural opening, ordered by recency:
1. {dest_name}'s most recent posts/activity (past days or weeks) — highest priority. Include the post URL in parentheses.
2. Thematic alignment between {dest_name}'s work and {origin_name}'s background — label these as "(background signal)".
Do NOT list things {origin_name} has done or posted.


## OUTREACH STRATEGY
{_strategy_instruction(shared_employers, dest_name)}

## LINKEDIN DRAFTS
Before writing, review the OUTREACH STRATEGY and OVERLAP sections you just wrote.
The drafts MUST apply those insights — especially any shared employer identified in OVERLAP.
{_linkedin_draft_angles(shared_employers, origin_name, dest_name)}

LinkedIn connection request notes — plain text ONLY, NO subject line, NO email formatting.
ALL 5 drafts are short LinkedIn notes written BY {origin_name} and sent TO {dest_name}.
— Maximum 300 characters each (LinkedIn's hard limit).
— Start each note with "{dest_name.split()[0]}" (first name only).
— No subject line. No "Subject:". Just the note text.
— Use {origin_name}'s voice and vocabulary from the TONE ANALYSIS.

### Draft 1
[angle: {_draft_angle(shared_employers, dest_name, 1)}]

### Draft 2
[angle: {_draft_angle(shared_employers, dest_name, 2)}]

### Draft 3
[angle: {_draft_angle(shared_employers, dest_name, 3)}]

### Draft 4
[angle: {_draft_angle(shared_employers, dest_name, 4)}]

### Draft 5
[angle: {_draft_angle(shared_employers, dest_name, 5)}]

## EMAIL DRAFTS
Before writing, review the OUTREACH STRATEGY and OVERLAP sections you just wrote.
Apply those insights in the emails — especially any shared employer identified in OVERLAP.
{_email_draft_angles(shared_employers, origin_name, dest_name)}

Cold email drafts — each MUST start with "Subject:" on the first line.
ALL 5 drafts are emails written BY {origin_name} and sent TO {dest_name}.
— Each draft: first line is "Subject: <subject ≤60 chars>", blank line, then 3–5 sentence body.
— Start body with "{dest_name.split()[0]}" (first name).
— Mirror {origin_name}'s voice exactly.

### Draft 1
Subject: [subject ≤60 chars, angle: {_draft_angle(shared_employers, dest_name, 1)}]

[3–5 sentences, angle: {_draft_angle(shared_employers, dest_name, 1)}]

### Draft 2
Subject: [subject ≤60 chars, angle: {_draft_angle(shared_employers, dest_name, 2)}]

[3–5 sentences]

### Draft 3
Subject: [subject ≤60 chars, angle: {_draft_angle(shared_employers, dest_name, 3)}]

[3–5 sentences]

### Draft 4
Subject: [subject ≤60 chars, angle: {_draft_angle(shared_employers, dest_name, 4)}]

[3–5 sentences]

### Draft 5
Subject: [subject ≤60 chars, angle: {_draft_angle(shared_employers, dest_name, 5)}]

[3–5 sentences]

Write all sections. Use ONLY facts explicitly present in the profile data above.
Do NOT invent employer names, dates, tenures, job titles, post content, or any other detail.
If something is not in the data, omit it — never guess or fabricate."""

    return prompt


# ── Shared employer helpers ───────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.lower().strip()


def _list_shared_employers(origin: dict, dest: dict) -> list[dict]:
    """
    Returns list of {company, origin_tenure, dest_tenure} for each shared employer.
    Matching is case-insensitive substring (handles Inc./LLC variants).
    """
    o_emps = origin.get("employers") or []
    d_emps = dest.get("employers") or []
    shared = []

    for oe in o_emps:
        on = _norm(oe["company"])
        for de in d_emps:
            dn = _norm(de["company"])
            if on == dn or (len(on) > 4 and (on in dn or dn in on)):
                shared.append({
                    "company":       oe["company"],
                    "origin_tenure": oe.get("tenure", ""),
                    "dest_tenure":   de.get("tenure", ""),
                })
                break

    return shared


def _list_shared_groups(origin: dict, dest: dict) -> list[str]:
    o_groups = {_norm(g) for g in (origin.get("groups") or [])}
    shared = []
    for g in (dest.get("groups") or []):
        if _norm(g) in o_groups:
            shared.append(g)
    return shared


def _overlap_alert(
    shared_employers: list[dict],
    shared_groups: list[str],
    origin_name: str,
    dest_name: str,
) -> str:
    if not shared_employers and not shared_groups:
        return ""

    lines = ["⚠️  OVERLAP DETECTED — surface this prominently:\n"]

    if shared_employers:
        lines.append("Shared employers:")
        for e in shared_employers:
            ot = f" {e['origin_tenure']}" if e["origin_tenure"] else ""
            dt = f" {e['dest_tenure']}"   if e["dest_tenure"]   else ""
            lines.append(f"  - {e['company']}: {origin_name}{ot} | {dest_name}{dt}")

    if shared_groups:
        lines.append("Shared groups:")
        for g in shared_groups:
            lines.append(f"  - {g}")

    lines.append("")
    return "\n".join(lines) + "\n"


def _overlap_section_instruction(
    shared_employers: list[dict],
    shared_groups: list[str],
    origin_name: str,
    dest_name: str,
) -> str:
    if not shared_employers and not shared_groups:
        return (
            "The system has already checked both profiles for shared employers and groups. "
            "NONE were found. Write exactly: 'No overlap detected.' "
            "Do NOT list any employers or groups — writing any employer name here is fabrication."
        )

    parts = []
    if shared_employers:
        for e in shared_employers:
            ot = e["origin_tenure"] or "dates unknown"
            dt = e["dest_tenure"]   or "dates unknown"
            parts.append(
                f"⚠️  {e['company']}: {origin_name} ({ot}), {dest_name} ({dt})"
            )
    if shared_groups:
        for g in shared_groups:
            parts.append(f"⚠️  Shared group: {g}")

    count = len(parts)
    items = "\n".join(parts)
    return (
        f"There are {count} overlap(s) detected. You MUST list ALL {count} of them — "
        f"do not skip any. For each, include the exact tenure ranges shown and explain "
        f"what the shared history signals about the relationship:\n{items}"
    )


def _draft_angle(shared_employers: list[dict], dest_name: str, draft_num: int) -> str:
    """Return the required angle for a specific draft number."""
    if shared_employers:
        companies = ", ".join(e["company"] for e in shared_employers)
        angles = {
            1: f"shared employer ({companies}) — open with the connection, make it warm not transactional",
            2: f"shared employer ({companies}) combined with {dest_name}'s most recent post",
            3: f"{dest_name}'s most recent post/activity — strongest recency hook",
            4: f"thematic overlap between both profiles' work",
            5: f"different angle on {dest_name}'s recent activity",
        }
    else:
        angles = {
            1: f"{dest_name}'s most recent post — strongest recency hook",
            2: f"second most recent post or theme from {dest_name}'s activity",
            3: f"thematic overlap between both profiles",
            4: f"different angle on {dest_name}'s work or background",
            5: f"another distinct angle from {dest_name}'s profile",
        }
    return angles.get(draft_num, "distinct angle")


def _linkedin_draft_angles(
    shared_employers: list[dict], origin_name: str, dest_name: str
) -> str:
    if shared_employers:
        companies = ", ".join(e["company"] for e in shared_employers)
        return (
            f"SHARED EMPLOYER RULE: {origin_name} and {dest_name} both worked at {companies}. "
            f"Drafts 1 and 2 MUST open with a reference to {companies} — "
            f"e.g. 'We overlapped at {companies}' or 'Fellow {companies} alum here'. "
            f"This is the warmest possible hook and must not be omitted."
        )
    return (
        f"No shared employer found. Lead every draft with {dest_name}'s most recent activity."
    )


def _email_draft_angles(
    shared_employers: list[dict], origin_name: str, dest_name: str
) -> str:
    if shared_employers:
        companies = ", ".join(e["company"] for e in shared_employers)
        return (
            f"SHARED EMPLOYER RULE: {origin_name} and {dest_name} both worked at {companies}. "
            f"Emails 1 and 2 MUST reference {companies} — it is the primary warm-outreach hook. "
            f"The subject line for Draft 1 should reference {companies} directly."
        )
    return (
        f"No shared employer found. Lead every email with {dest_name}'s most recent activity."
    )


def _strategy_instruction(shared_employers: list[dict], dest_name: str) -> str:
    if shared_employers:
        companies = ", ".join(e["company"] for e in shared_employers)
        return (
            f"They share employer(s): {companies}. Lead the strategy with this — "
            f"shared employer context is stronger than any recent post angle. "
            f"2-3 sentences on why this makes the outreach warm, not cold."
        )
    return (
        f"2-3 sentences: the single strongest angle, anchored to the most recent "
        f"thing {dest_name} posted or engaged with. Explain why this hook lands right now."
    )
