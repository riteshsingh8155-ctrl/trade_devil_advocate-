"""
run_analysis.py
================
Command-line runner for the SMC/ICT multi-timeframe analyzer.

USAGE
-----
    python run_analysis.py --4h data_4h.csv --1h data_1h.csv \
        --15m data_15m.csv --5m data_5m.csv --1m data_1m.csv

CSV FORMAT (each file, one per timeframe)
------------------------------------------
    Datetime,Open,High,Low,Close,Volume
    2026-08-01 09:15,100.0,100.5,99.8,100.2,12000
    ...

Where to get these CSVs:
  * TradingView: right-click chart -> Export chart data
  * Your broker/exchange terminal export
  * yfinance (run locally, needs internet):
        import yfinance as yf
        for tf, interval, period in [("4h","4h","730d"),("1h","1h","730d"),
                                      ("15m","15m","60d"),("5m","5m","60d"),
                                      ("1m","1m","7d")]:
            yf.download("EURUSD=X", period=period, interval=interval)\\
              .to_csv(f"data_{tf}.csv")
    (yfinance limits: 1m data only last 7 days, 5m/15m only last 60 days)
"""

import argparse
import sys
import pandas as pd
from smc_ict_core import analyze, print_report


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # find the datetime-like column
    dt_col = None
    for c in df.columns:
        if c.lower() in ("datetime", "date", "time", "timestamp"):
            dt_col = c
            break
    if dt_col is None:
        dt_col = df.columns[0]
    df[dt_col] = pd.to_datetime(df[dt_col])
    df = df.set_index(dt_col).sort_index()

    # normalize column names
    rename_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "open":
            rename_map[c] = "Open"
        elif cl == "high":
            rename_map[c] = "High"
        elif cl == "low":
            rename_map[c] = "Low"
        elif cl == "close":
            rename_map[c] = "Close"
        elif cl == "volume":
            rename_map[c] = "Volume"
    df = df.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}. "
                          f"Found columns: {list(df.columns)}")
    if "Volume" not in df.columns:
        df["Volume"] = 0
    return df[["Open", "High", "Low", "Close", "Volume"]]


def main():
    p = argparse.ArgumentParser(description="SMC/ICT multi-timeframe analyzer")
    p.add_argument("--4h", dest="tf4h", required=True, help="CSV path for 4H data")
    p.add_argument("--1h", dest="tf1h", required=True, help="CSV path for 1H data")
    p.add_argument("--15m", dest="tf15m", required=True, help="CSV path for 15M data")
    p.add_argument("--5m", dest="tf5m", required=True, help="CSV path for 5M data")
    p.add_argument("--1m", dest="tf1m", required=True, help="CSV path for 1M data")
    args = p.parse_args()

    try:
        df_4h = load_csv(args.tf4h)
        df_1h = load_csv(args.tf1h)
        df_15m = load_csv(args.tf15m)
        df_5m = load_csv(args.tf5m)
        df_1m = load_csv(args.tf1m)
    except Exception as e:
        print(f"Error loading data: {e}", file=sys.stderr)
        sys.exit(1)

    sig = analyze(df_4h, df_1h, df_15m, df_5m, df_1m)
    print_report(sig)


if __name__ == "__main__":
    main()
