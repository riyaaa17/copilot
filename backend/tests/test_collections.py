import json

import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from app.agents import collections_agent as agent
from app.models.tables import Counterparty, DraftStatus, EmailDraft
from app.tools import collections_tools as col


def facts(tone="firm", invoices=None):
    invoices = invoices or [dict(invoice_id=1, invoice_no="INV-00001", amount=1000.0,
                                 due_date="2026-08-01", days_overdue=60),
                            dict(invoice_id=2, invoice_no="INV-00002", amount=250.5,
                                 due_date="2026-09-01", days_overdue=29)]
    return dict(customer="Acme", tone=tone, invoices=invoices,
                total_amount=round(sum(i["amount"] for i in invoices), 2),
                sender_name="Riya", company_name="Demo Co")


def good_email(f) -> str:
    lines = " ".join(f"{i['invoice_no']} for ${i['amount']:,.2f} (due {i['due_date']})"
                     for i in f["invoices"])
    body = (f"Hi {f['customer']} team, we are following up on {lines}. The total outstanding is "
            f"${f['total_amount']:,.2f}. Could you please confirm the date we can expect payment, "
            f"or tell us if there is any problem with these invoices? Thank you, {f['sender_name']}, "
            f"{f['company_name']}")
    return json.dumps(dict(subject="Overdue invoices", body=body))


# ---------- tiers and prioritisation ----------
def test_tier_boundaries():
    assert col.tier_for(1) == ("reminder", "friendly")
    assert col.tier_for(14) == ("reminder", "friendly")
    assert col.tier_for(15) == ("follow_up", "firm")
    assert col.tier_for(46) == ("escalation", "urgent")
    assert col.tier_for(91) == ("final_notice", "urgent")


def open_ar_df(rows):
    base = dict(invoice_no="X", counterparty_id=1, amount=1000.0, days_overdue=30,
                p_collect_13w=0.5, expected_pay_date=None)
    return pd.DataFrame([{**base, "invoice_id": i + 1, **r} for i, r in enumerate(rows)])


INV = pd.DataFrame({"id": [1, 2, 3, 4], "due_date": pd.to_datetime(["2026-08-01"] * 4)})
PROFILES = pd.DataFrame({"counterparty_id": [1, 2], "payment_behavior": ["prompt", "chronic"]})
NAMES = {1: "Acme", 2: "Globex"}


def test_priority_is_amount_times_risk_not_just_amount():
    ar = open_ar_df([dict(counterparty_id=1, amount=10_000.0, days_overdue=5, p_collect_13w=0.99),
                     dict(counterparty_id=2, amount=5_000.0, days_overdue=120, p_collect_13w=0.1)])
    p = col.prioritize(ar, INV, PROFILES, NAMES)
    assert list(p["customer"]) == ["Globex", "Acme"]   # smaller but much riskier comes first


def test_invoices_not_yet_overdue_are_ignored():
    ar = open_ar_df([dict(days_overdue=0), dict(days_overdue=-10)])
    assert col.prioritize(ar, INV, PROFILES, NAMES).empty


def test_held_invoices_are_kept_out_of_emails():
    ar = open_ar_df([dict(days_overdue=30), dict(days_overdue=40, amount=9_999.0)])
    p = col.prioritize(ar, INV, PROFILES, NAMES, held_ids={2})
    groups = col.group_by_customer(p)
    assert [i["invoice_id"] for i in groups[0]["invoices"]] == [1]
    assert p.loc[p.invoice_id == 2, "hold_reason"].notna().all()


def test_customer_with_only_held_invoices_gets_no_email():
    p = col.prioritize(open_ar_df([dict()]), INV, PROFILES, NAMES, held_ids={1})
    assert col.group_by_customer(p) == []


def test_one_email_per_customer_with_tone_set_by_worst_invoice():
    ar = open_ar_df([dict(days_overdue=5), dict(days_overdue=100)])
    g = col.group_by_customer(col.prioritize(ar, INV, PROFILES, NAMES))
    assert len(g) == 1 and len(g[0]["invoices"]) == 2 and g[0]["tone"] == "urgent"


