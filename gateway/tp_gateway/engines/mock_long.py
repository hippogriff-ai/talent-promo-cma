"""mock-long scenario (CONTRACT.md §8) — realistic-scale scripted run.

Pure DATA for the shared machinery in engines/mock.py: a fictional persona
(Morgan Alvarez, full-stack engineer at Meridian Grid — fictional) applying to
a fictional posting (Orchard Systems — Staff Product Engineer), sized like a
real CMA run: 90+ feed-visible events, a 9-step plan revised 8 times (one step
skipped with a note mid-run, one step ADDED mid-run), 4 blocking questions
(choice/confirm/open/open), 25+ tool events with full-file memory writes, a
transient session.error with recovery, one context compaction, 3 long drafts
whose v1→v2 delta is bullet-glyph/whitespace churn plus EXACTLY 6 wording
edits (the normalized diff must show just the 6), and ~1.5M cumulative input
tokens. The stub judge (tools.py) special-cases this engine: v1 needs_revision
with 3 findings, v2 needs_revision with 1, v3 satisfied with a full rubric.

Everything here must stay deterministic given the canned answers — the golden
fixtures (mock_run_long.jsonl / .snapshot.json) regenerate byte-identically.
"""

from typing import Any

from tp_gateway.engines.mock import KICKOFF, PAUSE, Op, ScriptContext, msg, span, tool, update_plan

# ── persona inputs (used by the fixture generator as resume_text / job_text) ──

PERSONA_RESUME = """Morgan Alvarez
Brooklyn, NY · morgan.alvarez@example.com · (555) 014-7788 · github.com/morganalvarez-dev

Full-stack engineer with nine years building data-heavy web products — the last five at
Meridian Grid, a 400-person grid-analytics company. I own features end to end, schema to
service to UI, and do my best work where ambiguous product questions meet distributed-data
problems.

EXPERIENCE

Meridian Grid — Senior Software Engineer, Platform & Product (2021 – present)

• Tech lead for the outage-prediction pipeline: Kafka ingest of roughly 2.1 billion smart-meter readings a day, a ClickHouse feature store, and a Go model-serving layer; the pipeline backs storm-season alerting for 14 utility customers.
• Led the customer portal rebuild from a jQuery monolith to Next.js with a GraphQL BFF; p75 page load dropped from 4.2s to 1.1s and weekly active operator seats roughly tripled in the two quarters after launch.
- Designed the field-crew mobile app's offline-first sync — CRDT merge over intermittent LTE — so crews in coverage dead zones never lose work; "lost edits" support tickets went from a weekly complaint to near zero.
- Built the billing reconciliation service that diffs meter data against invoice lines and flags drift before statements go out; it now screens every invoice cycle for our three largest customers.
• Co-ran the platform team's incident review process and wrote the postmortem template the org still uses.
- Mentored four mid-level engineers; two were promoted to senior in the following cycle.
• Sat on the interview loop for platform and product-engineering hires (roughly forty onsites).

Meridian Grid — Software Engineer, Product (2019 – 2021)

- Shipped the alerting rules engine — user-defined thresholds compiled to streaming jobs — used by every Meridian customer today.
• Built internal admin tooling for the support team that cut median ticket-resolution time roughly in half.
- Rewrote the meter-data export API from offset paging to keyset pagination, eliminating the timeout class of support escalations.
• Ran the migration of six product services from EC2-classic to ECS with no customer-visible downtime.

Brightleaf Health — Software Engineer (2016 – 2019)

• Built patient-intake and scheduling flows (React, Rails) for a 40-clinic network, replacing a fax-and-spreadsheet workflow that front-desk staff had built up over a decade of workarounds.
- Introduced contract tests between the scheduling monolith and its first extracted service, which caught three breaking changes before they reached a clinic.
• Served on the two-person "keep the lights on" rotation for the legacy billing system, including the quarter where the vendor's SFTP integration failed silently every Sunday night.
- Ran the intern program's project track for two summers; both interns converted to full-time offers.

SELECTED PROJECTS

• Meter-data replay harness (internal): a deterministic replay of any customer's historical meter stream against a candidate pipeline build, used before every ingest release; it is the reason ingest deploys stopped being scary.
- Grid-event annotation UI (internal): a React tool the data-science team uses to label storm events in historical data; built in a two-week gap between roadmap items and still in daily use three years later.
• dotfiles-and-runbooks (personal): a public repo of my shell setup and the runbook templates I bring to every team.

EDUCATION

B.S. Computer Science — SUNY Binghamton, 2016
- Teaching assistant for Data Structures (two semesters); senior project on streaming joins over sensor data.

SKILLS

TypeScript, React, Next.js, Node.js, Go, Python, GraphQL, PostgreSQL, ClickHouse, Kafka,
Redis, Docker, Kubernetes, Terraform, AWS (ECS, RDS, MSK), Datadog, GitHub Actions.
Working style: RFC-first for anything cross-team; instrument before optimizing; postmortems
without blame but with named follow-ups.

TALKS & WRITING

- "Offline-first sync for field crews" — internal engineering summit, 2024
- Meridian Grid engineering blog: "Keyset pagination at meter scale" (2023)
- Postmortem template (internal, 2022) — adopted org-wide; the "what we changed" section is mandatory.

COMMUNITY

• Volunteer mentor, NYC tech-apprenticeship program (2022 – present): resume and interview prep for career changers.
- Occasional answerer of ClickHouse questions in the public community Slack.
"""

