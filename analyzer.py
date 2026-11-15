"""
Gold Price Strategy Analyzer
----------------------------
Downloads daily gold prices (XAU/USD), tests a moving-average crossover
trading strategy on them, compares it with simply buying and holding, and
draws charts of the result.

  python3 analyzer.py                              # gold, 50/200-day averages, since 2015
  python3 analyzer.py --fast 20 --slow 100         # try different averages
  python3 analyzer.py --sweep                      # test many combinations and rank them
  python3 analyzer.py --symbol aapl.us             # any symbol from stooq.com
  python3 analyzer.py --file my_prices.csv         # use your own CSV (needs Date and Close columns)

This is an educational project, not financial advice.
"""

import argparse
import csv
import io
import math
import os
import sys
import urllib.request
from datetime import datetime

DATA_DIR = "data"
CHARTS_DIR = "charts"
TRADING_DAYS = 252


# ---------- 1. Getting the data ----------

def download_prices(symbol):
    """Download daily prices as CSV from stooq.com (free, no API key) and cache them in data/."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    print(f"⬇️  Downloading {symbol} prices from stooq.com ...")
    with urllib.request.urlopen(url, timeout=20) as response:
        text = response.read().decode("utf-8")
    if not text.startswith("Date"):
        raise ValueError(f"stooq returned no data for '{symbol}'")
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{symbol}.csv")
    with open(path, "w") as f:
        f.write(text)
    return path


YAHOO_SYMBOLS = {"xauusd": "GC=F", "xagusd": "SI=F"}   # gold and silver futures on Yahoo Finance


def download_prices_yahoo(symbol):
    """Backup source: Yahoo Finance through the yfinance package (pip3 install yfinance)."""
    import yfinance as yf
    ticker = YAHOO_SYMBOLS.get(symbol, symbol.split(".")[0].upper())
    print(f"⬇️  Trying Yahoo Finance ({ticker}) instead ...")
    df = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"Yahoo returned no data for '{ticker}'")
    closes = df["Close"].squeeze()
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{symbol}.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Close"])
        for d, c in closes.dropna().items():
            writer.writerow([d.strftime("%Y-%m-%d"), f"{float(c):.4f}"])
    return path


def load_prices(path, start=None):
    """Read Date and Close columns. Skips blank or broken rows and returns them oldest-first."""
    dates, closes = [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                d = datetime.strptime(row["Date"][:10], "%Y-%m-%d")
                c = float(row["Close"])
            except (KeyError, ValueError, TypeError):
                continue
            if start and d < start:
                continue
            dates.append(d)
            closes.append(c)
    pairs = sorted(zip(dates, closes))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def get_prices(args):
    if args.file:
        path = args.file
    else:
        path = os.path.join(DATA_DIR, f"{args.symbol}.csv")
        if args.refresh or not os.path.exists(path):
            try:
                path = download_prices(args.symbol)
            except Exception as e:
                print(f"   stooq.com didn't work ({e}).")
                try:
                    path = download_prices_yahoo(args.symbol)
                except Exception as e2:
                    sys.exit(f"❌ Could not download data ({e2}).\n"
                             f"   Fix: pip3 install yfinance   and run again, or\n"
                             f"   download a CSV with Date and Close columns and run:\n"
                             f"   python3 analyzer.py --file your_file.csv")
    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    dates, closes = load_prices(path, start)
    if len(closes) < args.slow + 10:
        sys.exit(f"❌ Only {len(closes)} days of data. Need more than {args.slow} for a {args.slow}-day average.")
    return dates, closes


# ---------- 2. The strategy ----------

def moving_average(values, window):
    """Simple moving average using a running sum. Returns None until there's enough data."""
    result, running = [], 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        result.append(running / window if i >= window - 1 else None)
    return result