# ---------- guardrails ----------
def test_template_passes_its_own_checks_in_every_tone():
    for tone in ("friendly", "firm", "urgent"):
        f = facts(tone)
        t = col.render_template(f)
        assert col.check_draft(f, t["subject"], t["body"]) == [], tone


def test_check_catches_invented_amount():
    f = facts()
    bad = json.loads(good_email(f))["body"] + " We may add a $50.00 charge."
    assert any("not one of the supplied amounts" in p for p in col.check_draft(f, "s", bad))


def test_check_catches_missing_invoice_number_and_invented_date():
    f = facts()
    body = json.loads(good_email(f))["body"].replace("INV-00002", "invoice two") + " by 2026-12-25"
    problems = col.check_draft(f, "s", body)
    assert any("missing invoice number" in p for p in problems)
    assert any("2026-12-25" in p for p in problems)


def test_check_catches_threats():
    f = facts()
    body = json.loads(good_email(f))["body"] + " Otherwise we will take legal action."
    assert any("forbidden" in p for p in col.check_draft(f, "s", body))


# ---------- the LLM wrapper ----------
def test_good_llm_draft_is_used():
    f = facts()
    r = agent.draft_for_customer(f, lambda s, p: good_email(f))
    assert r["source"] == "llm" and r["warnings"] is None


def test_json_inside_code_fences_is_accepted():
    f = facts()
    r = agent.draft_for_customer(f, lambda s, p: "```json\n" + good_email(f) + "\n```")
    assert r["source"] == "llm"


def test_llm_that_invents_numbers_falls_back_to_template():
    f = facts()
    wrong = json.dumps(dict(subject="Hi", body="Please pay $9,999.00 for INV-00001 and INV-00002 "
                                              "today, total $9,999.00. " + "word " * 25))
    r = agent.draft_for_customer(f, lambda s, p: wrong)
    assert r["source"] == "template" and "not one of the supplied amounts" in r["warnings"]


def test_retry_succeeds_after_one_bad_reply():
    f = facts()
    replies = iter(["sorry, here you go!", good_email(f)])
    r = agent.draft_for_customer(f, lambda s, p: next(replies))
    assert r["source"] == "llm"


def test_llm_outage_does_not_break_drafting():
    def boom(s, p): raise TimeoutError("network")
    r = agent.draft_for_customer(facts(), boom)
    assert r["source"] == "template" and "LLM call failed" in r["warnings"]


def test_no_llm_uses_template():
    assert agent.draft_for_customer(facts(), None)["source"] == "template"


# ---------- approval workflow ----------
@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Counterparty(id=1, name="Acme", email="ap@acme.example.com"))
        s.add(EmailDraft(counterparty_id=1, to_email="ap@acme.example.com", subject="Hi & bye",
                         body="Body text", invoice_ids="1,2", total_amount=10.0))
        s.commit()
        yield s


def test_nothing_is_approved_or_sent_automatically(session):
    assert session.get(EmailDraft, 1).status == DraftStatus.DRAFT


def test_edit_then_approve_gives_mailto_link(session):
    agent.edit_draft(session, 1, "New subject", "New body")
    d = agent.move_draft(session, 1, DraftStatus.APPROVED)
    view = agent.draft_view(session, d)
    assert view["status"] == "approved" and view["mailto"].startswith("mailto:ap@acme.example.com")
    assert "New%20subject" in view["mailto"]


def test_cannot_skip_approval(session):
    with pytest.raises(agent.WorkflowError):
        agent.move_draft(session, 1, DraftStatus.SENT)       # draft -> sent is not allowed


def test_cannot_edit_after_approval(session):
    agent.move_draft(session, 1, DraftStatus.APPROVED)
    with pytest.raises(agent.WorkflowError):
        agent.edit_draft(session, 1, "x", "y")


def test_rejected_draft_is_final(session):
    agent.move_draft(session, 1, DraftStatus.REJECTED)
    with pytest.raises(agent.WorkflowError):
        agent.move_draft(session, 1, DraftStatus.APPROVED)


def test_missing_draft_is_reported(session):
    with pytest.raises(LookupError):
        agent.move_draft(session, 99, DraftStatus.APPROVED)