PERSONA_JOB = """Orchard Systems — Staff Product Engineer
San Francisco or Remote (US) · Full-time · Product Engineering

About Orchard

Orchard Systems builds the developer platform for climate-hardware fleets: heat pumps,
EV chargers, battery walls, and the long tail of electrified equipment that now sits in
millions of buildings. Fleet operators use Orchard to ingest device telemetry, write
control policies, and ship operator-facing apps without building a data platform first.
We're a Series C company (~160 people, ~45 in engineering) and our product surface is
growing faster than our team — which is exactly why this role exists.

The role

Staff Product Engineers at Orchard own problems, not tickets. You'll take an ambiguous,
cross-cutting product problem — "operators can't trust our alert feed", "onboarding a new
fleet takes three weeks of solutions engineering" — and drive it from discovery through
design, implementation, rollout, and operation. You'll write a meaningful amount of code
every week, and you'll multiply the twelve product engineers around you through design
review, pairing, and the standards you set.

What you'll do

- Own one of our three product pillars (ingest & data quality, policy engine, operator
  apps) end to end, including its roadmap trade-offs and its on-call health.
- Design and build features that span the stack: TypeScript/React front ends, Node and
  Go services, event streams (Kafka), and a Postgres + ClickHouse data layer.
- Run what you ship. Engineers at Orchard carry the pager for their systems — you'll be
  in the product-engineering on-call rotation from your second month.
- Turn messy operational reality into product: instrument, measure, and close the loop
  on data quality issues that today reach customers as confusing alerts.
- Level up the team: design reviews, RFCs, interview loop, and mentoring two to three
  mid-level engineers.
- Work directly with fleet operators (our customers) during discovery and incidents.

What we're looking for

- 8+ years building production web products, with depth on both front end and services;
  you should be equally credible in a React performance review and a schema design review.
- You've owned a system in production for years, not quarters: you can tell us about the
  pager, the postmortems, and what you changed so the same page never fired twice.
- Evidence of Staff-level scope: a cross-team problem you drove without formal authority,
  and engineers who got better because you were around.
- Fluency with event-streaming architectures (Kafka or equivalent) and analytical stores
  (ClickHouse, BigQuery, or similar) at real scale.
- You write clearly. RFCs, postmortems, and even commit messages are part of how you
  multiply a team.
- Comfort with ambiguity: you ask the questions that turn "make alerts better" into a
  shippable, measurable plan.

Nice to have

- Experience with time-series or IoT/telemetry data at billions-of-events-per-day scale.
- Prior work in energy, climate, or another physical-infrastructure domain.
- Offline-first or intermittent-connectivity product experience.
- Public writing or talks about systems you've run.

Your first six months

- Month 1: ship something small to production in week one (everyone does); shadow the
  on-call; read the last quarter's postmortems and RFCs.
- Months 2–3: join the rotation; take ownership of one pillar's health dashboard and
  propose the quarter's reliability investment with data.
- Months 4–6: drive one cross-team product problem end to end — discovery with three
  fleet operators, an RFC, a rollout plan, and the operational follow-through.

How we work

- RFCs for anything that crosses a team boundary; decisions are written down or they
  didn't happen.
- Weekly demos over status meetings; we show working software, including the failures.
- Every incident gets a postmortem with a named "what we changed"; repeat pages are
  treated as product defects, not operational noise.
- Product engineers talk to customers directly — no telephone game through PM.

What we offer

- $210k–$260k base (US), meaningful early-stage equity, 401(k) match.
- Health, dental, vision; 20 days PTO plus a company-wide winter shutdown.
- Remote-first with quarterly in-person weeks (SF or Denver).
- A real on-call: compensated, rotated fairly, and quiet — because we fix root causes.

Interview process

Recruiter screen (30m) → hiring-manager deep dive (60m) → practical exercise in your own
editor (90m, no leetcode) → virtual onsite: system design, product sense, code review,
and a values conversation (4h total) → references and offer.

Orchard Systems is an equal-opportunity employer. We hire people, not keyword lists, and
we'd rather see the three projects you're proud of than a resume tuned for a parser.
"""

# ── canned interview answers (fixtures + tests; the web e2e types its own) ────

CANNED_ANSWERS: list[str] = [
    "Outage-prediction pipeline",
    "Yes — four engineers, and it ran about eleven months end to end.",
    "Alert precision was 91% at the P1 threshold last quarter, and ingest availability held "
    "99.95% over the trailing year — both numbers are on the internal reliability dashboard.",
    "I've been in the weekly on-call rotation for three years — roughly 40 real pages. I ran "
    "the postmortem for the 2024 ingest outage (four hours of lost readings) and built the "
    "backfill tooling that turned that class of incident into a non-event.",
]

# ── plan (9 steps once "quantify" is added mid-run; "portfolio" gets skipped) ─

_STEP_TITLES = {
    "ingest": "Ingest resume & job posting",
    "research": "Research Orchard Systems",
    "jd-map": "Map JD requirements to evidence",
    "portfolio": "Review public portfolio & writing",
    "interview": "Gap-driven discovery interview",
    "quantify": "Quantify the anchor project",
    "draft": "Draft the tailored resume",
    "review": "Grounding review & revision",
    "deliver": "Deliver final versions",
}

_ORDER_8 = ["ingest", "research", "jd-map", "portfolio", "interview", "draft", "review", "deliver"]
_ORDER_9 = ["ingest", "research", "jd-map", "portfolio", "interview", "quantify", "draft", "review", "deliver"]

SKIPPED_STEP_NOTE = "No public portfolio or talks found beyond two internal items — JD marks it nice-to-have; skipping."
ADDED_STEP_NOTE = "Added mid-run: interview surfaced dashboard metrics that deserve their own pass."


def _plan_rev(order: list[str], statuses: dict[str, Any], current: str | None) -> Op:
    steps = []
    for sid in order:
        st = statuses[sid]
        if isinstance(st, tuple):
            steps.append({"id": sid, "title": _STEP_TITLES[sid], "status": st[0], "note": st[1]})
        else:
            steps.append({"id": sid, "title": _STEP_TITLES[sid], "status": st})
    return update_plan({"steps": steps, "current_step_id": current})


_SKIP = ("skipped", SKIPPED_STEP_NOTE)

_PLAN_REVS: list[Op] = [
    # rev0 — initial 8-step plan
    _plan_rev(
        _ORDER_8,
        {
            "ingest": "done",
            "research": "active",
            "jd-map": "pending",
            "portfolio": "pending",
            "interview": "pending",
            "draft": "pending",
            "review": "pending",
            "deliver": "pending",
        },
        "research",
    ),
    # rev1 — research done, jd-map active
    _plan_rev(
        _ORDER_8,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "active",
            "portfolio": "pending",
            "interview": "pending",
            "draft": "pending",
            "review": "pending",
            "deliver": "pending",
        },
        "jd-map",
    ),
    # rev2 — jd-map done; portfolio SKIPPED with a note; interview active
    _plan_rev(
        _ORDER_8,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "active",
            "draft": "pending",
            "review": "pending",
            "deliver": "pending",
        },
        "interview",
    ),
    # rev3 — mid-interview: "quantify" step ADDED (now 9 steps)
    _plan_rev(
        _ORDER_9,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "active",
            "quantify": ("pending", ADDED_STEP_NOTE),
            "draft": "pending",
            "review": "pending",
            "deliver": "pending",
        },
        "interview",
    ),
    # rev4 — interview done, quantify active
    _plan_rev(
        _ORDER_9,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "done",
            "quantify": ("active", ADDED_STEP_NOTE),
            "draft": "pending",
            "review": "pending",
            "deliver": "pending",
        },
        "quantify",
    ),
    # rev5 — quantify done, draft active
    _plan_rev(
        _ORDER_9,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "done",
            "quantify": ("done", ADDED_STEP_NOTE),
            "draft": "active",
            "review": "pending",
            "deliver": "pending",
        },
        "draft",
    ),
    # rev6 — draft submitted, review active
    _plan_rev(
        _ORDER_9,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "done",
            "quantify": ("done", ADDED_STEP_NOTE),
            "draft": "done",
            "review": "active",
            "deliver": "pending",
        },
        "review",
    ),
    # rev7 — review passed, deliver active
    _plan_rev(
        _ORDER_9,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "done",
            "quantify": ("done", ADDED_STEP_NOTE),
            "draft": "done",
            "review": "done",
            "deliver": "active",
        },
        "deliver",
    ),
    # rev8 — all done
    _plan_rev(
        _ORDER_9,
        {
            "ingest": "done",
            "research": "done",
            "jd-map": "done",
            "portfolio": _SKIP,
            "interview": "done",
            "quantify": ("done", ADDED_STEP_NOTE),
            "draft": "done",
            "review": "done",
            "deliver": "done",
        },
        None,
    ),
]

# ── drafts ────────────────────────────────────────────────────────────────────
# V1 is the canonical template. V2 = V1 with bullet-glyph churn on every
# untouched bullet line + EXACTLY the 6 wording edits below (same line count),
# so the normalized diff (talent-promo-web components/diff.ts) shows just the
# 6. V3 = V2 with a final polish pass. {ANCHOR} / {INCIDENT} are substituted
# from the interview answers at emit time.

