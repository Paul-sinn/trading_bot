"""walk_forward 테스트 (spec: specs/walk_forward.md).

잠긴 next-bar-limit 3% 베이스라인을 날짜 윈도우/워크포워드로 검증. 리포트 전용, 입력 불변.
next-open/winner extension 미사용. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.walk_forward import (
    WalkForwardValidation,
    build_walk_forward,
    compute_walk_forward_summary,
    format_walk_forward,
    generate_windows,
    make_window_result,
)


def _perf(ret=0.2, mdd=0.1, win=0.6, pnl=200.0, trades=10):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades)


def _bench(spy=0.1, qqq=0.12, eq=0.15, missing=()):
    def _b(name, sym, ret):
        return SimpleNamespace(name=name, symbol=sym, cumulative_return=ret,
                               return_diff_vs_strategy=None, note=None)
    baselines = [
        _b("SPY buy-hold", "SPY", None if "SPY" in missing else spy),
        _b("QQQ buy-hold", "QQQ", None if "QQQ" in missing else qqq),
        _b("equal-weight", None, None if "eq" in missing else eq),
    ]
    return SimpleNamespace(baselines=tuple(baselines))


def _wr(label, ret, mdd=0.1, kind="roll6", start="2025-01-01", end="2025-06-30"):
    return make_window_result(label, kind, start, end, _perf(ret=ret, mdd=mdd), _bench())


# --- 윈도우 생성 ---


def test_generate_windows_full_year_rolling():
    wins = generate_windows("2024-01-01", "2025-12-31")
    kinds = {}
    for w in wins:
        kinds.setdefault(w.kind, []).append(w)
    assert kinds["full"][0].start is None and kinds["full"][0].end is None
    assert {w.label for w in kinds["year"]} == {"2024", "2025"}
    # 24개월, 6m 윈도우 3m 스텝: 시작 2024-01..2025-07 → 7개.
    assert len(kinds["roll6"]) == 7
    # 12m 윈도우 3m 스텝: 시작 2024-01..2025-01 → 5개.
    assert len(kinds["roll12"]) == 5
    for w in kinds["roll6"] + kinds["roll12"]:
        assert w.start <= w.end


def test_rolling_empty_when_data_too_short():
    wins = generate_windows("2025-01-01", "2025-04-30")   # 4개월
    assert not [w for w in wins if w.kind == "roll6"]      # 6m 안 들어감
    assert not [w for w in wins if w.kind == "roll12"]
    assert [w for w in wins if w.kind == "full"]           # full은 항상


def test_yearly_intersects_data_range():
    wins = generate_windows("2025-03-15", "2025-09-20", roll6=False, roll12=False)
    year = next(w for w in wins if w.kind == "year")
    assert year.start == "2025-03-15" and year.end == "2025-09-20"


# --- WindowResult (벤치/eq 결측 안전) ---


def test_make_window_result_beats():
    r = make_window_result("w", "roll6", "2025-01-01", "2025-06-30", _perf(ret=0.2), _bench(spy=0.1, qqq=0.25))
    assert r.beats_spy is True       # 0.2 > 0.1
    assert r.beats_qqq is False      # 0.2 < 0.25
    assert r.eq_return == 0.15


def test_make_window_result_missing_benchmark_safe():
    r = make_window_result("w", "roll6", "2025-01-01", "2025-06-30", _perf(ret=0.2),
                           _bench(missing=("SPY", "eq")))
    assert r.spy_return is None and r.beats_spy is None
    assert r.eq_return is None        # equal-weight 결측 안전
    assert r.beats_qqq is True


def test_make_window_result_no_trades_window():
    r = make_window_result("w", "roll6", "2025-01-01", "2025-06-30", None, _bench())
    assert r.return_pct is None and r.trades == 0


# --- 요약 / 양수·음수 카운트 ---


def test_summary_counts_and_extremes():
    results = [_wr("a", 0.2), _wr("b", -0.05), _wr("c", 0.1), _wr("d", -0.15, mdd=0.2)]
    s = compute_walk_forward_summary(results)
    assert s.positive_windows == 2
    assert s.negative_windows == 2
    assert s.best_window.label == "a"
    assert s.worst_window.label == "d"
    assert abs(s.avg_return - (0.2 - 0.05 + 0.1 - 0.15) / 4) < 1e-9
    assert s.worst_drawdown == 0.2


# --- build / 경고 / bull_dependent ---


def test_build_flags_bull_dependent_and_bad_window():
    roll6 = [_wr("2025-01-01~2025-06-30", 0.2, start="2025-01-01", end="2025-06-30"),
             _wr("2025-07-01~2025-12-31", -0.12, start="2025-07-01", end="2025-12-31")]
    summary = compute_walk_forward_summary(roll6)
    rep = build_walk_forward(None, (), roll6, (), summary,
                             data_start="2025-01-01", data_end="2025-12-31")
    assert rep.bull_dependent is True                       # 전부 2025
    assert any("강세장 밖" in w or "regime" in w for w in rep.warnings)
    assert any("큰 손실" in w for w in rep.warnings)         # -12% 윈도우
    assert rep.real_orders_placed == 0


def test_build_not_bull_dependent_when_pre_2025():
    roll12 = [_wr("2023", 0.1, kind="roll12", start="2023-01-01", end="2023-12-31")]
    summary = compute_walk_forward_summary(roll12)
    rep = build_walk_forward(None, (), (), roll12, summary,
                             data_start="2023-01-01", data_end="2023-12-31")
    assert rep.bull_dependent is False


def test_format_includes_window_counts():
    roll6 = [_wr("a", 0.2), _wr("b", -0.05)]
    summary = compute_walk_forward_summary(roll6)
    rep = build_walk_forward(_wr("full", 0.5, kind="full"), (), roll6, (), summary,
                             data_start="2025-01-01", data_end="2025-12-31")
    text = format_walk_forward(rep)
    assert "positive 1" in text and "negative 1" in text
    assert "rolling 6-month" in text
    assert "real_orders_placed : 0" in text


# --- 러너: 잠긴 베이스라인 / next-open·winner extension 미사용 ---


def test_runner_locked_baseline_no_next_open(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import walk_forward as wrun

    monkeypatch.setattr(wrun.run_sim, "_feature_inputs", lambda a: ({}, None))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.max_holding_days,
                         args.stop_loss_pct, args.trailing_stop_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols), args.start_date, args.end_date))
        return SimpleNamespace(performance=_perf(), real_orders_placed=0)

    win = SimpleNamespace(label="2025H1", kind="roll6", start="2025-01-01", end="2025-06-30")
    report, error = wrun.run_walk_forward(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake, windows=[win])
    assert error is None
    assert captured, "윈도우 시뮬이 호출되어야 한다"
    for model, buf, mh, stop, trail, sm, wk, *_ in captured:
        assert model == "next-bar-limit" and model != "next-open"
        assert buf == 0.03 and mh == 60 and stop == 0.15 and trail == 0.20 and sm == "fractional" and wk == ()
    assert not any("winner" in str(c).lower() or "gap" in str(c).lower() for c in captured)
    assert report.real_orders_placed == 0
    assert isinstance(report, WalkForwardValidation)
