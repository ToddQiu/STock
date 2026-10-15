"""Curated company research, versioned in the existing append-only ledger."""

import json
import hashlib
from pathlib import Path

from .research_ledger import ResearchLedger


def load_company_landscape(ledger: ResearchLedger) -> dict:
    catalog = json.loads(Path(__file__).with_name("company_landscape.json").read_text(encoding="utf-8"))
    for company in catalog["companies"]:
        record = ledger.observe("company_profile", company["id"], {
            "reviewed_at": catalog["reviewed_at"], "method": catalog["method"], **company,
        })
        company.update(input_version_id=record["id"], version=record["version"], observed_at=record["observed_at"])
    return catalog


def seed_company_research(ledger: ResearchLedger, catalog: dict) -> int:
    first = ledger.cards()
    existing = {card["target"] for card in first["items"]}
    for offset in range(50, first["total"], 50):
        existing.update(card["target"] for card in ledger.cards(offset=offset)["items"])
    created = 0
    for company in catalog["companies"]:
        target = f"US:{company['ticker']}" if company["ticker"] else f"COMPANY:{company['id']}"
        if target in existing:
            continue
        identifier = hashlib.md5(f"company-research:{company['id']}".encode(), usedforsecurity=False).hexdigest()
        # Never replace a user's saved research with an automatically prepared draft.
        with ledger.session() as db:
            if db.execute("SELECT 1 FROM input_versions WHERE kind='research_card' AND identity=?", (identifier,)).fetchone():
                continue
        ledger.save_card({"id": identifier, "name": company["name"], "target": target,
                          "kind": "stock" if company["ticker"] else "cvc", "stance": "neutral",
                          "business": company["business"], "facts": company["business"],
                          "fundamentals": company["fundamentals"], "valuation": company["valuation"],
                          "macro": company["macro"], "strategic_fit": company["strategic_fit"],
                          "risks": company["risks"], "scenarios": company["scenarios"],
                          "review": "自动建立的研究草稿，尚未核验最新财报、估值与独立反证；不是已经完成的投资判断。",
                          "evidence": [{"role": "support", "url": source["url"],
                                        "excerpt": f"官网业务陈述：{company['business']}", "as_of": source.get("published_at") or ""}
                                       for source in company["sources"]]})
        created += 1
    return created
