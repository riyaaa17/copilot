import json

import pandas as pd
from app.agents import reporting_agent as agent
from app.tools import reporting as rep


# ---------- sample data ----------
def zero_anomalies():
    return dict(dup_ar_count=0, dup_ar_amount=0.0, dup_ap_count=0, dup_ap_amount=0.0,
                double_paid_count=0, double_paid_amount=0.0, outlier_count=0, outlier_amount=0.0)


def sample_data(**over):
    aging = [dict(bucket=b, count=1, amount=100.0, pct=20.0)
             for b in ["Not yet due", "1-30", "31-60", "61-90", "90+"]]
    k = dict(cash=900_000.0, open_ar=500_000.0, overdue_ar=150_000.0, overdue_pct=30.0,
             ar_90_plus=20_000.0, open_ap=80_000.0, dso=45.0, dpo=50.0, aging=aging)
    line = lambda n, f, a: dict(name=n, forecast=f, actual=a, variance=a - f,
                                variance_pct=None if abs(f) < 1 else round((a - f) / abs(f) * 100, 1))
    data = dict(
        as_of="2026-09-29", kpis=k, kpis_prev={**k, "cash": 700_000.0, "overdue_pct": 40.0, "dso": 55.0},
        forecast=dict(opening_cash=900_000.0, closing_p50_week13=750_000.0, change_pct=-16.7,
                      lowest_p10_balance=400_000.0, lowest_p10_week=5, prob_negative_cash_pct=0.0,
                      open_ar_at_risk_amount=90_000.0, open_ar_at_risk_count=4,
                      expected_collections_from_open_ar=300_000.0),
        variance=[line("Customer collections", 100_000.0, 150_000.0), line("Vendor payments", -5_000.0, -4_000.0),
                  line("Payroll, rent, tax and fees", 0.0, 0.0), line("Net cash flow", 95_000.0, 146_000.0)],
        variance_period="Forecast made on 2026-09-22 compared with actual bank activity.",
        outflow_weeks=[dict(week=5, start="2026-10-28", end="2026-11-03", net=-200_000.0,
                            drivers=[dict(name="Recurring: payroll", amount=-240_000.0)])],
        collections=dict(top_customers=[dict(customer="Acme", total_amount=70_000.0, exposure=40_000.0,
                                             max_days_overdue=101, invoice_count=3, tier="final_notice",
                                             tone="urgent")],
                         at_risk_amount=90_000.0, at_risk_count=4, held_count=0),
        anomalies=zero_anomalies(), drafts_pending=dict(count=0, amount=0.0))
    data.update(over)
    data["actions"] = rep.build_actions(data)
    return data


# ---------- formatting ----------
def test_money_formatting():
    assert rep.money(747934.6) == "$747,935"
    assert rep.money(-12000) == "-$12,000"
    assert rep.money(-0.2) == "$0"
    assert rep.signed_money(5) == "+$5" and rep.signed_money(-5) == "-$5"
    assert rep.pct(10.26) == "10.3%"

def test_money_rounds_halves_up_like_the_browser():
    assert rep.money(55794.5) == "$55,795" and rep.money(0.5) == "$1" and rep.money(2.5) == "$3"
    assert rep.money(-2.5) == "-$3" and rep.money(-0.4) == "$0" and rep.money(1234567.49) == "$1,234,567"
    assert rep.signed_money(55794.5) == "+$55,795" and rep.signed_money(0.2) == "$0"
# ---------- rewind ----------
def test_rewind_undoes_later_payments_and_drops_later_invoices():
    inv = pd.DataFrame(dict(
        id=[1, 2], issue_date=pd.to_datetime(["2026-01-01", "2026-03-01"]),
        paid_date=pd.to_datetime(["2026-02-15", None]), status=["paid", "open"]))
    bank = pd.DataFrame(dict(txn_date=pd.to_datetime(["2026-02-01", "2026-02-20"]), amount=[1.0, 2.0]))
    i, b = rep.rewind(inv, bank, "2026-02-10")
    assert list(i["id"]) == [1] and i.iloc[0]["status"] == "open" and pd.isna(i.iloc[0]["paid_date"])
    assert list(b["amount"]) == [1.0]


