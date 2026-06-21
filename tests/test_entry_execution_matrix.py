"""entry_execution_matrix 테스트 (spec: specs/entry_execution_matrix.md).

진입 실행 정책(current / next-bar-limit 1·2·3% / next-open) 실제 시뮬 비교. 60일 베이스라인 잠금,
winner extension 미적용, weekend 비움(일반주 미적용). real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import entry_execution_matrix as eem  # noqa: E402


def _fake_result(pnl=260.0, cum=0.26, mdd=0.07):
    md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
    perf = SimpleNamespace(cumulative_return=cum, max_drawdown=mdd, win_rate=0.55,
                           total_pnl=pnl, num_trades=48, num_closed_trades=48)
    return SimpleNamespace(performance=perf, multiday=md, real_orders_placed=0, portfolio=md.portfolio)


def _settings(**over):
    base = dict(data_root="x", benchmark="SPY", symbols=None, events_csv=None, assume_no_events=True)
    base.update(over)
    return base


# --- 정책 그리드 ---


def test_generate_policies():
    pols = eem.generate_policies()
    names = [p[0] for p in pols]
    assert names == ["current", "next-bar-limit-1%", "next-bar-limit-2%", "next-bar-limit-3%", "next-open"]
    # (name, model, buffer)
    by = {p[0]: p for p in pols}
    assert by["current"][1] == "current"
    assert by["next-bar-limit-2%"] == ("next-bar-limit-2%", "next-bar-limit", 0.02)
    assert by["next-open"][1] == "next-open"


# --- args 반영 (주입 simulate_fn) ---


def test_policy_args_reflect_model_buffer_and_locked_baseline(monkeypatch):
    monkeypatch.setattr(eem.run_sim, "_final_marks", lambda a, r: {})
    captured = {}

    def _fake(args):
        captured[args.entry_fill_model, args.entry_limit_buffer_pct] = SimpleNamespace(
            max_holding_days=args.max_holding_days, stop=args.stop_loss_pct, trail=args.trailing_stop_pct,
            share_mode=args.share_mode, weekend=tuple(args.weekend_exit_symbols),
        )
        return _fake_result()

    rep = eem.compute_entry_execution_matrix(**_settings(), simulate_fn=_fake)
    assert len(rep.policies) == 5
    # 베이스라인 잠금: 모든 정책이 60/0.15/0.20/fractional, weekend 비움.
    for snap in captured.values():
        assert snap.max_holding_days == 60
        assert snap.stop == 0.15 and snap.trail == 0.20
        assert snap.share_mode == "fractional"
        assert snap.weekend == ()
    assert ("next-bar-limit", 0.03) in captured        # buffer 반영
    assert ("current", 0.03) in captured or any(k[0] == "current" for k in captured)
    assert rep.real_orders_placed == 0


def test_best_by_return_mdd(monkeypatch):
    monkeypatch.setattr(eem.run_sim, "_final_marks", lambda a, r: {})

    def _fake(args):
        # 2% 정책이 최고 return/MDD.
        if args.entry_fill_model == "next-bar-limit" and abs(args.entry_limit_buffer_pct - 0.02) < 1e-9:
            return _fake_result(pnl=300.0, cum=0.40, mdd=0.05)   # ratio 8.0
        return _fake_result(pnl=260.0, cum=0.26, mdd=0.07)       # ratio ~3.7

    rep = eem.compute_entry_execution_matrix(**_settings(), simulate_fn=_fake)
    assert rep.best_by_return_mdd.name == "next-bar-limit-2%"
    assert rep.best_by_return_mdd.return_mdd_ratio == 8.0


def test_fail_closed_per_policy(monkeypatch):
    monkeypatch.setattr(eem.run_sim, "_final_marks", lambda a, r: {})

    def _fake(args):
        if args.entry_fill_model == "next-open":
            raise eem.run_sim.DataAdapterError("벤치마크 없음")
        return _fake_result()

    rep = eem.compute_entry_execution_matrix(**_settings(), simulate_fn=_fake)
    by = {p.name: p for p in rep.policies}
    assert by["next-open"].error is not None
    assert by["current"].error is None             # 나머지는 계속
    assert rep.real_orders_placed == 0


def test_format_contains_key_metrics(monkeypatch):
    monkeypatch.setattr(eem.run_sim, "_final_marks", lambda a, r: {})
    rep = eem.compute_entry_execution_matrix(**_settings(), simulate_fn=lambda a: _fake_result())
    text = eem.format_entry_execution_matrix(rep)
    for token in ("Entry Execution", "cum_ret", "MDD", "win", "PnL", "ret/MDD", "real_orders_placed"):
        assert token.lower() in text.lower(), token


def test_metrics_holding_and_exits():
    from agents.trade_diagnostics import TradeLeg
    legs = [
        TradeLeg(symbol="AMD", entry_date="2025-01-02", exit_date="2025-03-03", entry_price=100.0,
                 exit_price=150.0, qty=1.0, pnl=50.0, pnl_pct=0.5, exit_reason="time_stop"),
        TradeLeg(symbol="NVDA", entry_date="2025-01-02", exit_date="2025-01-20", entry_price=100.0,
                 exit_price=90.0, qty=1.0, pnl=-10.0, pnl_pct=-0.1, exit_reason="stop_loss_hit"),
    ]
    perf = SimpleNamespace(cumulative_return=0.4, max_drawdown=0.1, win_rate=0.5, total_pnl=40.0,
                           num_trades=2, num_closed_trades=2)
    p = eem._policy_metrics("next-open", "next-open", None, perf, legs, "2025-04-01")
    assert p.trades == 2
    assert dict(p.exit_reason_dist) == {"time_stop": 1, "stop_loss_hit": 1}
    assert p.return_mdd_ratio == 4.0
    assert p.top_symbol == "AMD"
    assert p.weekend_exit_count == 0
    assert p.real_orders_placed == 0