def backtest(closes, fast, slow, cash=10_000, fee=0.001):
    """
    Moving-average crossover, long only:
      - BUY  when the fast average crosses ABOVE the slow average
      - SELL when it crosses back BELOW
    The signal is decided at today's close but the trade happens at TOMORROW's close.
    This avoids "look-ahead bias" (using information you couldn't have had yet).
    `fee` is charged on every buy and sell (0.001 = 0.1%).
    """
    fast_ma = moving_average(closes, fast)
    slow_ma = moving_average(closes, slow)

    equity, position = [], 0          # position = 1 when we own gold, 0 when in cash
    units, money = 0.0, cash
    trades, buys, sells = [], [], []
    entry_price = None
    pending = None                     # trade decided yesterday, done today

    for i, price in enumerate(closes):
        # Do yesterday's decision at today's price
        if pending == "buy":
            units = money * (1 - fee) / price
            money, position, entry_price = 0.0, 1, price
            buys.append(i)
        elif pending == "sell":
            money = units * price * (1 - fee)
            trades.append(price / entry_price - 1)
            units, position = 0.0, 0
            sells.append(i)
        pending = None

        # Decide today's signal
        if fast_ma[i] is not None and slow_ma[i] is not None and fast_ma[i - 1] is not None and slow_ma[i - 1] is not None:
            crossed_up = fast_ma[i - 1] <= slow_ma[i - 1] and fast_ma[i] > slow_ma[i]
            crossed_down = fast_ma[i - 1] >= slow_ma[i - 1] and fast_ma[i] < slow_ma[i]
            if crossed_up and position == 0:
                pending = "buy"
            elif crossed_down and position == 1:
                pending = "sell"

        equity.append(money + units * price)

    if position == 1:                  # count the still-open trade in the stats
        trades.append(closes[-1] / entry_price - 1)

    return {"equity": equity, "fast_ma": fast_ma, "slow_ma": slow_ma,
            "buys": buys, "sells": sells, "trades": trades}


def buy_and_hold(closes, cash=10_000, fee=0.001):
    units = cash * (1 - fee) / closes[0]
    return [units * p for p in closes]


# ---------- 3. Measuring performance ----------

def stats(equity, dates):
    """Total return, yearly return (CAGR), worst drop (max drawdown) and Sharpe ratio."""
    total = equity[-1] / equity[0] - 1
    years = (dates[-1] - dates[0]).days / 365.25
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 else 0

    peak, max_dd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)

    daily = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    mean = sum(daily) / len(daily)
    std = math.sqrt(sum((r - mean) ** 2 for r in daily) / (len(daily) - 1))
    sharpe = mean / std * math.sqrt(TRADING_DAYS) if std > 0 else 0
    return {"total": total, "cagr": cagr, "max_dd": max_dd, "sharpe": sharpe, "final": equity[-1]}


def print_report(symbol, dates, fast, slow, strat, hold, result):
    trades = result["trades"]
    wins = [t for t in trades if t > 0]
    print()
    print(f"📈 {symbol.upper()}  |  {dates[0]:%Y-%m-%d} to {dates[-1]:%Y-%m-%d}  |  {len(dates):,} trading days")
    print(f"   Strategy: buy when {fast}-day average crosses above {slow}-day average, sell when it crosses below")
    print("=" * 62)
    print(f"{'':<22}{'CROSSOVER':>18}{'BUY & HOLD':>18}")
    print(f"{'Final value of $10k':<22}{strat['final']:>18,.0f}{hold['final']:>18,.0f}")
    print(f"{'Total return':<22}{strat['total']:>18.1%}{hold['total']:>18.1%}")
    print(f"{'Yearly return (CAGR)':<22}{strat['cagr']:>18.1%}{hold['cagr']:>18.1%}")
    print(f"{'Worst drop (max DD)':<22}{strat['max_dd']:>18.1%}{hold['max_dd']:>18.1%}")
    print(f"{'Sharpe ratio':<22}{strat['sharpe']:>18.2f}{hold['sharpe']:>18.2f}")
    print("-" * 62)
    print(f"Trades: {len(trades)}   Winning: {len(wins)}   "
          f"Win rate: {len(wins) / len(trades):.0%}" if trades else "Trades: 0")
    if trades:
        print(f"Best trade: {max(trades):+.1%}   Worst trade: {min(trades):+.1%}")
    better = "beat" if strat["sharpe"] > hold["sharpe"] else "did NOT beat"
    print(f"\n➡️  On a risk-adjusted basis (Sharpe), the crossover {better} buy & hold.")


# ---------- 4. Charts ----------