_V1_TEMPLATE: list[str] = [
    "# Morgan Alvarez",
    "",
    "Brooklyn, NY · morgan.alvarez@example.com · (555) 014-7788 · github.com/morganalvarez-dev",
    "",
    "Staff-level full-stack product engineer (nine years) who ships data-heavy platforms end to end — signature work: {ANCHOR}.",
    "",
    "## Why Orchard Systems",
    "",
    "- Orchard's platform problem — one clean developer surface over messy fleet telemetry — is the shape of problem I've spent five years solving at Meridian Grid.",
    "- I run what I build: I've carried the pager, written the postmortems, and hardened the pipelines afterward.",
    "- I scale through other engineers — mentoring, design review, and the hiring loop — which is the Staff half of this role.",
    "",
    "## Experience",
    "",
    "### Meridian Grid — Senior Software Engineer, Platform & Product (2021 – present)",
    "",
    "• Tech lead for the outage-prediction pipeline behind storm-season alerting trusted by every major utility on the eastern seaboard — Kafka ingest of 2.1 billion smart-meter readings a day, ClickHouse feature store, Go model-serving layer.",
    "• Built the billing reconciliation service that diffs meter data against invoice lines; it now screens every invoice cycle for our 3 largest customers.",
    "• Made the customer portal roughly 4x faster for thousands of daily operators after the rebuild.",
    "• Led the customer portal rebuild from a jQuery monolith to Next.js with a GraphQL BFF.",
    "• Weekly active operator seats roughly tripled in the 2 quarters after the portal relaunch.",
    "• Scaled ingest to five-nines durability with zero data loss since the 2021 launch.",
    "• Alert precision consistently above ninety percent on outage predictions.",
    "- Designed the field-crew app's offline-first sync — CRDT merge over intermittent LTE — so crews in dead zones never lose work; lost-edit tickets went from weekly to near zero.",
    "- Co-ran the platform team's incident review process.",
    "- Mentored engineers across the organization.",
    "",
    "### Meridian Grid — Software Engineer, Product (2019 – 2021)",
    "",
    "- Shipped the alerting rules engine — user-defined thresholds compiled to streaming jobs — used by every Meridian customer today.",
    "- Built internal admin tooling that cut median ticket-resolution time roughly in half (from 9 days).",
    "- Rewrote the meter-data export API from offset paging to keyset pagination, eliminating the timeout class of support escalations.",
    "- Ran the migration of six product services from EC2-classic to ECS with no customer-visible downtime.",
    "",
    "## Operational ownership",
    "",
    "- {INCIDENT}",
    "- Comfortable being named on an alert: I treat a page as a product bug in the making, not an interruption.",
    "",
    "## How I work",
    "",
    "- Discovery before code: I turn 'make the alerts better' into a measurable plan, and I write the RFC that says what we are deliberately not doing.",
    "- Instrument, then argue: the portal rebuild shipped behind flags with p75 dashboards live from week one, so rollout debates were about numbers, not vibes.",
    "- Blameless but specific: my postmortems name systems and decisions, never people — and each one ends with the change that retires the failure class.",
    "- Writing is the multiplier: RFCs, review notes, and the postmortem template are how one engineer's judgment scales past their calendar.",
    "",
    "## Selected stack depth",
    "",
    "- Streaming & storage: Kafka (MSK), ClickHouse, PostgreSQL, Redis — from schema design down to query-plan debugging.",
    "- Product surface: TypeScript, React, Next.js, Node, GraphQL BFFs; Go for services where the allocator matters.",
    "",
    "### Brightleaf Health — Software Engineer (2016 – 2019)",
    "",
    "• Built patient-intake and scheduling flows (React, Rails) for a 40-clinic network.",
    "• Introduced contract tests between the scheduling monolith and its first extracted service.",
    "• Served on the two-person keep-the-lights-on rotation for the legacy billing system.",
    "",
    "## Writing & talks",
    "",
    "- \"Offline-first sync for field crews\" — internal engineering summit, 2024.",
    "- Meridian Grid engineering blog: \"Keyset pagination at meter scale\" (2023).",
    "",
    "## Skills",
    "",
    "TypeScript, React, Next.js, Node.js, Go, Python, GraphQL, PostgreSQL, ClickHouse, Kafka,",
    "Redis, Docker, Kubernetes, Terraform, AWS (ECS, RDS, MSK), Datadog, GitHub Actions.",
    "",
    "## Education",
    "",
    "B.S. Computer Science — SUNY Binghamton, 2016.",
]

# The SIX wording edits v1 → v2 (old line must match a template line exactly).
_EDITS_V1_TO_V2: list[tuple[str, str]] = [
    (
        "• Tech lead for the outage-prediction pipeline behind storm-season alerting trusted by every major utility on the eastern seaboard — Kafka ingest of 2.1 billion smart-meter readings a day, ClickHouse feature store, Go model-serving layer.",
        "• Tech lead for the outage-prediction pipeline behind storm-season alerting for 14 utility customers — Kafka ingest of 2.1 billion smart-meter readings a day, ClickHouse feature store, Go model-serving layer.",
    ),
    (
        "• Made the customer portal roughly 4x faster for thousands of daily operators after the rebuild.",
        "• Cut portal p75 page load from 4.2s to 1.1s in the rebuild, measured across all operator dashboards.",
    ),
    (
        "• Scaled ingest to five-nines durability with zero data loss since the 2021 launch.",
        "• Held 99.95% ingest availability over the trailing year, per the internal reliability dashboard.",
    ),
    (
        "• Led the customer portal rebuild from a jQuery monolith to Next.js with a GraphQL BFF.",
        "• Led a four-engineer team through the eleven-month customer portal rebuild — jQuery monolith to Next.js with a GraphQL BFF.",
    ),
    (
        "- Mentored engineers across the organization.",
        "- Mentored four mid-level engineers; two were promoted to senior in the following cycle.",
    ),
    (
        "• Alert precision consistently above ninety percent on outage predictions.",
        "• 91% alert precision at the P1 threshold last quarter on outage predictions.",
    ),
]

# The polish pass v2 → v3 (old lines are v2 lines: churned-or-edited v1 lines).
_EDITS_V2_TO_V3: list[tuple[str, str]] = [
    (
        # fixes the round-2 finding: invented "(from 9 days)" baseline
        "• Built internal admin tooling that cut median ticket-resolution time roughly in half (from 9 days).",
        "• Built internal admin tooling that cut median ticket-resolution time roughly in half.",
    ),
    (
        "• Co-ran the platform team's incident review process.",
        "• Co-ran the platform team's incident review process and wrote the postmortem template the org still uses.",
    ),
    (
        "• Comfortable being named on an alert: I treat a page as a product bug in the making, not an interruption.",
        "• Comfortable being named on an alert: a page is a product bug in the making, and the fix belongs to whoever answered it.",
    ),
    (
        "• Orchard's platform problem — one clean developer surface over messy fleet telemetry — is the shape of problem I've spent five years solving at Meridian Grid.",
        "• Orchard's platform problem — one clean developer surface over messy fleet telemetry — is the problem shape I've spent five years on at Meridian Grid, at 2.1 billion readings a day.",
    ),
    (
        "• I scale through other engineers — mentoring, design review, and the hiring loop — which is the Staff half of this role.",
        "• I scale through other engineers — mentoring, design reviews, RFCs, and roughly forty onsite interviews — which is the Staff half of this role.",
    ),
]