# ---------- variance ----------
def test_variance_table_compares_forecast_with_bank_activity():
    week = dict(drivers={"Collections: open invoices": 60.0, "Collections: new billings": 40.0,
                         "Vendor payments: open bills": -30.0, "Recurring: payroll": -50.0})
    actual = pd.DataFrame(dict(category=["customer_receipt", "vendor_payment", "payroll", None],
                               amount=[120.0, -20.0, -50.0, -9.0]))
    rows = {r["name"]: r for r in rep.variance_table(week, actual)}
    assert rows["Customer collections"]["forecast"] == 100.0 and rows["Customer collections"]["variance"] == 20.0
    assert rows["Vendor payments"]["variance"] == 10.0
    assert rows["Payroll, rent, tax and fees"]["variance"] == 0.0
    assert rows["Net cash flow"]["forecast"] == 20.0 and rows["Net cash flow"]["actual"] == 50.0


def test_variance_pct_is_none_when_nothing_was_forecast():
    rows = rep.variance_table(dict(drivers={}), pd.DataFrame(dict(category=["rent"], amount=[-5.0])))
    assert rows[2]["variance_pct"] is None


def test_outflow_weeks_pick_the_weakest_and_name_the_drivers():
    weeks = [dict(week=w, start="s", end="e", net=n, drivers=d) for w, n, d in [
        (1, -50.0, {"Recurring: payroll": -80.0, "Collections: open invoices": 30.0}),
        (2, 10.0, {"Collections: open invoices": 10.0}),
        (3, -300.0, {"Recurring: tax": -250.0, "Recurring: rent": -50.0}),
        (4, -100.0, {"Recurring: payroll": -100.0})]]
    out = rep.outflow_weeks(weeks, n=2)
    assert [w["week"] for w in out] == [3, 4]            # nets -300 and -100; week 1 (-50) misses out
    assert out[0]["drivers"][0] == dict(name="Recurring: tax", amount=-250.0)
    assert all(d["amount"] < 0 for w in out for d in w["drivers"])


# ---------- actions ----------
def test_actions_are_ordered_by_priority_and_grounded_in_numbers():
    d = sample_data()
    priorities = [a["priority"] for a in d["actions"]]
    assert priorities == sorted(priorities, key=["high", "medium", "low"].index)
    text = " ".join(a["text"] for a in d["actions"])
    assert "$150,000" in text and "Acme" in text


def test_shortfall_risk_is_top_priority():
    d = sample_data()
    d["forecast"]["prob_negative_cash_pct"] = 12.0
    d["actions"] = rep.build_actions(d)
    assert d["actions"][0]["priority"] == "high" and "Cash shortfall risk" in d["actions"][0]["text"]


def test_pending_drafts_replace_the_generate_drafts_action():
    d = sample_data(drafts_pending=dict(count=3, amount=50_000.0))
    text = " ".join(a["text"] for a in d["actions"])
    assert "approve 3 collection email" in text and "Generate collection drafts" not in text


def test_anomaly_actions_appear_only_when_there_is_something_to_do():
    assert not any("duplicate" in a["text"] for a in sample_data()["actions"])
    a = zero_anomalies(); a.update(dup_ap_count=2, dup_ap_amount=8_000.0)
    text = " ".join(x["text"] for x in sample_data(anomalies=a)["actions"])
    assert "Hold payment on 2 flagged duplicate vendor bill" in text and "$8,000" in text


# ---------- narrative guardrail ----------
FACTS_TEXT = json.dumps({"cash_today": "$896,868", "chg": "16.6%", "var": "-$12,000"})


def test_narrative_with_supplied_figures_passes():
    n = dict(summary="Cash is $896,868, down 16.6%.", variance_commentary="Short by $12,000 last week.")
    assert rep.check_narrative(FACTS_TEXT, n) == []   # sign differences in wording are fine


def test_narrative_with_invented_or_rounded_figures_fails():
    n = dict(summary="Cash is about $897k and fell 17%.", variance_commentary="Fine.")
    problems = rep.check_narrative(FACTS_TEXT, n)
    assert any("$897" in p for p in problems) and any("17%" in p for p in problems)
    assert any("abbreviated" in p for p in problems)


def test_figures_followed_by_punctuation_are_matched_correctly():
    facts = json.dumps({"a": "$750,000", "b": "16.7%"})
    n = dict(summary="It ends at $750,000, down 16.7%. Then $750,000.", variance_commentary="ok ($750,000)")
    assert rep.check_narrative(facts, n) == []

def test_cash_described_as_down_from_the_forecast_is_rejected():
    # the real mistake a model made: every figure correct, meaning backwards
    n = dict(summary="Cash on hand is $896,868, down from the 13-week base case forecast of $747,935, "
                     "a 16.6% decline.", variance_commentary="ok")
    facts = json.dumps({"a": "$896,868", "b": "$747,935", "c": "16.6%"})
    assert any("moving from the forecast" in p for p in rep.check_narrative(facts, n))