def draw_chart(symbol, dates, closes, result, hold_equity, fast, slow):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(Skipping chart: install matplotlib with  pip3 install matplotlib)")
        return None

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(13, 10), sharex=True,
                                        gridspec_kw={"height_ratios": [3, 2, 1]})
    fig.suptitle(f"{symbol.upper()}: {fast}/{slow}-day moving-average crossover", fontsize=15, fontweight="bold")

    ax1.plot(dates, closes, color="goldenrod", linewidth=1, label="Price")
    ax1.plot(dates, result["fast_ma"], color="tab:blue", linewidth=1, label=f"{fast}-day avg")
    ax1.plot(dates, result["slow_ma"], color="tab:purple", linewidth=1, label=f"{slow}-day avg")
    ax1.scatter([dates[i] for i in result["buys"]], [closes[i] for i in result["buys"]],
                marker="^", color="green", s=80, zorder=5, label="Buy")
    ax1.scatter([dates[i] for i in result["sells"]], [closes[i] for i in result["sells"]],
                marker="v", color="red", s=80, zorder=5, label="Sell")
    ax1.set_ylabel("Price (USD)")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    ax2.plot(dates, result["equity"], color="tab:green", label="Crossover strategy")
    ax2.plot(dates, hold_equity, color="gray", linestyle="--", label="Buy & hold")
    ax2.set_ylabel("Value of $10,000")
    ax2.legend(loc="upper left")
    ax2.grid(alpha=0.3)

    peak, dd = result["equity"][0], []
    for v in result["equity"]:
        peak = max(peak, v)
        dd.append((v / peak - 1) * 100)
    ax3.fill_between(dates, dd, 0, color="tab:red", alpha=0.4)
    ax3.set_ylabel("Drawdown %")
    ax3.grid(alpha=0.3)

    os.makedirs(CHARTS_DIR, exist_ok=True)
    path = os.path.join(CHARTS_DIR, f"{symbol}_{fast}_{slow}.png")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    return path


# ---------- 5. Parameter sweep ----------

def sweep(symbol, dates, closes, cash, fee):
    """Try many fast/slow combinations and rank them by Sharpe ratio."""
    print(f"\n🔬 Testing moving-average combinations on {symbol.upper()} ...\n")
    rows = []
    for fast in (10, 20, 50, 100):
        for slow in (50, 100, 150, 200, 250):
            if fast >= slow or slow + 10 > len(closes):
                continue
            r = backtest(closes, fast, slow, cash, fee)
            s = stats(r["equity"], dates)
            rows.append((fast, slow, s, len(r["trades"])))
    rows.sort(key=lambda r: r[2]["sharpe"], reverse=True)
    hold = stats(buy_and_hold(closes, cash, fee), dates)
    print(f"{'FAST':>5}{'SLOW':>6}{'CAGR':>9}{'MAX DD':>9}{'SHARPE':>8}{'TRADES':>8}")
    print("-" * 45)
    for fast, slow, s, n in rows:
        print(f"{fast:>5}{slow:>6}{s['cagr']:>9.1%}{s['max_dd']:>9.1%}{s['sharpe']:>8.2f}{n:>8}")
    print("-" * 45)
    print(f"{'Buy & hold':>11}{hold['cagr']:>9.1%}{hold['max_dd']:>9.1%}{hold['sharpe']:>8.2f}{1:>8}")
    print("\n⚠️  The best row is the best on PAST data. Picking it and expecting the same")
    print("   result in the future is called overfitting.")


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Backtest a moving-average crossover strategy on gold prices.")
    parser.add_argument("--symbol", default="xauusd", help="stooq.com symbol (default: xauusd = gold in USD)")
    parser.add_argument("--file", help="use a local CSV with Date and Close columns instead of downloading")
    parser.add_argument("--start", default="2015-01-01", help="first date to test, YYYY-MM-DD")
    parser.add_argument("--fast", type=int, default=50, help="fast moving average in days")
    parser.add_argument("--slow", type=int, default=200, help="slow moving average in days")
    parser.add_argument("--cash", type=float, default=10_000)
    parser.add_argument("--fee", type=float, default=0.001, help="fee per trade (0.001 = 0.1%%)")
    parser.add_argument("--sweep", action="store_true", help="test many fast/slow combinations")
    parser.add_argument("--refresh", action="store_true", help="download fresh data even if cached")
    args = parser.parse_args()

    if args.fast >= args.slow:
        sys.exit("❌ --fast must be smaller than --slow")

    name = os.path.splitext(os.path.basename(args.file))[0] if args.file else args.symbol
    dates, closes = get_prices(args)

    if args.sweep:
        sweep(name, dates, closes, args.cash, args.fee)
        return

    result = backtest(closes, args.fast, args.slow, args.cash, args.fee)
    hold_equity = buy_and_hold(closes, args.cash, args.fee)
    print_report(name, dates, args.fast, args.slow,
                 stats(result["equity"], dates), stats(hold_equity, dates), result)
    path = draw_chart(name, dates, closes, result, hold_equity, args.fast, args.slow)
    if path:
        print(f"\n🖼️  Chart saved to {path}")


if __name__ == "__main__":
    main()
