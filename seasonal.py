"""HOM seasonal offer calendar — offers are SEASON-dependent (from the team's offer/video calendar sheet).

The wrong offer at the wrong time fails: "Lock In This Year's Prices / Book Now, Paint Later" only works
Nov-Dec (book now to paint next spring); "Exterior Market Launch" is the Feb-Jun spring-scarcity play, etc.
This module returns the season-appropriate offer(s) for the current month + the client's services, which
the strategy engine injects so it never recommends an out-of-season offer. Climate-aware: warm-winter
states can run exterior offers later/earlier (exteriors paintable more of the year).
"""
import datetime

SEASONAL_OFFERS = [
    {"name": "Exterior Market Launch", "months": [2, 3, 4, 5, 6], "service": "Exterior",
     "lead": "Save up to 10-15% off (or hundreds off)",
     "angle": ("Scarcity of spring & summer spots — book your estimate now so you can choose the exact week "
               "you want to paint. This is for PLANNERS.")},
    {"name": "Exterior Season Last Call", "months": [8, 9, 10], "service": "Exterior",
     "lead": "Save up to 10-15% off (or hundreds off)",
     "angle": ("Scarcity of late-summer & fall spots — book your estimate now to paint before the cold weather "
               "comes. This is for PROCRASTINATORS.")},
    {"name": "Best Offer of the Year — Interiors & Cabinets", "months": [8, 9, 10, 11, 12, 1, 2],
     "service": "Interior, Cabinet", "lead": "Save up to $1,000 off + free upgrades",
     "angle": ("Our biggest offer of the year, run once. Save when you paint your interior/cabinets Nov-Feb — "
               "it keeps our crews busy while homeowners are busy with the holidays.")},
    {"name": "Lock In This Year's Prices Next Year", "months": [11, 12], "service": "All Services",
     "lead": "Lock in this year's pricing / save 10-15%",
     "angle": ("Save when you book before the end of the year. For homeowners who already know they'll paint "
               "next year — start the conversation 1-3 months earlier. (This is the 'Book Now, Paint Later' "
               "winter play — only Nov-Dec.)")},
]

# Offer-stack add-ons (choose 3-4 to build the full offer).
OFFER_STACK = [
    "A dollar/percent discount (save hundreds, or 10-15% off)", "Free color consultation", "Free ceiling paint",
    "One free accent wall", "Soft-close hinge installation (cabinets)", "Free paint upgrade ($500+ value)",
    "VIP Priority Scheduling (choose your week)", "1-year extended touch-up warranty",
    "Complimentary driveway & path powerwash", "Complimentary house cleaning", "Professional photo package",
    "$150 to a charity in your name", "Complimentary paint maintenance kit",
    "Front door included with a full exterior repaint",
]

# States where exteriors are paintable most of the year — the cold-weather scarcity framing softens.
WARM_WINTER_STATES = {"FL", "TX", "AZ", "CA", "GA", "NC", "SC", "LA", "AL", "MS", "NV", "NM", "HI"}


def _svc_match(offer_service, client_services):
    s = offer_service.lower()
    c = (client_services or "").lower()
    if s == "all services" or not c:
        return True
    if "exterior" in s:
        return "exterior" in c
    if "interior" in s or "cabinet" in s:
        return ("interior" in c) or ("cabinet" in c)
    return True


def active_offers(month=None, services="", region=""):
    """Season-appropriate seasonal offer(s) for the month AND the client's services (offers whose service
    the client doesn't sell are excluded). Empty list = between windows -> use the evergreen baseline."""
    month = month or datetime.date.today().month
    return [o for o in SEASONAL_OFFERS
            if month in o["months"] and _svc_match(o["service"], services)]


def context_for_prompt(month=None, services="", region=""):
    """A compact seasonal-offer briefing to inject into the strategy prompt."""
    month = month or datetime.date.today().month
    mname = datetime.date(2000, month, 1).strftime("%B")
    state = (region or "").strip().upper()[-2:]
    warm = state in WARM_WINTER_STATES
    act = active_offers(month, services, region)
    if act:
        lines = "\n".join(f"  - {o['name']} ({o['service']}): {o['lead']} — {o['angle']}" for o in act)
        active = f"In-season offer(s) for {mname}:\n{lines}"
    else:
        active = (f"{mname} is between seasonal windows — lead with the evergreen 'Free Estimate' baseline plus "
                  f"a value stack; don't force an out-of-season discount.")
    full = "; ".join(f"{o['name']} (months {min(o['months'])}-{max(o['months'])}, {o['service']})" for o in SEASONAL_OFFERS)
    climate = (f"NOTE: {state} is a warm-winter market — exteriors are paintable most of the year, so the "
               f"cold-weather scarcity framing is weaker; lean on choice-of-week/limited-crew scarcity instead."
               if warm else
               f"NOTE: {state} has real winters — exterior offers should respect the paint-season windows above.")
    stack = "Offer stack add-ons (pick 3-4): " + "; ".join(OFFER_STACK)
    return (f"CURRENT MONTH: {mname}.\n{active}\n\nFull seasonal calendar: {full}.\n{climate}\n{stack}\n"
            f"NEVER recommend an out-of-season offer (e.g. 'Lock In Prices / Book Now Paint Later' only Nov-Dec).")
