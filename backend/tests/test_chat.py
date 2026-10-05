import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from app.agents import orchestrator as o
from app.tools import chat_tools as ct
from app.tools import forecast as fx


# ---------- explain_change ----------
def make_forecast(opening=1000.0):
    S, H = 4, 13
    payroll = np.zeros((S, H)); payroll[:, 1] = -300.0
    comps = {"Collections: open invoices": np.full((S, H), 100.0), "Recurring: payroll": payroll,
             "Recurring: bank_fees": np.full((S, H), -0.2)}
    balance = opening + np.cumsum(sum(comps.values()), axis=1)
    weeks = [dict(week=k + 1, start=f"s{k}", end=f"e{k}", net=float(sum(c[:, k].mean() for c in comps.values())),
                  drivers={n: float(a[:, k].mean()) for n, a in comps.items()}) for k in range(H)]
    return fx.Forecast(date(2026, 1, 1), opening, S, weeks, {}, pd.DataFrame(), comps, balance)


def test_drivers_add_up_and_name_the_real_cause():
    r = ct.explain_change(make_forecast(), 2)
    assert r["horizon"] == "2 weeks" and r["cash_today"] == "$1,000"
    assert r["expected_change"] == "-$100" and r["direction"] == "down" and r["change_pct"] == "10.0%"
    assert r["drivers"][0] == dict(driver="Payroll", kind="outflow", amount="-$300")
    assert [d["driver"] for d in r["drivers"]] == ["Payroll", "Collections: open invoices"]  # fees too small
    assert r["weekly"][0]["main_driver"] == "Collections: open invoices"
    assert r["weekly"][1]["main_driver"] == "Payroll"


def test_driver_names_are_readable():
    assert ct.driver_label("Recurring: bank_fees") == "Bank fees"


def test_claim_check_flags_a_wrong_premise_only():
    fc = make_forecast()
    assert "differs" in ct.explain_change(fc, 2, claimed_change_pct=-15)["claim_check"]
    assert "about right" in ct.explain_change(fc, 2, claimed_change_pct=-10)["claim_check"]
    assert "claim_check" not in ct.explain_change(fc, 2)


def test_horizon_is_clamped_and_bad_values_default():
    assert ct.explain_change(make_forecast(), 99)["horizon"] == "13 weeks"
    assert ct.explain_change(make_forecast(), "banana")["horizon"] == "4 weeks"


def test_no_percentage_when_there_is_no_cash_to_measure_against():
    assert ct.explain_change(make_forecast(opening=0.0), 2)["change_pct"] is None

def test_users_percentage_is_read_in_code_with_its_direction():
    assert ct.claimed_pct_from_message("Why is cash down 15% next month?") == -15.0
    assert ct.claimed_pct_from_message("Will cash rise 8.5% by March?") == 8.5
    assert ct.claimed_pct_from_message("Is 15% right?") is None          # no direction, no guess
    assert ct.claimed_pct_from_message("Why is cash down next month?") is None

# ---------- customer matching ----------
NAMES = {1: "Ironwood Industries", 2: "Ironwood Group", 3: "Cedar Solutions"}


def test_unique_match_is_returned():
    assert ct.match_customer("cedar", NAMES) == [(3, "Cedar Solutions")]
    assert ct.match_customer("Ironwood Industries", NAMES) == [(1, "Ironwood Industries")]


def test_lookalike_customers_are_never_guessed_between():
    assert {n for _, n in ct.match_customer("ironwood", NAMES)} == {"Ironwood Industries", "Ironwood Group"}

def test_matching_ignores_case_so_lowercase_queries_work():
    names = {1: "Acme Corp", 2: "Acme Labs", 3: "Zenith"}
    assert {n for _, n in ct.match_customer("acme", names)} == {"Acme Corp", "Acme Labs"}
    assert ct.match_customer("ZENITH", names) == [(3, "Zenith")]
    assert ct.match_customer("acme corp", names) == [(1, "Acme Corp")]
def test_no_match_gives_empty_list():
    assert ct.match_customer("zzzzqq", NAMES) == [] and ct.match_customer("  ", NAMES) == []