def _churn(line: str) -> str:
    """Cosmetic-only churn: flip the bullet glyph. The web diff normalizer must
    fold these to context rows (CONTRACT §8 mock-long)."""
    if line.startswith("• "):
        return "- " + line[2:]
    if line.startswith("- "):
        return "• " + line[2:]
    return line


def _apply_edits(lines: list[str], edits: list[tuple[str, str]]) -> tuple[list[str], set[int]]:
    out = list(lines)
    touched: set[int] = set()
    for old, new in edits:
        matches = [i for i, ln in enumerate(out) if ln == old]
        if len(matches) != 1:  # pragma: no cover — scenario authoring error
            raise ValueError(f"edit target not unique ({len(matches)} matches): {old!r}")
        out[matches[0]] = new
        touched.add(matches[0])
    return out, touched


def _v2_template() -> list[str]:
    edited, touched = _apply_edits(_V1_TEMPLATE, _EDITS_V1_TO_V2)
    # churn every line EXCEPT the 6 edited ones (whitespace churn on two prose
    # lines; glyph churn on the bullets)
    out = []
    for i, ln in enumerate(edited):
        if i in touched:
            out.append(ln)
        elif ln.startswith("## "):
            out.append("##  " + ln[3:])  # doubled space — collapses under normalization
        else:
            out.append(_churn(ln))
    return out


_V2_TEMPLATE = _v2_template()
_V3_TEMPLATE, _ = _apply_edits(_V2_TEMPLATE, _EDITS_V2_TO_V3)


def _fill(lines: list[str], ctx: ScriptContext) -> str:
    text = "\n".join(lines) + "\n"
    return text.replace("{ANCHOR}", ctx.answers[0]).replace("{INCIDENT}", ctx.answers[3])


def draft_v1(ctx: ScriptContext) -> str:
    return _fill(_V1_TEMPLATE, ctx)


def draft_v2(ctx: ScriptContext) -> str:
    return _fill(_V2_TEMPLATE, ctx)


def draft_v3(ctx: ScriptContext) -> str:
    return _fill(_V3_TEMPLATE, ctx)


# ── interview questions ───────────────────────────────────────────────────────

QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "Which Meridian Grid project should anchor this resume as your signature impact story?",
        "context": (
            "Orchard's JD asks for one thing above all: evidence you can own an ambiguous, "
            "cross-cutting platform problem end to end. Your resume lists four candidate "
            "stories — the outage-prediction pipeline, the customer portal rebuild, the "
            "field-crew offline sync, and the billing reconciliation service — but a Staff "
            "resume needs ONE story told deeply, with the others in support. The pipeline "
            "shows scale and streaming depth (their ingest pillar); the portal shows a "
            "team-lead arc and product sense; offline sync is the most technically novel and "
            "maps to their nice-to-have; billing reconciliation is the most business-legible. "
            "I'll structure the whole experience section around whichever you pick, so choose "
            "the one you can talk about for forty-five minutes without notes."
        ),
        "kind": "choice",
        "options": [
            "Outage-prediction pipeline",
            "Customer portal rebuild",
            "Field-crew offline sync",
            "Billing reconciliation service",
        ],
    },
    {
        "question": (
            "Your resume says you 'led the customer portal rebuild'. Before I put a team-lead "
            "claim in a Staff resume: is it accurate that you led a team of four engineers "
            "through roughly eleven months of that rebuild?"
        ),
        "context": (
            "Scope conflation — 'led' meaning 'was the senior person nearby' — is the single "
            "most common failure the grounding review flags, and Staff-level screens always "
            "drill into team size and duration. If you confirm, I'll write 'led a "
            "four-engineer team through the eleven-month rebuild' and defend it; if the real "
            "shape was different, answer in your own words and I'll phrase exactly what you "
            "did instead — smaller-but-true beats bigger-but-wobbly in every loop I've seen."
        ),
        "kind": "confirm",
    },
    {
        "question": (
            "The outage-prediction pipeline needs one number an interviewer can push on. Do "
            "you know — even approximately — the alert precision, ingest availability, or "
            "another operational metric you'd defend out loud?"
        ),
        "context": (
            "The current bullet says the pipeline 'backs storm-season alerting for 14 utility "
            "customers', which is good but static. Staff resumes live and die on operational "
            "numbers the candidate personally stands behind — and the grounding review will "
            "cut any metric that doesn't trace to your resume or to your answers here, so I "
            "can only use what you give me. If you have a dashboard number you trust, give it "
            "with its qualifier (threshold, time window, data source); if you don't, say so "
            "and I'll keep the bullet qualitative rather than invent precision."
        ),
        "kind": "open",
    },
    {
        "question": (
            "Orchard's posting is blunt about operational ownership — 'you run what you ship' "
            "and a named on-call rotation. Your resume never says on-call. What are your real "
            "operational scars at Meridian: pages you took, an incident you ran end to end, "
            "and what changed because of you afterward?"
        ),
        "context": (
            "This is the biggest JD-to-resume gap the analysis found. 'Co-ran the incident "
            "review process' describes process, not ownership — a hiring manager reading it "
            "will assume you facilitated meetings. One concrete incident with your "
            "fingerprints on the fix is worth a whole section of adjectives; whatever you "
            "tell me becomes a first-class bullet under an 'Operational ownership' heading, "
            "phrased only from your words so it survives the grounding review."
        ),
        "kind": "open",
    },
]

# ── memory file contents (full files — tool inputs are deliberately long) ─────

_PROFILE_MD = """# Candidate profile — Morgan Alvarez

Source: pasted resume, verbatim ingest. Confidence notes inline.

## Identity
- Full-stack engineer, 9 years total; 5 at Meridian Grid (grid analytics, ~400 people).
- Self-described sweet spot: ambiguous product questions + distributed-data problems.

## Strongest evidence (verbatim-backed)
- Outage-prediction pipeline: tech lead; Kafka ingest ~2.1B smart-meter readings/day;
  ClickHouse feature store; Go serving layer; backs storm-season alerting for 14 utilities.
- Customer portal rebuild: jQuery monolith -> Next.js + GraphQL BFF; p75 4.2s -> 1.1s;
  weekly active operator seats ~3x in two quarters. Resume says "led" — VERIFY scope.
- Field-crew offline sync: CRDT merge over intermittent LTE; lost-edit tickets -> ~zero.
- Billing reconciliation service: screens every invoice cycle, three largest customers.
- Mentoring: four mid-level engineers, two promoted. Interview loop: ~40 onsites.

## Gaps / unknowns (candidates for ask_user)
- No on-call or incident-ownership language anywhere on the resume. JD requires it.
- No operational metrics the candidate personally owns (precision? availability?).
- "Led" the portal rebuild — team size and duration unstated.
- No public portfolio links beyond one internal talk and one blog post.

## Tone notes
- Resume voice is concrete and unhedged; keep that. Avoid superlatives it never uses.
"""

