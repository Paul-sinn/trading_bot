"""universe_bias 테스트 (spec: specs/universe_bias.md).

유니버스 확장/편향 테스트(실험 전용). 잠긴 베이스라인 파라미터·기본 유니버스 불변. 레버리지 ETF 미혼합.
브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.universe_bias import (
    UniverseBiasReport,
    build_universe_bias,
    compute_top_shares,
    format_universe_bias_markdown,
    summarize_universe,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.universe_bias_test as ubt  # noqa: E402


def _sp(symbol, pnl, win=0.6):
    return SimpleNamespace(symbol=symbol, total_pnl=pnl, win_rate=win, trades=3)


def _win(label, pnl):
    return SimpleNamespace(label=label, pnl=pnl, return_pct=0.1, max_drawdown=0.05, trade_count=3)


def _robust(symbol_perf=(), windows=()):
    return SimpleNamespace(symbol_perf=tuple(symbol_perf), windows=tuple(windows))


def _bench(spy=0.4, qqq=0.55, eq=1.2):
    def _b(name, sym, ret):
        return SimpleNamespace(name=name, symbol=sym, cumulative_return=ret, note=None)
    return SimpleNamespace(baselines=(_b("SPY buy-hold", "SPY", spy), _b("QQQ buy-hold", "QQQ", qqq),
                                      _b("equal-weight", None, eq)))


def _perf(ret=0.75, mdd=0.13, win=0.65, pnl=750.0, trades=80):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades)


# --- 상수 잠금 / 레버리지 미혼합 ---


def test_baseline_universe_constant_unchanged():
    assert ubt.BASELINE_UNIVERSE == (
        "NVDA", "AMD", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA",
        "AVGO", "SMCI", "ARM", "MU", "TSM", "ASML", "NFLX", "ORCL", "CRM", "PLTR",
    )
    assert len(ubt.BASELINE_UNIVERSE) == 18


def test_no_leveraged_etfs_in_any_universe():
    assert ubt.LEVERAGED_ETFS.isdisjoint(ubt.BASELINE_UNIVERSE)
    assert ubt.LEVERAGED_ETFS.isdisjoint(ubt.EXPANDED_UNIVERSE)
    # SMH/SOXX/QQQ(1x)는 레버리지가 아니다.
    assert "TQQQ" not in ubt.EXPANDED_UNIVERSE and "SOXL" not in ubt.EXPANDED_UNIVERSE


def test_expanded_is_experiment_only_and_run_sim_default_unchanged():
    # 확장 유니버스는 실험 패키지에만 존재 — run_sim 기본값 불변(하드코딩 유니버스 없음).
    args = ubt.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.symbols is None
    assert args.entry_fill_model == "current"      # 기본 진입 모델 불변
    assert args.max_holding_days is None
    assert set(ubt.EXPANDED_UNIVERSE) != set(ubt.BASELINE_UNIVERSE)


def test_tradable_excludes_aux_and_leveraged_and_missing():
    available = {"NVDA", "AMD", "MU", "SPY", "QQQ", "TQQQ"}
    out = ubt._tradable(("NVDA", "AMD", "MU", "SPY", "QQQ", "TQQQ", "INTC"), available)
    assert out == ["NVDA", "AMD", "MU"]            # SPY/QQQ 보조·TQQQ 레버리지·INTC 결측 제외


# --- 순수 요약 ---


def test_compute_top_shares():
    perf = [_sp("MU", 250.0), _sp("AMD", 150.0), _sp("NVDA", 100.0), _sp("AAPL", -20.0)]
    top1, s1, top3, s3, best, worst = compute_top_shares(perf)
    assert top1 == "MU" and abs(s1 - 250.0 / 500.0) < 1e-9
    assert top3 == ("MU", "AMD", "NVDA") and abs(s3 - 1.0) < 1e-9
    assert best == "MU" and worst == "AAPL"


def test_summarize_missing_and_zero_trade():
    requested = ("NVDA", "AMD", "MU", "INTC", "MRVL")    # INTC/MRVL 결측
    present = ["NVDA", "AMD", "MU"]
    robustness = _robust(symbol_perf=[_sp("NVDA", 100.0), _sp("MU", 200.0)],  # AMD 무거래
                         windows=[_win("2025-Q2", 50.0)])
    r = summarize_universe("baseline", requested, present, _perf(), robustness, _bench())
    assert set(r.missing) == {"INTC", "MRVL"}
    assert r.zero_trade == ("AMD",)
    assert r.quarterly == (("2025-Q2", 50.0),)
    assert r.beats_spy is True and r.beats_qqq is True


def test_summarize_no_trades_universe_safe():
    r = summarize_universe("x", ("FOO",), [], None, _robust(), SimpleNamespace(baselines=()))
    assert r.cumulative_return is None and r.trades == 0
    assert r.missing == ("FOO",)


# --- build / 경고 ---


def test_build_flags_expanded_equals_baseline_and_mu_dependence():
    base = summarize_universe("baseline", ubt.BASELINE_UNIVERSE, ["NVDA", "AMD", "MU"],
                              _perf(pnl=750.0), _robust([_sp("MU", 400.0), _sp("AMD", 200.0)]), _bench())
    expanded = summarize_universe("expanded", ubt.EXPANDED_UNIVERSE, ["NVDA", "AMD", "MU"],
                                  _perf(pnl=750.0), _robust([_sp("MU", 400.0)]), _bench())
    no_mu = summarize_universe("expanded_no_mu", tuple(s for s in ubt.EXPANDED_UNIVERSE if s != "MU"),
                               ["NVDA", "AMD"], _perf(pnl=400.0), _robust([_sp("AMD", 200.0)]), _bench())
    rep = build_universe_bias([base, expanded, no_mu])
    assert any("새 심볼을 추가하지 못함" in w for w in rep.warnings)   # expanded가 새 심볼 못 더함
    assert any("MU 의존" in w for w in rep.warnings)            # 750→400 = -47%
    assert rep.real_orders_placed == 0


def test_format_markdown_has_sections():
    base = summarize_universe("baseline", ubt.BASELINE_UNIVERSE, ["NVDA", "MU"],
                              _perf(), _robust([_sp("MU", 250.0)], [_win("2025-Q2", 50.0)]), _bench())
    rep = build_universe_bias([base])
    md = format_universe_bias_markdown(rep)
    assert "# Universe Expansion / Bias Test" in md
    assert "## 변형 비교" in md
    assert "## 결측·무거래 심볼" in md
    assert "질문에 대한 답" in md
    assert "real_orders_placed = 0" in md


# --- 러너: 잠긴 베이스라인 / 브로커 미사용 ---


def test_runner_locked_baseline_no_broker(monkeypatch):
    monkeypatch.setattr(ubt.run_sim, "_feature_inputs",
                        lambda a: ({"NVDA": object(), "AMD": object(), "MU": object(), "AAPL": object()}, None))
    monkeypatch.setattr(ubt.run_sim, "_final_marks", lambda a, r: {})
    monkeypatch.setattr(ubt, "compute_trade_diagnostics", lambda md, final_prices=None: SimpleNamespace(trades=()))
    monkeypatch.setattr(ubt, "compute_robustness_report",
                        lambda md, pd, trade_diag=None: _robust([_sp("MU", 200.0), _sp("AMD", 100.0), _sp("NVDA", 50.0)],
                                                                [_win("2025-Q2", 50.0)]))
    monkeypatch.setattr(ubt, "compute_baseline_comparison",
                        lambda perf, pd, **k: _bench())
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.max_holding_days,
                         args.stop_loss_pct, args.trailing_stop_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols), tuple(args.symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=_perf(), real_orders_placed=0)

    report, error = ubt.run_universe_bias(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    assert isinstance(report, UniverseBiasReport)
    assert report.real_orders_placed == 0
    for model, buf, mh, stop, trail, sm, wk, syms in captured:
        assert model == "next-bar-limit" and model != "next-open"
        assert buf == 0.03 and mh == 60 and stop == 0.15 and trail == 0.20 and sm == "fractional"
        assert wk == ()                                  # weekend_exit 빈 집합
        assert ubt.LEVERAGED_ETFS.isdisjoint(syms)       # 레버리지 미혼합
        assert set(syms) <= {"NVDA", "AMD", "MU", "AAPL"}  # 결측·보조 제외
    names = [v.name for v in report.variants]
    assert names == ["baseline", "expanded", "expanded_no_mu", "expanded_no_top3"]