# ---------- permission gate ----------
def test_only_explicit_requests_unlock_drafting():
    assert ct.user_wants_drafts("Please draft emails to the top customers")
    assert ct.user_wants_drafts("write a reminder to Acme")
    assert not ct.user_wants_drafts("Which customers should we chase first?")
    assert not ct.user_wants_drafts("Why is cash down next month?")


# ---------- answer verification ----------
TOOL_TEXT = json.dumps(dict(cash="$896,868", end="$632,943", pct="29.4%", horizon="4 weeks", low="week 9"))


def test_answer_using_tool_figures_passes():
    assert ct.check_answer("Cash goes from $896,868 to $632,943, down 29.4% over 4 weeks.", TOOL_TEXT) == []


def test_invented_or_abbreviated_figures_fail():
    problems = ct.check_answer("Cash falls about $264k, roughly 30%.", TOOL_TEXT)
    assert any("30%" in p for p in problems) and any("abbreviated" in p for p in problems)


def test_figures_with_no_source_fail():
    assert ct.check_answer("Cash is $896,868.", "")


def test_user_quoted_figure_is_allowed_when_supplied_as_context():
    assert ct.check_answer("Not 15%: it is 29.4%.", TOOL_TEXT + "\nWhy is cash down 15%?") == []


def test_answer_without_figures_needs_no_source():
    assert ct.check_answer("I don't have data on other companies.", "") == []


def test_wrong_direction_is_rejected():
    assert any("moving from a forecast" in p for p in
               ct.check_answer("Cash is down from the base case forecast of $632,943.", TOOL_TEXT))


# ---------- the loop ----------
RESULT = dict(cash_today="$896,868", expected_cash_at_end="$632,943", change_pct="29.4%", horizon="4 weeks")


def scripted(replies, seen=None):
    it = iter(replies)

    def fn(messages, tools):
        if seen is not None:
            seen.append(json.loads(json.dumps(messages)))
        return next(it)
    return fn


def call(name="explain_cash_change", args=None, cid="c1", raw=None):
    return dict(content=None, tool_calls=[dict(id=cid, name=name, arguments=raw if raw is not None
                                               else json.dumps(args or {}))])


def say(text):
    return dict(content=text, tool_calls=[])


GOOD = "Cash goes from $896,868 to $632,943, down 29.4% over 4 weeks."


def test_tool_then_verified_answer():
    r = o.converse("Why is cash down?", [], scripted([call(args={"weeks": 4}), say(GOOD)]),
                   lambda n, a: RESULT)
    assert r["source"] == "llm" and r["verified"] and r["answer"] == GOOD
    assert r["trace"] == [dict(tool="explain_cash_change", arguments={"weeks": 4}, ok=True)]


def test_tool_results_and_calls_are_passed_back_to_the_model():
    seen = []
    o.converse("q", [dict(role="user", content="earlier"), dict(role="assistant", content="reply")],
               scripted([call(), say(GOOD)], seen), lambda n, a: RESULT)
    assert seen[0][0]["role"] == "system" and [m["role"] for m in seen[0][1:]] == ["user", "assistant", "user"]
    assert [m["role"] for m in seen[1][-2:]] == ["assistant", "tool"]
    assert seen[1][-2]["tool_calls"][0]["function"]["name"] == "explain_cash_change"


def test_figures_without_any_tool_call_are_not_trusted():
    r = o.converse("How much cash?", [], scripted([say("Cash is $5,000,000."), say("Cash is $5,000,000.")]),
                   lambda n, a: RESULT)
    assert r["verified"] is False and len(r["warnings"]) == 2


def test_one_repair_attempt_is_given():
    seen = []
    r = o.converse("Why?", [], scripted([call(), say("About $897k."), say(GOOD)], seen), lambda n, a: RESULT)
    assert r["source"] == "llm" and r["verified"] and len(r["warnings"]) == 1
    assert "Your answer had problems" in seen[-1][-1]["content"]