_ORCHARD_OVERVIEW_MD = """# Research — Orchard Systems (fictional target)

## Company
- Developer platform for climate-hardware fleets: heat pumps, EV chargers, batteries.
- Series C, ~160 people, ~45 engineering, product engineering ~12 (per JD text).
- Three product pillars named in the JD: ingest & data quality, policy engine,
  operator apps.

## What they sell (synthesized from posting language)
- "Ingest device telemetry, write control policies, ship operator-facing apps without
  building a data platform first" — i.e., they ARE the data platform. Candidates who
  have BUILT such a platform internally are the bullseye.

## Engineering culture signals
- "Own problems, not tickets" / "run what you ship" / compensated quiet on-call —
  operational ownership is a first-class hiring bar, not a perk disclaimer.
- "We hire people, not keyword lists" + editor-based practical — resume should read
  as narrative evidence, not ATS keyword soup. Keep keywords but in sentences.

## Stack overlap with candidate
- TypeScript/React front ends: direct match (portal rebuild).
- Node + Go services: Go serving layer at Meridian; Node via BFF.
- Kafka event streams: direct match at 2.1B readings/day scale.
- Postgres + ClickHouse: direct match (feature store).
- Telemetry at billions-of-events/day: direct match — lead with this.

## Positioning
- Anchor story should be the pillar-shaped one. Pipeline maps to "ingest & data
  quality"; portal maps to "operator apps". Ask the candidate which they can defend
  deepest — do not choose for them.
"""

_JD_REQUIREMENTS_MD = """# JD requirement extraction — Orchard Staff Product Engineer

Requirements ranked by how hard the JD leans on them (R1 = hardest).

R1  Operational ownership: pager, postmortems, "same page never fired twice".
    Resume evidence: WEAK — incident review process co-run is process-adjacent.
R2  Staff-level scope without formal authority; multiplying ~12 product engineers.
    Resume evidence: MEDIUM — mentoring (4/2 promoted), interview loop, template org
    still uses. Needs one cross-team story told as a story.
R3  Full-stack depth (React credibility AND schema credibility).
    Resume evidence: STRONG — portal rebuild + feature-store design.
R4  Event streaming at scale (Kafka) + analytical stores (ClickHouse).
    Resume evidence: STRONG — 2.1B readings/day pipeline. Lead with it.
R5  Ambiguity-to-plan skill ("make alerts better" -> measurable plan).
    Resume evidence: MEDIUM — implied by tech-lead role; make explicit in summary.
R6  Clear writing (RFCs, postmortems).
    Resume evidence: MEDIUM — blog post + postmortem template; cite both.
R7  Telemetry/IoT at billions/day (nice-to-have).
    Resume evidence: STRONG — direct.
R8  Offline/intermittent connectivity (nice-to-have).
    Resume evidence: STRONG — field-crew CRDT sync.
R9  Climate/energy domain (nice-to-have).
    Resume evidence: STRONG — five years of grid analytics.
R10 Public writing/talks (nice-to-have).
    Resume evidence: WEAK — one internal talk, one blog post. Do not overclaim.

Plan implications: interview must close R1 (operational ownership) and de-risk the
"led" claim inside R2. R4/R7/R8/R9 are the differentiators — surface early.
"""

_GAP_ANALYSIS_MD = """# Gap analysis — Morgan Alvarez -> Orchard Staff Product Engineer

## Gap 1 (blocking): operational ownership language
JD: "you'll be in the on-call rotation from your second month"; wants pager scars.
Resume: zero mentions of on-call, pages, or incidents owned. The incident-review
bullet reads as facilitation. RESOLUTION: ask directly (interview Q4). Do not draft
around it — a Staff resume without this section is dead on arrival at Orchard.

## Gap 2 (blocking): unquantified anchor claims
"Trusted by every major utility on the eastern seaboard" cannot be sourced. The
resume itself says 14 utility customers — use that. Precision/availability numbers
exist only if the candidate owns them personally (interview Q3).

## Gap 3 (risk): scope of "led the customer portal rebuild"
Team size and duration unstated. If it was 2 people for 3 months, the Staff story
changes shape. RESOLUTION: confirm the four-engineer / eleven-month reading (Q2)
or re-phrase to the true shape.

## Gap 4 (structural): four competing stories, no anchor
Resume gives pipeline, portal, offline sync, and billing equal billing. Orchard's
pillar structure rewards ONE deep story. RESOLUTION: candidate picks (Q1); the
other three become supporting bullets with one line each.

## Non-gaps (do not ask, do not pad)
- Stack overlap is excellent; skills section needs no additions, only ordering.
- Domain fit (energy/climate) is inherent; one line in the summary suffices.
- Portfolio: two artifacts exist; JD marks it nice-to-have. Skipping the portfolio
  review step rather than manufacturing links.
"""


def _interview_log(through: int) -> str:
    entries = [
        (
            "## Q1 — anchor project (choice)\n"
            "ASKED: which of four projects anchors the resume.\n"
            "ANSWER: {A1}\n"
            "USE: restructure experience section around this; others get one line each."
        ),
        (
            "## Q2 — portal rebuild scope (confirm)\n"
            "ASKED: confirm 'led a four-engineer team, ~eleven months'.\n"
            "ANSWER: {A2}\n"
            "USE: portal bullet may carry team size + duration, verbatim-grounded."
        ),
        (
            "## Q3 — anchor metrics (open)\n"
            "ASKED: one operational number the candidate will defend out loud.\n"
            "ANSWER: {A3}\n"
            "USE: precision + availability figures usable WITH qualifiers; cite dashboard."
        ),
        (
            "## Q4 — operational scars (open)\n"
            "ASKED: pages taken, incident run end to end, what changed after.\n"
            "ANSWER: {A4}\n"
            "USE: becomes the 'Operational ownership' section verbatim-grounded; closes R1."
        ),
    ]
    return "# Interview log\n\n" + "\n\n".join(entries[:through]) + "\n"


def _interview_log_op(through: int) -> Op:
    def payload(ctx: ScriptContext) -> dict[str, Any]:
        content = _interview_log(through)
        for i, a in enumerate(ctx.answers[:through], start=1):
            content = content.replace("{A" + str(i) + "}", a)
        return {
            "name": "memory_write",
            "input": {"path": "coach/interview_log.md", "content": content},
            "tool_use_id": f"mocktool_l_log{through}",
        }

    return ("emit", "agent.tool_use", payload)


_DRAFT_NOTES_MD = """# Draft notes — structure decisions

1. Summary line carries: years, "end to end", anchor project by name. No adjectives.
2. "Why Orchard Systems" section (3 bullets): platform-shape match, operational
   ownership, force multiplication. Mirrors the JD's own priority order R1/R2.
3. Experience: anchor project first with metrics from Q3 (with qualifiers), then
   portal (scope per Q2), then offline sync + billing as single-line support.
4. NEW "Operational ownership" section from Q4's answer, phrased from their words
   only — this is the section Orchard's JD is actually hiring for.
5. Writing & talks: the two real artifacts, dated, no padding (R10 stays honest).
6. Keywords appear inside claim sentences (Kafka, ClickHouse, GraphQL, on-call) —
   Orchard explicitly dislikes keyword lists (posting: "people, not keyword lists").
"""

# ── research phase data (14 searches, 10 fetches, per-topic notes) ────────────

