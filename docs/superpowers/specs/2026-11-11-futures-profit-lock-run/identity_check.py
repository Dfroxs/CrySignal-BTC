# Identity check for the be_lock_r knob (H-PLK prereg, Discard). Usage:
#   git show 7b35144:backtest.py > <dir>/backtest_old.py   # the simulator before the knob
#   ./venv/bin/python identity_check.py <dir>
import sys, copy
sys.path.insert(0, "/Users/dfroxs/Playground/Python/SpotSignal"); sys.path.insert(1, sys.argv[1])
import numpy as np, pandas as pd
import backtest as new, backtest_old as old
from backtest import _exit_signal
from scripts.exit_ic import _signal_at
rng = np.random.default_rng(11)
tot = diff = 0
for seed in range(6):
    rng = np.random.default_rng(seed)
    n = 4000
    ret = rng.normal(0, 0.006, n) + (0.0004 if seed % 2 else -0.0004)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                       "ATR_14": close * 0.006}, index=pd.date_range("2024-01-01", periods=n, freq="1h"))
    for i in range(250, n - 80, 7):
        raw = _signal_at(df, i)
        for kind in ("BUY", "SELL"):
            sig = _exit_signal(dict(raw), "futures")
            if kind == "SELL":
                e = sig["entry_price"]
                sig = dict(sig, type="SELL", stop_loss=2*e - sig["stop_loss"], take_profit=2*e - sig["take_profit"], tp2=2*e - sig["tp2"])
            for params in (None, {}, {"trailing_atr_factor": 2.5}):
                a = old._simulate_forward(df, i, copy.deepcopy(sig), 72, "1h", "futures", exit_params=params)
                s2 = copy.deepcopy(sig)
                b = new._simulate_forward(df, i, s2, 72, "1h", "futures", exit_params=params)
                tot += 1
                if a != b or "_lock_armed" in s2:
                    diff += 1
print(f"compared {tot} simulations (BUY+SELL, exit_params None / {{}} / trail 2.5): {diff} differences")
sys.exit(1 if diff else 0)