def test_failed_repair_uses_the_built_in_answer():
    r = o.converse("Why?", [], scripted([call(), say("About $897k."), say("Still $897k.")]),
                   lambda n, a: RESULT, fallback=lambda m: "Built-in answer.")
    assert r["source"] == "fallback" and r["answer"] == "Built-in answer." and r["verified"]


def test_no_llm_uses_the_built_in_answer():
    r = o.converse("Why?", [], None, lambda n, a: RESULT, fallback=lambda m: "Built-in answer.")
    assert r["source"] == "fallback" and "no LLM configured" in r["warnings"]


def test_unknown_question_without_llm_gets_a_helpful_message():
    r = o.converse("Meaning of life?", [], None, lambda n, a: RESULT, fallback=lambda m: None)
    assert "Try asking about cash" in r["answer"]


def test_llm_outage_falls_back_instead_of_crashing():
    def boom(messages, tools): raise ConnectionError("down")
    r = o.converse("Why?", [], boom, lambda n, a: RESULT, fallback=lambda m: "Built-in answer.")
    assert r["source"] == "fallback" and "LLM call failed" in r["warnings"][0]


def test_endless_tool_calling_is_stopped():
    r = o.converse("Why?", [], lambda m, t: call(), lambda n, a: RESULT, fallback=lambda m: "Built-in answer.")
    assert r["source"] == "fallback" and "step limit" in r["warnings"][-1]
    assert len(r["trace"]) == o.MAX_STEPS


def test_figures_from_the_question_and_earlier_turns_are_allowed():
    hist = [dict(role="assistant", content="Last month payroll was $242,478.")]
    ans = "Payroll is still $242,478, and cash is down 29.4% not 15%."
    r = o.converse("Is it really down 15%?", hist, scripted([call(), say(ans)]), lambda n, a: RESULT)
    assert r["verified"]


def test_malformed_tool_arguments_do_not_crash():
    got = {}
    def run(name, args): got["args"] = args; return RESULT
    r = o.converse("q", [], scripted([call(raw="{not json"), say(GOOD)]), run)
    assert got["args"] == {} and r["verified"]


def test_a_failing_tool_is_recorded_and_the_loop_continues():
    r = o.converse("q", [], scripted([call(), say("I could not get that figure.")]),
                   lambda n, a: dict(error="The tool failed."))
    assert r["trace"][0]["ok"] is False and r["verified"]


# ---------- tool safety ----------
def test_unknown_tool_is_an_error_not_a_crash():
    assert "Unknown tool" in o.run_tool(None, "does_not_exist", {})["error"]


def test_a_crashing_tool_is_contained(monkeypatch):
    monkeypatch.setitem(o.TOOLS, "boom", lambda ctx, a: 1 / 0)
    assert "failed" in o.run_tool(None, "boom", {})["error"]


def test_every_advertised_tool_exists_and_vice_versa():
    assert {s["function"]["name"] for s in o.TOOL_SPECS} == set(o.TOOLS)

# ---------- tool schemas (strict validators reject null for optional fields) ----------
def test_every_tool_parameter_is_required():
    for spec in o.TOOL_SPECS:
        params = spec["function"]["parameters"]
        assert set(params["required"]) == set(params["properties"]), spec["function"]["name"]


def test_the_model_is_not_asked_to_extract_the_users_percentage():
    assert "claimed_change_pct" not in json.dumps(o.TOOL_SPECS)


# ---------- built-in answers for follow-up questions ----------
def test_follow_up_inherits_the_topic_of_the_previous_question():
    from types import SimpleNamespace
    ctx = SimpleNamespace(forecast=make_forecast())
    assert o.fallback_answer(ctx, "And what about the full quarter?") is None
    text = o.fallback_answer(ctx, "And what about the full quarter?", previous="Why is cash down next month?")
    assert text and "next 13 weeks" in text


def test_percentage_in_the_previous_question_is_not_reapplied_to_the_follow_up():
    from types import SimpleNamespace
    ctx = SimpleNamespace(forecast=make_forecast())
    text = o.fallback_answer(ctx, "And the full quarter?", previous="Why is cash down 15% next month?")
    assert "differs from the user" not in text