_RESEARCH: list[dict[str, Any]] = [
    {
        "query": "Orchard Systems climate hardware developer platform",
        "url": "https://orchardsystems.example/product",
        "note": (
            "Orchard sells the platform layer, not the hardware.\n"
            "Their product page frames the buyer as the fleet operator's engineering team — "
            "people who would otherwise build ingest, storage, and alerting themselves. That "
            "makes your Meridian work almost embarrassingly on-target: you've spent five years "
            "being their customer archetype and their product team at once.\n\n"
            "I'm noting their vocabulary — 'fleets', 'policies', 'operator apps' — so the "
            "resume can speak it without keyword-stuffing."
        ),
    },
    {
        "query": "Orchard Systems series C funding announcement",
        "url": "https://orchardsystems.example/blog/series-c",
        "note": (
            "Series C confirmed at ~160 headcount.\n"
            "The funding post says the round funds 'the policy engine and enterprise fleet "
            "onboarding'. Enterprise onboarding pain is mentioned twice — your billing "
            "reconciliation and export-API war stories both read as onboarding-adjacent "
            "credibility if we need a supporting bullet."
        ),
    },
    {
        "query": "Orchard Systems engineering blog",
        "url": "https://orchardsystems.example/blog/engineering",
        "note": (
            "Engineering blog exists and is substantive.\n"
            "Recent posts: ClickHouse migration write-up, an on-call principles post ('quiet "
            "pager is a feature'), and a post on schema evolution for device telemetry. The "
            "on-call post confirms the JD's operational-ownership bar is cultural, not HR "
            "copy. A resume that says 'carried the pager' in plain words will land."
        ),
    },
    {
        "query": "Orchard Systems staff product engineer role expectations",
        "url": None,
        "note": (
            "Staff at Orchard means pillar ownership.\n"
            "Cross-referencing the JD against their careers page: Staff engineers own one of "
            "three named pillars end to end including 'its on-call health'. This is why the "
            "anchor-project decision matters — I'll ask you to pick rather than hedge across "
            "four stories."
        ),
    },
    {
        "query": "Orchard Systems ClickHouse telemetry ingest architecture",
        "url": "https://orchardsystems.example/blog/clickhouse-migration",
        "note": (
            "Their data layer mirrors yours.\n"
            "The ClickHouse post describes Kafka -> materialized views -> operator dashboards, "
            "which is structurally the Meridian outage pipeline. Expect the system-design "
            "interview to go deep here; the resume should give them the hook explicitly "
            "(Kafka, ClickHouse, billions of readings a day)."
        ),
    },
    {
        "query": "climate hardware fleet telemetry data quality challenges",
        "url": "https://gridnotes.example/fleet-telemetry-data-quality",
        "note": (
            "Domain scan: data quality is the industry's open wound.\n"
            "Every practitioner writeup I can find lists the same failure modes — clock drift, "
            "gap-filled meters, firmware-version skew. Your reconciliation service is a direct "
            "'I fixed this class of problem' story; one supporting bullet, business framing."
        ),
    },
    {
        "query": "Orchard Systems glassdoor engineering culture reviews",
        "url": None,
        "note": (
            "Culture check: nothing alarming, one useful signal.\n"
            "Reviews repeatedly mention 'writing culture' and RFC-driven decisions. The JD's "
            "R6 (clear writing) is real. Your blog post and the postmortem template are worth "
            "their own line — dated, verifiable artifacts beat 'strong communication skills'."
        ),
    },
    {
        "query": "staff engineer resume anchor project single story best practice",
        "url": "https://hiringsignals.example/staff-resumes-one-story",
        "note": (
            "Calibrating the Staff resume shape.\n"
            "Consensus across hiring-manager writeups: one deep story with numbers beats four "
            "shallow ones; supporting projects get one line each. This confirms the structure "
            "I proposed in the plan — anchor first, then support."
        ),
    },
    {
        "query": "Meridian Grid outage prediction pipeline public references",
        "url": "https://meridiangrid.example/customers",
        "note": (
            "Checking what's publicly claimable about YOUR work.\n"
            "Meridian's public site says 'utility customers across three regions' — it does "
            "NOT say 'every major utility on the eastern seaboard', and neither does your "
            "resume. Nothing public contradicts the 14-customers figure, so that's the "
            "defensible number."
        ),
    },
    {
        "query": "Orchard Systems interview process practical exercise format",
        "url": "https://orchardsystems.example/careers/interviewing",
        "note": (
            "Interview format confirms the no-keyword-soup posture.\n"
            "Editor-based practical in your own environment, then a code-review round. The "
            "resume's job is to earn the hiring-manager deep dive — narrative evidence over "
            "ATS optimization, though I'll keep the hard keywords present in sentence form."
        ),
    },
    {
        "query": "CRDT offline sync field workforce apps production case studies",
        "url": "https://syncpatterns.example/crdt-field-apps",
        "note": (
            "Your offline-sync story is rarer than you think.\n"
            "Production CRDT deployments with real field crews are thin on the ground — most "
            "'offline-first' writeups are demos. Orchard lists intermittent-connectivity "
            "experience as a nice-to-have; one crisp bullet keeps it discoverable."
        ),
    },
    {
        "query": "grid analytics competitors Orchard Systems positioning",
        "url": None,
        "note": (
            "Adjacent-domain check.\n"
            "Meridian (grid analytics) and Orchard (fleet platform) are neighbors, not "
            "competitors — no conflict-of-interest smell in the application, and the domain "
            "vocabulary transfers almost one-to-one: meters/readings <-> devices/telemetry."
        ),
    },
    {
        "query": "Orchard Systems policy engine product documentation",
        "url": "https://docs.orchardsystems.example/policy-engine",
        "note": (
            "Policy engine docs skim.\n"
            "Policies compile to streaming jobs over the telemetry firehose — which is "
            "your alerting rules engine bullet, nearly verbatim. Moving that bullet up in the "
            "2019–2021 section so it's not buried."
        ),
    },
    {
        "query": "Orchard Systems status page public incident history",
        "url": "https://status.orchardsystems.example/history",
        "note": (
            "Status-page archaeology.\n"
            "Eleven public incidents in the past year, every one with a linked postmortem and "
            "a 'what we changed' section. A company that publishes postmortems will read your "
            "2024 ingest-outage story as fluent native speech — keep the postmortem mention "
            "in the operational section."
        ),
    },
    {
        "query": "Orchard Systems github open source repositories",
        "url": None,
        "note": (
            "Open-source presence check.\n"
            "A handful of SDK repos and a telemetry schema spec; no expectation that "
            "candidates arrive with public OSS. Confirms the portfolio step stays skipped — "
            "your leverage is operational evidence, not stars."
        ),
    },
    {
        "query": "compensated on-call rotation product engineering norms 2026",
        "url": "https://oncallreport.example/2026-survey",
        "note": (
            "Last research pass — on-call norms.\n"
            "Orchard's 'compensated, rotated fairly, and quiet' phrasing tracks the current "
            "healthy-rotation norm. They will ask about YOUR rotation experience directly; "
            "the interview section of this run has to produce that answer in your own words."
        ),
    },
]

# ── span usage (cumulative input-side ≈ 1.54M tokens, CONTRACT §8) ────────────

_SPANS: list[tuple[int, int, int]] = [
    # (input_tokens, output_tokens, cache_read_input_tokens)
    (2400, 900, 0),
    (5200, 1300, 38000),
    (6100, 2100, 74000),
    (4800, 1700, 109000),
    (7300, 2400, 141000),
    (5900, 2000, 168000),
    (8200, 3100, 197000),
    (6800, 2600, 223000),
    (9400, 4800, 251000),
    (7100, 3900, 276000),
]

EXPECTED_INPUT_TOKENS = sum(s[0] + s[2] for s in _SPANS)  # 1,539,200


def _span_op(i: int) -> Op:
    inp, out, cache = _SPANS[i]
    return span(f"mockspan_l_{i + 1:02d}", inp, out, cache_read=cache)


