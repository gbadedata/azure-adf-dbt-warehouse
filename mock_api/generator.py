"""Deterministic synthetic data for a charity's supporter CRM and event exports.

Two snapshots are produced, one day apart, so incremental loading can be
tested properly:

* ``day1`` is the source as it looked at 06:00 UTC on 1 November 2026.
* ``day2`` is the source 24 hours later: new supporters and donations,
  refunds, pending gifts that cleared, consent changes, a campaign budget
  change, and a few *late commits*: donations whose ``updated_at`` is earlier
  than the day-one high watermark but which were not visible until day two.
  A loader without an overlap window misses them.

All people are invented. Email addresses use the reserved ``example.org``
domain. Nothing here describes a real organisation or real supporters.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

SNAPSHOT_DAY1 = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
SNAPSHOT_DAY2 = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
HISTORY_START = datetime(2025, 11, 1, 0, 0, tzinfo=UTC)

REGISTRATION_COLUMNS = [
    "registration_id",
    "event_name",
    "event_date",
    "campaign_id",
    "supporter_email",
    "registered_at",
    "ticket_type",
    "fundraising_target_gbp",
]

FIRST_NAMES = [
    "alex",
    "sam",
    "jordan",
    "priya",
    "tomas",
    "aisha",
    "kofi",
    "mei",
    "liam",
    "zara",
    "noah",
    "amara",
    "ewan",
    "fatima",
    "callum",
    "nia",
    "oscar",
    "leila",
    "rory",
    "hana",
]
LAST_NAMES = [
    "smith",
    "jones",
    "patel",
    "okafor",
    "nowak",
    "evans",
    "chen",
    "khan",
    "murphy",
    "adeyemi",
    "taylor",
    "walsh",
    "silva",
    "hughes",
    "ali",
    "brown",
    "osei",
    "reid",
    "kaur",
    "fraser",
]
POSTCODE_DISTRICTS = [
    "E1",
    "N7",
    "SE15",
    "SW9",
    "M14",
    "B12",
    "LS6",
    "BS5",
    "G41",
    "CF24",
    "NE6",
    "L8",
    "NG7",
    "S11",
    "EH7",
    "CO1",
    "BN2",
    "OX4",
    "CB1",
    "PL4",
]
PAYMENT_METHODS = ["card", "direct_debit", "paypal"]
AMOUNTS = [5, 10, 10, 15, 20, 20, 25, 30, 50, 50, 75, 100, 250]


@dataclass(frozen=True)
class Scale:
    supporters: int = 2400
    max_donations_per_supporter: int = 6
    registrations_day1: int = 300
    new_supporters_day2: int = 120
    new_donations_day2: int = 350
    refunds_day2: int = 40
    pending_cleared_day2: int = 25
    consent_changes_day2: int = 30
    late_commits: int = 4
    registrations_day2_new: int = 60
    registrations_day2_repeat: int = 25
    registrations_day2_changed: int = 5


SMALL = Scale(
    supporters=120,
    max_donations_per_supporter=4,
    registrations_day1=40,
    new_supporters_day2=10,
    new_donations_day2=30,
    refunds_day2=5,
    pending_cleared_day2=3,
    consent_changes_day2=4,
    late_commits=3,
    registrations_day2_new=8,
    registrations_day2_repeat=6,
    registrations_day2_changed=2,
)

CAMPAIGNS = [
    ("CMP-000", "General donations", "website", date(2025, 1, 1), date(2027, 12, 31), "0.00"),
    ("CMP-001", "Winter Appeal 2025", "email", date(2025, 11, 10), date(2026, 1, 31), "18000.00"),
    ("CMP-002", "Search Ads Always-On", "search", date(2025, 11, 1), date(2026, 12, 31), "24000.00"),
    ("CMP-003", "Spring Email Series", "email", date(2026, 3, 1), date(2026, 4, 30), "6000.00"),
    ("CMP-004", "Walk Together 2026", "events", date(2026, 5, 1), date(2026, 9, 30), "15000.00"),
    ("CMP-005", "Summer Run Series", "events", date(2026, 6, 1), date(2026, 8, 31), "12000.00"),
    ("CMP-006", "Awareness Week Social", "social", date(2026, 5, 11), date(2026, 5, 24), "9000.00"),
    ("CMP-007", "Workplace Giving Partners", "partnerships", date(2026, 1, 15), date(2026, 12, 31), "7500.00"),
    ("CMP-008", "Podcast Sponsorship", "audio", date(2026, 7, 1), date(2026, 10, 31), "11000.00"),
    ("CMP-009", "Christmas Appeal 2026", "email", date(2026, 10, 20), date(2027, 1, 15), "20000.00"),
]

EVENTS = [
    ("Walk Together London", date(2026, 9, 20), "CMP-004", ["standard", "family", "virtual"], [100, 150, 250]),
    ("Walk Together Manchester", date(2026, 9, 27), "CMP-004", ["standard", "family"], [100, 150]),
    ("Summer Run Series - Bristol 10k", date(2026, 7, 12), "CMP-005", ["standard", "charity_place"], [150, 300, 500]),
    ("Winter Wander Edinburgh", date(2026, 12, 6), "CMP-009", ["standard", "family", "virtual"], [50, 100]),
]


def iso(moment: datetime) -> str:
    """Format a timestamp the way the source API does: UTC, milliseconds, ``Z``."""
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _money(value: Decimal | int | float) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01'))}"


def _random_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    seconds = (end - start).total_seconds()
    offset = rng.uniform(0, seconds)
    moment = start + timedelta(seconds=offset)
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


def _quirky_email(rng: random.Random, email: str) -> str:
    """Return the email as a source system might store it: odd case or stray spaces."""
    roll = rng.random()
    if roll < 0.03:
        return email.upper()
    if roll < 0.05:
        return f" {email.title()} "
    return email


def _campaigns() -> list[dict]:
    rows = []
    for campaign_id, name, channel, start, end, budget in CAMPAIGNS:
        created = datetime.combine(start - timedelta(days=14), datetime.min.time(), tzinfo=UTC)
        created = max(created, HISTORY_START - timedelta(days=30))
        rows.append(
            {
                "campaign_id": campaign_id,
                "name": name,
                "channel": channel,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "budget_gbp": budget,
                "created_at": iso(created),
                "updated_at": iso(created + timedelta(hours=3)),
            }
        )
    return rows


def _active_campaigns(on: datetime) -> list[str]:
    day = on.date()
    return [c[0] for c in CAMPAIGNS if c[0] != "CMP-000" and c[3] <= day <= c[4]]


def _new_supporter(rng: random.Random, number: int, created: datetime) -> dict:
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    email = f"{first}.{last}{number}@example.org"
    active = _active_campaigns(created)
    acquisition = rng.choice(active) if active and rng.random() < 0.8 else "CMP-000"
    return {
        "supporter_id": f"SUP-{number:06d}",
        "email": _quirky_email(rng, email),
        "postcode_district": rng.choice(POSTCODE_DISTRICTS),
        "acquisition_campaign_id": acquisition,
        "marketing_consent": rng.random() < 0.62,
        "status": "active",
        "created_at": iso(created),
        "updated_at": iso(created),
    }


def _new_donation(
    rng: random.Random,
    number: int,
    supporter: dict,
    received: datetime,
    status: str,
) -> dict:
    active = _active_campaigns(received)
    if rng.random() < 0.55:
        campaign = supporter["acquisition_campaign_id"]
    else:
        campaign = rng.choice(active) if active else "CMP-000"
    amount = Decimal(rng.choice(AMOUNTS))
    if rng.random() < 0.15:
        amount += Decimal(rng.choice([0.5, 2.5, 7.25]))
    return {
        "donation_id": f"DON-{number:07d}",
        "supporter_id": supporter["supporter_id"],
        "campaign_id": campaign,
        "amount": _money(amount),
        "currency": "GBP",
        "payment_method": rng.choice(PAYMENT_METHODS),
        "status": status,
        "gift_aid": rng.random() < 0.45,
        "received_at": iso(received),
        "updated_at": iso(received),
    }


def _registration(
    rng: random.Random,
    number: int,
    supporters: list[dict],
    registered: datetime,
) -> dict:
    event_name, event_date, campaign_id, tickets, targets = rng.choice(EVENTS)
    if rng.random() < 0.8:
        email = supporters[rng.randrange(len(supporters))]["email"].strip().lower()
        email = email.upper() if rng.random() < 0.1 else email
    else:
        email = f"guest{number}@example.org"
    return {
        "registration_id": f"REG-{number:06d}",
        "event_name": event_name,
        "event_date": event_date.isoformat(),
        "campaign_id": campaign_id,
        "supporter_email": email,
        "registered_at": iso(registered),
        "ticket_type": rng.choice(tickets),
        "fundraising_target_gbp": _money(rng.choice(targets)),
    }


def generate(seed: int = 2026, scale: Scale = Scale()) -> dict:
    """Build both snapshots and both event files. Same seed, same output."""
    rng = random.Random(seed)
    campaigns = _campaigns()

    # ---- Day one: a year of history up to 06:00 on 1 November 2026 -------------
    supporters: list[dict] = []
    for number in range(1, scale.supporters + 1):
        created = _random_between(rng, HISTORY_START, SNAPSHOT_DAY1 - timedelta(hours=2))
        supporters.append(_new_supporter(rng, number, created))

    donations: list[dict] = []
    donation_number = 1
    for supporter in supporters:
        created = parse_iso(supporter["created_at"])
        for _ in range(rng.randint(0, scale.max_donations_per_supporter)):
            received = _random_between(rng, created, SNAPSHOT_DAY1 - timedelta(hours=1))
            recent = received > SNAPSHOT_DAY1 - timedelta(days=10)
            roll = rng.random()
            if recent and roll < 0.25:
                status = "pending"
            elif roll > 0.96:
                status = "refunded"
            else:
                status = "paid"
            donation = _new_donation(rng, donation_number, supporter, received, status)
            if status == "refunded":
                refunded_at = min(received + timedelta(days=rng.randint(1, 20)), SNAPSHOT_DAY1 - timedelta(hours=1))
                donation["updated_at"] = iso(refunded_at)
            donations.append(donation)
            donation_number += 1

    # A visible tail just before the snapshot, so the day-one high watermark is
    # 05:58, and the late commits below sit eight minutes behind it.
    tail_supporter = supporters[0]
    tail = _new_donation(rng, donation_number, tail_supporter, SNAPSHOT_DAY1 - timedelta(minutes=2), "paid")
    donations.append(tail)
    donation_number += 1

    late_commits = []
    for _ in range(scale.late_commits):
        supporter = supporters[rng.randrange(len(supporters))]
        late = _new_donation(rng, donation_number, supporter, SNAPSHOT_DAY1 - timedelta(minutes=10), "paid")
        late_commits.append(late)
        donation_number += 1

    registrations_day1 = []
    registration_number = 1
    for _ in range(scale.registrations_day1):
        registered = _random_between(rng, datetime(2026, 4, 1, tzinfo=UTC), SNAPSHOT_DAY1 - timedelta(hours=1))
        registrations_day1.append(_registration(rng, registration_number, supporters, registered))
        registration_number += 1

    day1 = {
        "campaigns": copy.deepcopy(campaigns),
        "supporters": copy.deepcopy(supporters),
        "donations": copy.deepcopy(donations),
    }

    # ---- Day two: the next 24 hours of change ----------------------------------
    window_start = SNAPSHOT_DAY1 + timedelta(minutes=1)
    window_end = SNAPSHOT_DAY2 - timedelta(minutes=5)

    def in_window() -> str:
        return iso(_random_between(rng, window_start, window_end))

    campaigns2 = copy.deepcopy(campaigns)
    christmas = next(c for c in campaigns2 if c["campaign_id"] == "CMP-009")
    christmas["budget_gbp"] = _money(Decimal(christmas["budget_gbp"]) + 5000)
    christmas["updated_at"] = in_window()

    supporters2 = copy.deepcopy(supporters)
    for supporter in rng.sample(supporters2, scale.consent_changes_day2):
        supporter["marketing_consent"] = not supporter["marketing_consent"]
        supporter["updated_at"] = in_window()

    new_supporters = []
    for offset in range(scale.new_supporters_day2):
        created = parse_iso(in_window())
        new_supporters.append(_new_supporter(rng, scale.supporters + offset + 1, created))
    supporters2.extend(new_supporters)

    donations2 = copy.deepcopy(donations)
    paid = [d for d in donations2 if d["status"] == "paid"]
    for donation in rng.sample(paid, scale.refunds_day2):
        donation["status"] = "refunded"
        donation["updated_at"] = in_window()
    pending = [d for d in donations2 if d["status"] == "pending"]
    for donation in pending[: scale.pending_cleared_day2]:
        donation["status"] = "paid"
        donation["updated_at"] = in_window()

    for _ in range(scale.new_donations_day2):
        pool = new_supporters if rng.random() < 0.6 else supporters
        supporter = pool[rng.randrange(len(pool))]
        received = parse_iso(in_window())
        received = max(received, parse_iso(supporter["created_at"]))
        donations2.append(_new_donation(rng, donation_number, supporter, received, "paid"))
        donation_number += 1
    donations2.extend(copy.deepcopy(late_commits))

    registrations_day2 = []
    for _ in range(scale.registrations_day2_new):
        registered = parse_iso(in_window())
        registrations_day2.append(_registration(rng, registration_number, supporters2, registered))
        registration_number += 1
    repeats = copy.deepcopy(rng.sample(registrations_day1, scale.registrations_day2_repeat))
    for repeat in repeats[: scale.registrations_day2_changed]:
        repeat["ticket_type"] = "charity_place" if repeat["ticket_type"] != "charity_place" else "standard"
        repeat["fundraising_target_gbp"] = _money(Decimal(repeat["fundraising_target_gbp"]) + 100)
    registrations_day2.extend(repeats)

    day2 = {"campaigns": campaigns2, "supporters": supporters2, "donations": donations2}

    return {
        "day1": day1,
        "day2": day2,
        "files": {"day1": registrations_day1, "day2": registrations_day2},
        "meta": {
            "seed": seed,
            "snapshot_day1": iso(SNAPSHOT_DAY1),
            "snapshot_day2": iso(SNAPSHOT_DAY2),
            "late_commit_donation_ids": [d["donation_id"] for d in late_commits],
            "changed_registration_ids": [r["registration_id"] for r in repeats[: scale.registrations_day2_changed]],
        },
    }
