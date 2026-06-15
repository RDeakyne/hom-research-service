"""Canonical Painting Ad Angle Taxonomy (v1).

The SHARED vocabulary that makes the Angle Engine work: OUR ads (the Angle Performance Index) and
COMPETITOR ads (the Manus Meta Ad Library teardown) are both tagged into this same list, so they're
directly comparable. Seeded from AI Creative/meta-ad-angles-v1+v2 and the hook_library.md copy-angle
tags. Versioned on purpose — if you change it, rebuild angle_index.json so the index stays comparable.
"""

ANGLES = [
    {"key": "owner_identity",  "name": "Owner / Founder Identity",        "desc": "Local, family-owned; the owner on camera; personal intro / origin."},
    {"key": "transformation",  "name": "Transformation / Before-After",   "desc": "Visible before/after; pride of result; 'best house on the block'."},
    {"key": "roi",             "name": "Investment / ROI / Resale Value", "desc": "Paint as a financial investment; resale; protect home value."},
    {"key": "craftsmanship",   "name": "Craftsmanship / Quality / Prep",  "desc": "Meticulous prep, clean lines, process, durability of the work."},
    {"key": "social_proof",    "name": "Social Proof / Reviews",          "desc": "Star ratings, review counts, # homes painted, testimonials."},
    {"key": "trust_risk",      "name": "Trust & Risk Reversal",           "desc": "Licensed/insured, warranty, money-back / satisfaction guarantee."},
    {"key": "done_for_you",    "name": "Done-For-You / Minimal Disruption","desc": "No mess, stress-free, leave for work, we handle everything."},
    {"key": "problem_pain",    "name": "Problem-First / Pain",            "desc": "Cost of neglect, peeling, rot, fading, warranty failure."},
    {"key": "diy_vs_pro",      "name": "DIY-Fail vs. Pro",                "desc": "Ladder safety, failed weekend project, why hire a pro, humor."},
    {"key": "seasonal",        "name": "Seasonal Urgency",                "desc": "Spring prep, paint before winter, weather-driven timing."},
    {"key": "local",           "name": "Local / Neighborhood Callout",    "desc": "City / neighborhood specificity; 'painters in <city>'."},
    {"key": "story",           "name": "Origin / Story",                  "desc": "Humble origin, tenure milestone, why we started, customer story."},
    {"key": "aspiration",      "name": "Aspiration / Identity",           "desc": "Curating quality, pride of home, lifestyle / identity framing."},
    {"key": "offer",           "name": "Offer / Savings",                 "desc": "Intro offer, seasonal discount, free estimate, savings framing."},
]

KEYS = [a["key"] for a in ANGLES]
NAMES = {a["key"]: a["name"] for a in ANGLES}
DESCS = {a["key"]: a["desc"] for a in ANGLES}


def list_for_prompt():
    """Bulleted taxonomy for an LLM classification/synthesis prompt."""
    return "\n".join(f"- {a['key']}: {a['name']} — {a['desc']}" for a in ANGLES)
