from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import TypeAdapter, ValidationError
from app.agents import orchestrator as o
from app.tools import forecast as fx
from app.tools import whatif as wi
from app.tools.analytics import INVOICE_COLS
from app.tools.forecast import Scenario, run_forecast

AS_OF = date(2026, 6, 30)
NO_BANK = pd.DataFrame(columns=["txn_date", "amount", "category"]).astype(
    {"txn_date": "datetime64[ns]", "amount": float, "category": object})
EFFECT = TypeAdapter(wi.Effect)


def make_inv(rows):
    base = dict(invoice_no="X", type="AR", counterparty_id=1, category=None, issue_date="2026-03-01",
                due_date="2026-03-31", paid_date=None, amount=100.0, status="open")
    df = pd.DataFrame([{**base, "id": i + 1, **r} for i, r in enumerate(rows)], columns=INVOICE_COLS)
    for c in ("issue_date", "due_date", "paid_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def paid(n=10, cid=1, kind="AR", days_late=0):
    day = str((pd.Timestamp("2026-03-31") + pd.Timedelta(days=days_late)).date())
    return [dict(type=kind, counterparty_id=cid, status="paid", paid_date=day)] * n


def due_in(days):  # an open invoice due `days` from AS_OF (negative = overdue)
    return str((pd.Timestamp(AS_OF) + pd.Timedelta(days=days)).date())


def run(rows, scenario=None, bank=NO_BANK, cash=10_000.0, sims=200):
    return run_forecast(make_inv(rows), bank, AS_OF, cash, n_sims=sims, scenario=scenario)


def driver(fc, week, name):
    return fc.weeks[week - 1]["drivers"].get(name, 0.0)


# ---------- the engine ----------
def test_an_empty_scenario_changes_nothing_at_all():
    rows = paid(10, days_late=15) + [dict(issue_date="2026-06-03", due_date=due_in(3), amount=1000.0)]
    a, b = run(rows), run(rows, Scenario())
    assert np.array_equal(a.balance, b.balance) and a.summary == b.summary
    assert all(np.array_equal(a.components[k], b.components[k]) for k in a.components)


def test_customer_pays_in_week_n_collects_the_overdue_invoice_then():
    rows = paid(10, days_late=40) + [dict(issue_date="2026-05-20", due_date=due_in(-10), amount=1000.0)]
    base, alt = run(rows), run(rows, Scenario(pay_in_weeks=((1, 1),)))
    assert base.open_ar.iloc[0]["expected_pay_date"] == str(AS_OF + timedelta(days=30))   # week 5 normally
    assert alt.open_ar.iloc[0]["p_collect_13w"] == 1.0
    assert alt.open_ar.iloc[0]["expected_pay_date"] == str(AS_OF + timedelta(days=4))     # week 1
    assert driver(alt, 1, "Collections: open invoices") == 1000.0
    assert driver(base, 1, "Collections: open invoices") == 0.0


def test_invoices_not_yet_due_are_left_to_the_normal_pattern():
    rows = paid(10, days_late=0) + [dict(issue_date="2026-06-20", due_date=due_in(20), amount=1000.0)]
    base, alt = run(rows), run(rows, Scenario(pay_in_weeks=((1, 1),)))
    assert base.open_ar.iloc[0]["expected_pay_date"] == alt.open_ar.iloc[0]["expected_pay_date"]


def test_a_customer_who_never_pays_collects_nothing_and_bills_nothing():
    rows = paid(10, days_late=40) + [dict(issue_date="2026-05-20", due_date=due_in(-10), amount=1000.0)]
    base, alt = run(rows), run(rows, Scenario(fail_customers=frozenset({1})))
    assert alt.open_ar.iloc[0]["p_collect_13w"] == 0.0
    assert alt.components["Collections: open invoices"].sum() == 0.0
    assert alt.components["Collections: new billings"].sum() == 0.0
    assert base.components["Collections: new billings"].sum() > 0.0


def test_slower_customers_push_cash_into_later_weeks():
    rows = paid(10) + [dict(issue_date="2026-06-20", due_date=due_in(3), amount=1000.0)]
    base, alt = run(rows), run(rows, Scenario(ar_shift_days=14))
    assert driver(base, 1, "Collections: open invoices") == 1000.0
    assert driver(alt, 1, "Collections: open invoices") == 0.0
    assert driver(alt, 3, "Collections: open invoices") == 1000.0   # 3 + 14 = day 17, week 3


def test_paying_earlier_cannot_land_in_the_past():
    rows = paid(10) + [dict(issue_date="2026-06-20", due_date=due_in(3), amount=1000.0)]
    alt = run(rows, Scenario(ar_shift_days=-30))
    assert driver(alt, 1, "Collections: open invoices") == 1000.0


def test_paying_vendors_later_moves_the_outflow():
    rows = paid(3, cid=2, kind="AP") + [dict(type="AP", counterparty_id=2, issue_date="2025-01-01",
                                             due_date="2025-02-01", amount=300.0)]
    base, alt = run(rows), run(rows, Scenario(ap_shift_days=14))
    assert driver(base, 1, "Vendor payments: open bills") == -300.0
    assert driver(alt, 1, "Vendor payments: open bills") == 0.0
    assert driver(alt, 3, "Vendor payments: open bills") == -300.0


PAYROLL_BANK = pd.DataFrame(
    [dict(txn_date=d, amount=-5000.0, category="payroll") for d in pd.date_range("2026-01-31", periods=6, freq="ME")]
    + [dict(txn_date=d, amount=-1000.0, category="rent") for d in pd.date_range("2026-01-01", periods=6, freq="MS")])


def test_moving_payroll_moves_it_to_the_right_week_and_keeps_the_amount():
    base, alt = run([], bank=PAYROLL_BANK), run([], Scenario(recurring_shift_days=(("payroll", 7),)), bank=PAYROLL_BANK)
    assert driver(base, 5, "Recurring: payroll") == -5000.0 and driver(alt, 5, "Recurring: payroll") == 0.0
    assert driver(alt, 6, "Recurring: payroll") == -5000.0                      # Jul 31 + 7 = Aug 7
    assert sum(w["drivers"]["Recurring: payroll"] for w in alt.weeks) == -10000.0
    assert driver(base, 1, "Recurring: rent") == driver(alt, 1, "Recurring: rent")   # rent untouched


def test_a_payment_pushed_past_the_horizon_drops_out_of_it():
    def payroll_total(days):
        alt = run([], Scenario(recurring_shift_days=(("payroll", days),)), bank=PAYROLL_BANK)
        return sum(w["drivers"].get("Recurring: payroll", 0.0) for w in alt.weeks)
    assert payroll_total(60) == -5000.0    # Jul 31 + 60 = Sep 29, the last day of the window; Aug 31 falls out
    assert payroll_total(90) == 0.0        # both fall outside the 13 weeks


def test_one_off_events_land_in_their_week_and_stay_in_the_balance():
    base = run([], bank=PAYROLL_BANK)
    alt = run([], Scenario(one_offs=((3, -500.0, "Equipment"),)), bank=PAYROLL_BANK)
    assert driver(alt, 3, "One-off: Equipment") == -500.0
    assert alt.weeks[-1]["closing_p50"] == pytest.approx(base.weeks[-1]["closing_p50"] - 500.0)
    assert alt.weeks[2]["outflows"] < base.weeks[2]["outflows"]            # counted as an outflow
    receipt = run([], Scenario(one_offs=((2, 900.0, "Loan"),)), bank=PAYROLL_BANK)
    assert receipt.weeks[1]["inflows"] == pytest.approx(base.weeks[1]["inflows"] + 900.0)


def test_two_one_offs_with_the_same_name_are_both_kept():
    alt = run([], Scenario(one_offs=((1, -100.0, "Repair"), (2, -50.0, "Repair"))))
    assert sum(k.startswith("One-off: Repair") for k in alt.components) == 2


# ---------- comparing ----------
def test_driver_differences_add_up_to_the_change_in_expected_cash():
    rows = paid(10, days_late=15) + [dict(issue_date="2026-05-20", due_date=due_in(-10), amount=1000.0)]
    sc = Scenario(ar_shift_days=7, one_offs=((2, -300.0, "Repair"),), pay_in_weeks=((1, 2),))
    base, alt = run(rows, bank=PAYROLL_BANK), run(rows, sc, bank=PAYROLL_BANK)
    result = wi.compare(base, alt)
    total = sum(d["difference"] for d in result["drivers"])
    assert total == pytest.approx(alt.balance[:, -1].mean() - base.balance[:, -1].mean(), abs=1.0)


def test_a_no_op_scenario_has_zero_difference_in_every_week():
    rows = paid(10, days_late=15) + [dict(issue_date="2026-05-20", due_date=due_in(-10), amount=1000.0)]
    result = wi.compare(run(rows), run(rows, wi.build_scenario([wi.CustomersPayLater(type="customers_pay_later", days=0)])))
    assert all(w["difference"] == 0 for w in result["weeks"]) and result["drivers"] == []
    assert result["headline"]["verdict"] == "about the same"
    assert "unchanged" in wi.summary_sentence(result)


def test_summary_sentence_says_the_right_direction():
    rows = paid(10) + [dict(issue_date="2026-06-20", due_date=due_in(3), amount=50_000.0)]
    base = run(rows, cash=100_000.0)
    worse = wi.compare(base, run(rows, Scenario(one_offs=((2, -40_000.0, "Repair"),)), cash=100_000.0))
    better = wi.compare(base, run(rows, Scenario(one_offs=((2, 40_000.0, "Loan"),)), cash=100_000.0))
    assert worse["headline"]["verdict"] == "worse" and "lower than" in wi.summary_sentence(worse)
    assert better["headline"]["verdict"] == "better" and "higher than" in wi.summary_sentence(better)


# ---------- building and validating scenarios ----------
def test_effects_are_merged_into_one_scenario():
    sc = wi.build_scenario([
        wi.CustomersPayLater(type="customers_pay_later", days=5), wi.CustomersPayLater(type="customers_pay_later", days=3),
        wi.ShiftRecurring(type="shift_recurring", category="payroll", days=7),
        wi.CustomerPays(type="customer_pays", counterparty_id=4, weeks=2),
        wi.OneOff(type="one_off", week=1, amount=-10, description="x")])
    assert sc.ar_shift_days == 8 and dict(sc.recurring_shift_days) == {"payroll": 7}
    assert dict(sc.pay_in_weeks) == {4: 2} and len(sc.one_offs) == 1


def test_a_customer_who_never_pays_cannot_also_pay_in_week_n():
    sc = wi.build_scenario([wi.CustomerPays(type="customer_pays", counterparty_id=4, weeks=2),
                            wi.CustomerFails(type="customer_fails", counterparty_id=4)])
    assert dict(sc.pay_in_weeks) == {} and 4 in sc.fail_customers


@pytest.mark.parametrize("bad", [
    {"type": "customer_pays", "counterparty_id": 1, "weeks": 0},
    {"type": "customer_pays", "counterparty_id": 1, "weeks": 14},
    {"type": "customers_pay_later", "days": 999},
    {"type": "one_off", "week": 1, "amount": 5, "description": ""},
    {"type": "something_else"}])
def test_nonsense_effects_are_rejected(bad):
    with pytest.raises(ValidationError):
        EFFECT.validate_python(bad)


def test_unknown_customers_and_categories_are_reported_in_plain_words():
    with pytest.raises(ValueError, match="not found"):
        wi.validate_effects([wi.CustomerFails(type="customer_fails", counterparty_id=99)], {1: "Acme"}, ["payroll"])
    with pytest.raises(ValueError, match="salary"):
        wi.validate_effects([wi.ShiftRecurring(type="shift_recurring", category="salary", days=3)], {}, ["payroll"])


def test_assumptions_read_as_plain_english():
    text = " ".join(wi.describe([
        wi.CustomerPays(type="customer_pays", counterparty_id=1, weeks=1),
        wi.ShiftRecurring(type="shift_recurring", category="payroll", days=7),
        wi.CustomersPayLater(type="customers_pay_later", days=-5),
        wi.OneOff(type="one_off", week=3, amount=-50000, description="Equipment purchase")], {1: "Acme"}))
    assert "Acme pays its already-overdue invoices in week 1" in text
    assert "payroll payment is moved 7 days later" in text
    assert "5 days earlier" in text and "one-off payment of $50,000 in week 3" in text
    fees = wi.describe([wi.ShiftRecurring(type="shift_recurring", category="bank_fees", days=3)], {})[0]
    assert "bank fees" in fees and "_" not in fees


# ---------- the chat tools ----------
def make_ctx():
    rows = paid(10, days_late=40) + [dict(issue_date="2026-05-20", due_date=due_in(-10), amount=1000.0)]
    inv = make_inv(rows)
    base = run_forecast(inv, PAYROLL_BANK, AS_OF, 10_000.0, n_sims=200)
    names = {1: "Acme Corp", 2: "Acme Labs", 3: "Zenith"}
    return SimpleNamespace(inv=inv, bank=PAYROLL_BANK, as_of=AS_OF, cash=10_000.0, dup=frozenset(), held=frozenset(),
                           names=names, customer_names=names, forecast=base)


def test_chat_tool_reports_assumption_effect_and_drivers():
    r = o.t_wi_customer_pays(make_ctx(), {"customer": "Acme Corp", "weeks": 1})
    assert r["assumptions"][0].startswith("Acme Corp pays") and r["verdict"] in {"better", "worse", "about the same"}
    assert set(r["cash_at_week_13"]) == {"without_change", "with_change", "difference"}
    assert r["cash_at_week_13"]["difference"].startswith(("+", "-", "$"))


def test_chat_tool_asks_when_two_customers_match():
    r = o.t_wi_customer_pays(make_ctx(), {"customer": "acme", "weeks": 1})
    assert r["ambiguous"] and set(r["candidates"]) == {"Acme Corp", "Acme Labs"}


def test_chat_tool_rejects_unknown_things_without_crashing():
    ctx = make_ctx()
    assert "error" in o.t_wi_customer_pays(ctx, {"customer": "Nobody Ltd", "weeks": 1})
    assert "salary" in o.t_wi_move_payment(ctx, {"category": "salary", "days": 7})["error"]
    assert "number" in o.t_wi_one_off(ctx, {"week": 1, "amount": "lots", "description": "x"})["error"]


def test_chat_tool_clamps_silly_values():
    ctx = make_ctx()
    assert "week 13" in o.t_wi_customer_pays(ctx, {"customer": "Acme Corp", "weeks": 99})["assumptions"][0]
    assert "90 days later" in o.t_wi_customers_pay_later(ctx, {"days": 5000})["assumptions"][0]


def test_every_what_if_tool_is_registered_with_required_parameters():
    names = {s["function"]["name"] for s in o.TOOL_SPECS}
    assert {n for n in names if n.startswith("what_if_")} == {n for n in o.TOOLS if n.startswith("what_if_")}
    assert len([n for n in names if n.startswith("what_if_")]) == 6