def test_correct_direction_is_accepted():
    n = dict(summary="Cash is $896,868 today and is forecast to fall to $747,935 by week 13, "
                     "16.6% lower.", variance_commentary="ok")
    facts = json.dumps({"a": "$896,868", "b": "$747,935", "c": "16.6%"})
    assert rep.check_narrative(facts, n) == []


def test_day_and_week_figures_are_verified_too():
    facts = json.dumps({"dso": "45.3 days", "move": "-10.4 days", "low": "week 9"})
    ok = dict(summary="DSO is 45.3 days, down 10.4 days, with the low in week 9.", variance_commentary="ok")
    bad = dict(summary="DSO is 47 days and the low is in week 7.", variance_commentary="ok")
    assert rep.check_narrative(facts, ok) == []
    problems = rep.check_narrative(facts, bad)
    assert any("47day" in p for p in problems) and any("week7" in p for p in problems)


def test_ready_made_sentences_are_correctly_worded():
    s = rep.narrative_facts(sample_data())["ready_made_sentences"]
    assert "lower than today's cash" in s["outlook"] and "$750,000" in s["outlook"]


def test_ai_written_wording_is_labelled():
    d = sample_data()
    n = rep.template_narrative(rep.narrative_facts(d))
    assert "written by an AI assistant" in rep.render_markdown(d, n, "llm")
    assert "written by an AI assistant" not in rep.render_markdown(d, n, "template")

def test_narrative_with_a_computed_sum_fails():
    n = dict(summary="Cash is $896,868 and $12,001 short.", variance_commentary="ok")
    assert any("$12,001" in p for p in rep.check_narrative(FACTS_TEXT, n))


def test_empty_paragraph_fails():
    assert any("empty" in p for p in rep.check_narrative(FACTS_TEXT, dict(summary="", variance_commentary="x")))


def test_template_narrative_passes_its_own_check():
    facts = rep.narrative_facts(sample_data())
    assert rep.check_narrative(json.dumps(facts), rep.template_narrative(facts)) == []


# ---------- the document ----------
def test_markdown_has_every_section_and_every_action():
    d = sample_data()
    md = rep.render_markdown(d, rep.template_narrative(rep.narrative_facts(d)))
    for heading in ["# Weekly CFO Briefing", "## Summary", "## Key numbers", "## 13-week cash outlook",
                    "## Last week: forecast vs actual", "## Collections", "## Data quality",
                    "## Recommended actions"]:
        assert heading in md
    assert all(a["text"] in md for a in d["actions"])


def test_markdown_survives_missing_dso():
    d = sample_data()
    d["kpis"]["dso"] = None
    md = rep.render_markdown(d, rep.template_narrative(rep.narrative_facts(d)))
    assert "n/a" in md


# ---------- the LLM wrapper ----------
def good_reply(facts):
    return json.dumps(rep.template_narrative(facts))


def test_good_llm_narrative_is_used():
    facts = rep.narrative_facts(sample_data())
    n, source, warnings = agent.narrate(facts, lambda s, p: good_reply(facts))
    assert source == "llm" and warnings is None and n["summary"]


def test_code_fenced_json_is_accepted():
    facts = rep.narrative_facts(sample_data())
    assert agent.narrate(facts, lambda s, p: "```json\n" + good_reply(facts) + "\n```")[1] == "llm"


def test_llm_that_invents_a_number_falls_back_to_the_template():
    facts = rep.narrative_facts(sample_data())
    bad = json.dumps(dict(summary="Cash is $1,234,567 today.", variance_commentary="Collections beat forecast."))
    n, source, warnings = agent.narrate(facts, lambda s, p: bad)
    assert source == "template" and "$1,234,567" in warnings
    assert rep.check_narrative(json.dumps(facts), n) == []


def test_retry_after_unparseable_reply():
    facts = rep.narrative_facts(sample_data())
    replies = iter(["Here is your briefing!", good_reply(facts)])
    assert agent.narrate(facts, lambda s, p: next(replies))[1] == "llm"


def test_llm_outage_still_produces_a_briefing():
    def boom(s, p): raise ConnectionError("down")
    n, source, warnings = agent.narrate(rep.narrative_facts(sample_data()), boom)
    assert source == "template" and "LLM call failed" in warnings


def test_no_llm_uses_the_template():
    assert agent.narrate(rep.narrative_facts(sample_data()), None)[1] == "template"