def _mem_write(path: str, content: str, tool_id: str) -> Op:
    return tool("memory_write", {"path": path, "content": content}, tool_id)


def _mem_read(path: str, tool_id: str) -> Op:
    return tool("memory_read", {"path": path}, tool_id)


def _ask_op(i: int) -> Op:
    q = QUESTIONS[i]
    payload: dict[str, Any] = {"question": q["question"], "context": q["context"], "kind": q["kind"]}
    if "options" in q:
        payload["options"] = q["options"]
    return ("ask", payload)


# ── script assembly ───────────────────────────────────────────────────────────


def _emit_all(ops: list[Op], *items: Op) -> None:
    for it in items:
        ops.append(it)
        ops.append(PAUSE)


def _build_script() -> list[Op]:
    ops: list[Op] = []
    tool_n = 0

    def tid() -> str:
        nonlocal tool_n
        tool_n += 1
        return f"mocktool_l_{tool_n:03d}"

    # ── phase 0: kickoff, running, initial plan, intro ────────────────────────
    _emit_all(
        ops,
        KICKOFF,
        ("emit", "session.status_running", {}),
        _PLAN_REVS[0],
        msg(
            "Starting the full run — here's how I'll work.\n"
            "I read your resume and the Orchard Systems posting on ingest. The plan on your "
            "screen is live: I revise it as I learn, and I'll mark steps done, skipped, or "
            "added rather than pretending the first plan survived contact with reality.\n\n"
            "First pass impressions, so you know where this is heading: your stack overlap "
            "with Orchard is excellent (Kafka, ClickHouse, TypeScript/React, Go), your scale "
            "story is real, and the resume's biggest liability is that it never says the "
            "words 'on-call' or 'incident' while their posting practically shouts them.\n\n"
            "I'll research the company first, map every JD requirement to evidence, then "
            "interview you about the gaps only you can close. Expect four questions — answer "
            "in your own words; everything you say becomes quotable grounding material."
        ),
    )

    # ── phase 1: profile ingest into memory ───────────────────────────────────
    _emit_all(
        ops,
        _mem_read("coach/", tid()),
        msg(
            "Writing your profile to memory before anything else.\n"
            "I keep a verbatim-backed candidate profile so later steps (and the grounding "
            "review) can trace every claim to a source. Anything I couldn't source from your "
            "resume is filed under 'gaps' — that list becomes the interview."
        ),
        _mem_write("coach/candidate_profile.md", _PROFILE_MD, tid()),
        _span_op(0),
    )

    # ── phase 2: research (14 searches, 10 fetches, per-topic notes) ──────────
    _emit_all(
        ops,
        msg(
            "Research pass on Orchard Systems.\n"
            "Sixteen queries queued: company shape, funding stage, engineering culture, "
            "stack, interview process, and the domain around them. I'll note what each pass "
            "changes about the resume strategy — research that doesn't change the draft is "
            "trivia, and I'll say so when that's the case."
        ),
    )
    for i, topic in enumerate(_RESEARCH):
        _emit_all(ops, tool("web_search", {"query": topic["query"]}, tid()))
        if topic["url"]:
            _emit_all(ops, tool("web_fetch", {"url": topic["url"]}, tid()))
        _emit_all(ops, msg(topic["note"]))
        if i in (4, 9):
            _emit_all(ops, _span_op(1 if i == 4 else 2))
    _emit_all(
        ops,
        _mem_write("coach/research/orchard_overview.md", _ORCHARD_OVERVIEW_MD, tid()),
        msg(
            "Research is done and filed.\n"
            "Net effect on strategy: lead with the ingest-scale story, speak their pillar "
            "vocabulary, keep keywords inside sentences, and make operational ownership a "
            "named section instead of an implication. Moving to the requirement map."
        ),
        _PLAN_REVS[1],
    )

    # ── phase 3: JD requirement mapping + transient error + portfolio skip ────
    _emit_all(
        ops,
        msg(
            "Mapping all ten JD requirements to resume evidence.\n"
            "Each requirement gets a strength grade and a plan: strong evidence gets "
            "surfaced, medium evidence gets sharpened, weak evidence goes to the interview. "
            "The full map is in memory; highlights follow."
        ),
        _mem_write("coach/research/jd_requirements.md", _JD_REQUIREMENTS_MD, tid()),
        msg(
            "R1 operational ownership — WEAK, and it's their top requirement.\n"
            "The posting wants pager scars ('what you changed so the same page never fired "
            "twice'). Your only adjacent bullet is co-running the incident review process, "
            "which reads as facilitation. This goes to the interview as the highest-stakes "
            "question; I won't draft around it."
        ),
        msg(
            "R2 Staff scope — MEDIUM, fixable with shape.\n"
            "Mentoring four engineers with two promotions is real evidence, and the "
            "postmortem template is an artifact with org-wide adoption. What's missing is one "
            "cross-team story told as a story — the anchor project will carry that load."
        ),
        _span_op(3),
        msg(
            "R3 full-stack depth and R4 streaming scale — STRONG, lead with them.\n"
            "You're equally credible in the React rebuild and the ClickHouse feature store, "
            "which is rare and is literally their screening bar ('equally credible in a React "
            "performance review and a schema design review'). The 2.1B readings/day figure "
            "goes in the first bullet."
        ),
    )
    _emit_all(
        ops,
        tool("web_fetch", {"url": "https://orchardsystems.example/careers/staff-product-engineer"}, tid()),
        (
            "emit",
            "session.error",
            {
                "error": {
                    "type": "overloaded_error",
                    "message": "upstream fetch timed out after 30s — retrying the last tool call (attempt 1 of 3)",
                }
            },
        ),
        ("emit", "session.status_running", {}),
        msg(
            "Recovered — the fetch went through on retry.\n"
            "Transient upstream timeout; nothing lost. The careers page confirmed the JD text "
            "I already had, so no strategy change."
        ),
    )
    _emit_all(
        ops,
        msg(
            "R5 through R10, quickly.\n"
            "Ambiguity-to-plan (R5): implied by the tech-lead role; the summary line will "
            "make it explicit. Writing (R6): your blog post and the postmortem template get "
            "a dated 'Writing & talks' section — verifiable artifacts, no adjectives.\n\n"
            "The nice-to-haves are quietly excellent: telemetry at billions/day (R7), "
            "offline-first sync (R8), and five years of energy domain (R9) are all direct "
            "hits. R10 (public portfolio) is your weakest nice-to-have: two internal-ish "
            "artifacts. I'm SKIPPING the portfolio-review step rather than padding it — "
            "the JD marks it optional, and manufactured links would hurt you in their "
            "writing-culture screen."
        ),
        _PLAN_REVS[2],
        _mem_write("coach/gap_analysis.md", _GAP_ANALYSIS_MD, tid()),
        _span_op(4),
    )

    # ── phase 4: interview (4 blocking questions) ─────────────────────────────
    _emit_all(
        ops,
        msg(
            "Interview time — four questions, one at a time.\n"
            "These are the gaps only you can close: your anchor story, the true scope of the "
            "portal 'led' claim, one defensible operational metric, and your on-call reality. "
            "Answer plainly; I quote you, I don't embellish you."
        ),
        msg(
            "Question 1 of 4 — the anchor.\n"
            "Four projects are competing to be your headline. Orchard's Staff role owns ONE "
            "pillar deeply, so the resume should mirror that shape. Pick the story you can "
            "defend for forty-five minutes."
        ),
    )
    ops.append(_ask_op(0))
    _emit_all(
        ops,
        ("emit", "session.status_running", {}),
        (
            "emit",
            "agent.message",
            lambda ctx: {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Anchor locked: "
                            + ctx.answers[0]
                            + ".\nThe experience section restructures around it — anchor first "
                            "with metrics, the other three projects drop to one supporting "
                            "line each. Logging the answer to memory before Q2."
                        ),
                    }
                ]
            },
        ),
        _interview_log_op(1),
        msg(
            "Question 2 of 4 — the 'led' claim.\n"
            "Staff screens always drill into team size and duration, so I'd rather pin this "
            "down now than have a hiring manager pin you down later."
        ),
    )
    ops.append(_ask_op(1))
    _emit_all(
        ops,
        ("emit", "session.status_running", {}),
        (
            "emit",
            "agent.message",
            lambda ctx: {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Portal scope recorded — your words: \""
                            + ctx.answers[1]
                            + "\"\nThat's a real team-lead arc and it goes in verbatim-backed. "
                            "Two questions left; the next one is about numbers."
                        ),
                    }
                ]
            },
        ),
        _interview_log_op(2),
        _span_op(5),
        ("emit", "agent.thread_context_compacted", {}),
        msg(
            "Context compacted — nothing lost.\n"
            "The research notes, requirement map, and your two answers are all in memory "
            "files, which is exactly why I write them down instead of trusting the thread. "
            "Continuing the interview."
        ),
        msg(
            "Question 3 of 4 — one number you'd defend out loud.\n"
            "The anchor bullet is currently qualitative. If you own a dashboard metric, give "
            "it with its qualifiers; if not, say so and the bullet stays honest."
        ),
    )
    ops.append(_ask_op(2))
    _emit_all(
        ops,
        ("emit", "session.status_running", {}),
        (
            "emit",
            "agent.message",
            lambda ctx: {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Metrics recorded, with their qualifiers: \""
                            + ctx.answers[2]
                            + "\"\nNumbers with thresholds and time windows survive "
                            "interviews; naked percentages don't. This also earns a new plan "
                            "step — the anchor project deserves a dedicated quantify pass "
                            "before drafting, so I'm adding one."
                        ),
                    }
                ]
            },
        ),
        _interview_log_op(3),
        _PLAN_REVS[3],
        msg(
            "Question 4 of 4 — operational scars.\n"
            "This is the run's highest-stakes question: Orchard's top requirement is the one "
            "thing your resume never says. Whatever you tell me becomes an 'Operational "
            "ownership' section in your own words."
        ),
    )
    ops.append(_ask_op(3))
    _emit_all(
        ops,
        ("emit", "session.status_running", {}),
        (
            "emit",
            "agent.message",
            lambda ctx: {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "That's the section, done — your words: \""
                            + ctx.answers[3]
                            + "\"\nThree years of rotation, a named postmortem, and tooling "
                            "that retired an incident class: that is precisely the 'same page "
                            "never fired twice' evidence their JD asks for. Interview closed."
                        ),
                    }
                ]
            },
        ),
        _interview_log_op(4),
        _PLAN_REVS[4],
        _span_op(6),
    )

    # ── phase 5: quantify pass ────────────────────────────────────────────────
    _emit_all(
        ops,
        _mem_read("coach/research/jd_requirements.md", tid()),
        _mem_read("coach/gap_analysis.md", tid()),
        msg(
            "Quantify pass on the anchor project.\n"
            "Consolidating every number that survives grounding: 2.1B readings/day and 14 "
            "utility customers (resume), 91% P1 precision and 99.95% ingest availability "
            "(your Q3 answer, dashboard-sourced with qualifiers), p75 4.2s to 1.1s and the "
            "four-engineer/eleven-month portal scope (resume + your Q2 confirmation). "
            "Anything not on that list stays out of the draft."
        ),
        _mem_write("coach/draft_notes.md", _DRAFT_NOTES_MD, tid()),
        _PLAN_REVS[5],
    )

    # ── phase 6: drafting + judged revisions ──────────────────────────────────
    _emit_all(
        ops,
        msg(
            "Drafting v1 now.\n"
            "Structure per the notes in memory: summary with the anchor named, a 'Why "
            "Orchard' section mirroring their own priority order, anchor-first experience, "
            "the new Operational ownership section from your Q4 answer, and honest writing/"
            "talks. The grounding review runs automatically when I submit — expect it to "
            "push back at least once; that's the system working."
        ),
        _span_op(7),
    )
    ops.append(
        (
            "submit",
            lambda ctx: {
                "draft": draft_v1(ctx),
                "label": "orchard-tailored",
                "summary": "Anchor-first restructure with the new Operational ownership section.",
            },
        )
    )
    _emit_all(
        ops,
        _PLAN_REVS[6],
        msg(
            "The review found three ungrounded claims — revising.\n"
            "It flagged the 'every major utility on the eastern seaboard' scale claim (your "
            "resume says 14 customers), the 'roughly 4x faster for thousands of operators' "
            "framing (the sourced numbers are p75 4.2s to 1.1s), and the 'five-nines "
            "durability, zero data loss' line (the only availability figure any source "
            "supports is your 99.95%). All three get rewritten to the defensible numbers, "
            "plus I'm tightening the portal-lead and mentoring bullets to your confirmed "
            "specifics while I'm in there. The layout churn you'll see in the diff is "
            "glyph-normalization noise; the review only counts real wording changes."
        ),
        _span_op(8),
    )
    ops.append(
        (
            "submit",
            lambda ctx: {
                "draft": draft_v2(ctx),
                "label": "orchard-tailored r2",
                "summary": "Three flagged claims regrounded; portal scope and mentoring made specific.",
            },
        )
    )
    _emit_all(
        ops,
        msg(
            "One finding left — an invented baseline.\n"
            "The admin-tooling bullet said 'roughly in half (from 9 days)', and no source "
            "gives the 9-day baseline; I made it up from pattern-matching, which is exactly "
            "what the review exists to catch. Dropping the parenthetical and doing a final "
            "polish pass: the incident-review bullet gets the org-wide template back, and "
            "the summary bullets absorb the interview-loop count from your resume."
        ),
        _span_op(9),
    )
    ops.append(
        (
            "submit",
            lambda ctx: {
                "draft": draft_v3(ctx),
                "label": "orchard-final",
                "summary": "Grounding-clean final; every number traces to the resume or your answers.",
            },
        )
    )
    _emit_all(
        ops,
        _PLAN_REVS[7],
        msg(
            "Review passed — packaging the final version.\n"
            "The rubric scores are attached to the verdict below; the download button on the "
            "draft panel gives you resume.md as reviewed."
        ),
        _PLAN_REVS[8],
        msg(
            "Done — three drafts, one survivor.\n"
            "What changed and why, in one breath: the resume now tells Orchard's shape of "
            "story (one pillar, owned end to end), the operational-ownership gap became a "
            "named section in your own words, every number carries its qualifier and its "
            "source, and the two claims that would have died in a reference check died here "
            "instead. Interview prep tip: the three findings the review caught are exactly "
            "the three questions a good hiring manager would have asked — rehearse those "
            "answers."
        ),
    )
    ops.append(("emit", "session.status_idle", {"stop_reason": {"type": "end_turn"}}))
    return ops


LONG_SCRIPT: list[Op] = _build_script()
