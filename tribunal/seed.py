"""Seed Azure AI Search: policy documents (Challenge 1 equivalent) and a synthetic
prior-claims corpus with planted fraud signals for the demo.

    python -m tribunal.seed            # both
    python -m tribunal.seed policies   # or: prior
"""
import glob
import json
import os
import random
import re
import sys

from azure.search.documents.indexes.models import SearchFieldDataType, SimpleField

from .search_tools import POLICY_INDEX, PRIOR_INDEX, ensure_index, upload
from .workflow import REPO

DATA = os.path.join(REPO, "challenge-0", "data")
SAMPLES = os.path.join(REPO, "challenge-6", "sample_claims")


def seed_policies():
    docs = []
    for path in sorted(glob.glob(os.path.join(DATA, "policies", "*.md"))):
        text = open(path).read()
        title = re.search(r"^# (.+)$", text, re.M).group(1)
        # ponytail: chunk on "## Section N" headers, keep the policy title on every chunk
        parts = re.split(r"^(?=## )", text, flags=re.M)
        for i, part in enumerate(parts):
            if len(part.strip()) < 40:
                continue
            heading = part.splitlines()[0].lstrip("# ").strip()
            docs.append({
                "id": f"{os.path.basename(path)[:-3]}-{i}",
                "title": f"{title} · {heading}",
                "content": part.strip(),
                "category": "policy",
                "file_name": os.path.basename(path),
            })
    ensure_index(POLICY_INDEX, [
        SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="file_name", type=SearchFieldDataType.String, filterable=True),
    ])
    upload(POLICY_INDEX, docs)
    print(f"policies: {len(docs)} chunks -> {POLICY_INDEX}")


NAMES = ["Priya Natarajan", "Tom Whitfield", "Elena Marquez", "Kwame Mensah", "Sofia Lindqvist", "Jamal Carter",
         "Hannah Byrne", "Luis Ortega", "Mei Chen", "Oscar Delgado", "Ava Thompson", "Noah Fischer"]
VEHICLES = ["2015 Ford Focus", "2019 Toyota RAV4", "2013 VW Golf", "2018 Nissan Altima", "2010 Chevrolet Malibu",
            "2020 Hyundai Elantra", "2016 Mazda 3", "2012 BMW 328i", "2017 Kia Sportage", "2014 Jeep Cherokee"]
DAMAGE = ["front bumper cracked and left headlight shattered after rear-ending a stopped vehicle at a red light",
          "rear bumper and trunk lid dented when struck from behind in stop-and-go traffic",
          "driver side doors scraped and mirror torn off in a parking garage sideswipe",
          "hail damage across hood and roof, multiple dents, windshield chipped",
          "passenger side rear quarter panel crumpled after being T-boned at an intersection",
          "front end damage from hitting a deer on a rural road at night, radiator leaking",
          "windshield and roof damaged by fallen tree branch during storm",
          "rear-end collision, bumper and exhaust damaged, vehicle drivable"]
POLICIES = ["COMP-AUTO-001", "COMM-AUTO-001", "LIAB-AUTO-001", "HV-AUTO-001"]


def _vin():
    return "".join(random.choices("ABCDEFGHJKLMNPRSTUVWXYZ0123456789", k=17))


def _doc(i, holder, vehicle, vin, policy, date, damage, outcome, paid):
    return {
        "id": f"CLM-{i:04d}", "claim_id": f"CLM-{i:04d}", "title": f"CLM-{i:04d} · {holder} · {vehicle}",
        "holder": holder, "vin": vin, "vehicle": vehicle, "policy_number": policy,
        "incident_date": date, "outcome": outcome, "paid_amount": float(paid),
        "content": f"Claimant {holder}, policy {policy}, vehicle {vehicle} VIN {vin}. Incident {date}: {damage}. "
                   f"Outcome: {outcome}, paid ${paid:,}.",
    }


def seed_prior():
    random.seed(7)
    docs = []
    for i in range(1, 41):
        paid = random.choice([0, 850, 1400, 2300, 3100, 4850, 6200])
        docs.append(_doc(i, random.choice(NAMES), random.choice(VEHICLES), _vin(), random.choice(POLICIES),
                         f"2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}", random.choice(DAMAGE),
                         "paid" if paid else "denied", paid))

    # Planted signals, keyed off the Challenge 6 sample claims so the demo is reproducible.
    c4 = json.load(open(os.path.join(SAMPLES, "crash4_structured.json")))
    c3 = json.load(open(os.path.join(SAMPLES, "crash3_structured.json")))
    # crash4: same VIN, same damage, different claimant, paid 3 months ago -> recycled damage
    docs.append(_doc(412, "Andrew Bennett", c4["vehicle_year_make_model"], c4["vehicle_vin"], c4["policy_number"],
                     "2025-05-14", c4["damage_description"], "paid", 4850))
    # crash3: same holder, two other claims inside 12 months -> frequency
    docs.append(_doc(431, c3["policy_holder_name"], "2009 Honda Fit", _vin(), c3["policy_number"], "2025-02-03",
                     DAMAGE[1], "paid", 1400))
    docs.append(_doc(432, c3["policy_holder_name"], "2009 Honda Fit", _vin(), c3["policy_number"], "2025-04-22",
                     DAMAGE[2], "denied", 0))

    ensure_index(PRIOR_INDEX, [
        SimpleField(name="claim_id", type=SearchFieldDataType.String),
        SimpleField(name="holder", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="vin", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="vehicle", type=SearchFieldDataType.String),
        SimpleField(name="policy_number", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="incident_date", type=SearchFieldDataType.String),
        SimpleField(name="outcome", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="paid_amount", type=SearchFieldDataType.Double),
    ])
    upload(PRIOR_INDEX, docs)
    print(f"prior claims: {len(docs)} docs -> {PRIOR_INDEX} (planted: CLM-0412 crash4 VIN dup, CLM-0431/0432 crash3 repeat claimant)")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "policies"):
        seed_policies()
    if which in ("all", "prior"):
        seed_prior()
