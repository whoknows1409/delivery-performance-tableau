"""Delivery-performance analysis: the numbers behind the Tableau dashboard.

Usage (from the repo root, after `python src/build_model.py`):
    python src/analyze.py

Writes outputs/*.csv. `outputs/expected_dashboard_values.csv` lists every headline number
the dashboard shows, so the Tableau build can be checked against an independent calculation.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "tableau"
OUT = ROOT / "outputs"
MIN_SELLER_ORDERS = 30   # sellers below this have too few orders for a stable late rate


def pct(x: float) -> float:
    return round(100 * x, 1)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    o = pd.read_csv(DATA / "orders.csv")
    items = pd.read_csv(DATA / "items.csv")
    d = o[o.is_delivered == 1]                       # delivery metrics use delivered orders only
    on_time, late = d[d.is_late == 0], d[d.is_late == 1]
    results = []

    def add(section: str, metric: str, value, note: str = "") -> None:
        results.append({"section": section, "metric": metric, "value": value, "note": note})

    # ---- 1. Overview ----------------------------------------------------------------
    add("Overview", "Orders (Jan 2017 - Aug 2018)", len(o))
    add("Overview", "Delivered orders", len(d))
    add("Overview", "Product revenue (BRL millions)", round(o.product_value.sum() / 1e6, 2))
    add("Overview", "Freight charged (BRL millions)", round(o.freight_value.sum() / 1e6, 2))
    add("Overview", "Average order value (BRL)", round(o.product_value.mean(), 2), "orders with items")
    add("Overview", "Average review score", round(o.review_score.mean(), 2), "all reviewed orders")
    add("Overview", "Late delivery rate (%)", pct(d.is_late.mean()), "delivered after promised date")
    add("Overview", "Late orders", int(d.is_late.sum()))
    add("Overview", "Avg delivery time (days)", round(d.total_delivery_days.mean(), 1))
    add("Overview", "Avg promised time (days)", round(d.promised_days.mean(), 1))
    add("Overview", "Delivered 10+ days before promise (%)", pct((d.days_vs_promise <= -10).mean()),
        "promise dates are heavily padded")

    # ---- 2. Late deliveries drive bad reviews -----------------------------------------
    add("Reviews", "Avg review, on-time orders", round(on_time.review_score.mean(), 2))
    add("Reviews", "Avg review, late orders", round(late.review_score.mean(), 2))
    add("Reviews", "1-2 star share, on-time (%)", pct((on_time.review_score <= 2).mean()))
    add("Reviews", "1-2 star share, late (%)", pct((late.review_score <= 2).mean()))
    by_bucket = (d.groupby("delay_bucket")
                  .agg(orders=("order_id", "count"), avg_review=("review_score", "mean"),
                       one_two_star_pct=("review_score", lambda s: 100 * (s <= 2).mean()))
                  .round(2))
    order = ["On time or early", "1-3 days late", "4-7 days late", "8-14 days late", "15+ days late"]
    by_bucket.loc[order].to_csv(OUT / "review_by_delay_bucket.csv")

    # ---- 3. Where does the delay happen? (seller handling vs carrier transit) --------
    legs = pd.DataFrame({
        "on_time_avg_days": [on_time.approval_hours.mean() / 24, on_time.seller_handling_days.mean(),
                             on_time.carrier_transit_days.mean()],
        "late_avg_days": [late.approval_hours.mean() / 24, late.seller_handling_days.mean(),
                          late.carrier_transit_days.mean()],
    }, index=["Payment approval", "Seller handling", "Carrier transit"]).round(2)
    legs["extra_days_when_late"] = (legs.late_avg_days - legs.on_time_avg_days).round(2)
    legs["share_of_extra_pct"] = (100 * legs.extra_days_when_late / legs.extra_days_when_late.sum()).round(1)
    legs.to_csv(OUT / "delay_by_leg.csv")
    add("Delay legs", "Extra days, late vs on-time", round(legs.extra_days_when_late.sum(), 1))
    add("Delay legs", "Share of extra time in carrier transit (%)", legs.loc["Carrier transit", "share_of_extra_pct"])
    add("Delay legs", "Share of extra time in seller handling (%)", legs.loc["Seller handling", "share_of_extra_pct"])

    # ---- 4. Sellers missing the carrier hand-over deadline ----------------------------
    missed = d[d.seller_missed_deadline == 1]
    met = d[d.seller_missed_deadline == 0]
    add("Sellers", "Orders where seller missed hand-over deadline (%)", pct(len(missed) / len(d)))
    add("Sellers", "Late rate when seller missed deadline (%)", pct(missed.is_late.mean()))
    add("Sellers", "Late rate when seller met deadline (%)", pct(met.is_late.mean()))
    avoidable = len(missed) * (missed.is_late.mean() - met.is_late.mean())
    add("Sellers", "Scenario: late orders avoided if missed-deadline orders matched the rest",
        round(avoidable), "association, not proven cause")
    add("Sellers", "Scenario: late rate after fix (%)", pct((d.is_late.sum() - avoidable) / len(d)))

    seller_orders = (items[["order_id", "seller_id", "seller_state"]].drop_duplicates(["order_id", "seller_id"])
                     .merge(d[["order_id", "is_late", "review_score"]], on="order_id"))
    sc = seller_orders.groupby(["seller_id", "seller_state"]).agg(
        delivered_orders=("order_id", "nunique"), late_orders=("is_late", "sum"),
        late_rate=("is_late", "mean"), avg_review=("review_score", "mean")).reset_index()
    big = sc[sc.delivered_orders >= MIN_SELLER_ORDERS].sort_values("late_rate", ascending=False)
    big.round(3).to_csv(OUT / "seller_scorecard.csv", index=False)
    worst = big.head(round(0.1 * len(big)))
    add("Sellers", f"Sellers with {MIN_SELLER_ORDERS}+ delivered orders", len(big))
    add("Sellers", "Worst 10% of those sellers: share of their orders (%)",
        pct(worst.delivered_orders.sum() / big.delivered_orders.sum()))
    add("Sellers", "Worst 10% of those sellers: share of their late orders (%)",
        pct(worst.late_orders.sum() / big.late_orders.sum()))
    add("Sellers", "Correlation: seller late rate vs avg review", round(big.late_rate.corr(big.avg_review), 2))

    # ---- 5. Geography -----------------------------------------------------------------
    region = d.groupby("customer_region").agg(
        orders=("order_id", "count"), late_rate_pct=("is_late", lambda s: 100 * s.mean()),
        avg_delivery_days=("total_delivery_days", "mean"), avg_promised_days=("promised_days", "mean"),
        median_freight_pct=("freight_pct_of_value", "median"), avg_review=("review_score", "mean"),
    ).round(2).sort_values("late_rate_pct", ascending=False)
    region.to_csv(OUT / "region_performance.csv")
    state = d.groupby(["customer_state", "customer_state_name"]).agg(
        orders=("order_id", "count"), late_rate_pct=("is_late", lambda s: 100 * s.mean()),
        avg_delivery_days=("total_delivery_days", "mean"), avg_review=("review_score", "mean"),
    ).round(2).sort_values("late_rate_pct", ascending=False)
    state.to_csv(OUT / "state_performance.csv")
    far = d.customer_region.isin(["North", "Northeast"])
    add("Geography", "North + Northeast: share of delivered orders (%)", pct(far.mean()))
    add("Geography", "North + Northeast: share of late orders (%)", pct(d[far].is_late.sum() / d.is_late.sum()))
    add("Geography", "Late rate Northeast (%)", region.loc["Northeast", "late_rate_pct"])
    add("Geography", "Late rate Southeast (%)", region.loc["Southeast", "late_rate_pct"])
    add("Geography", "Median freight as % of product value, North", region.loc["North", "median_freight_pct"])
    add("Geography", "Median freight as % of product value, Southeast", region.loc["Southeast", "median_freight_pct"])
    add("Geography", "Items shipped by Sao Paulo sellers (%)", pct((items.seller_state == "SP").mean()))
    same = d.groupby("same_state_as_seller").total_delivery_days.mean()
    add("Geography", "Avg delivery days, same-state seller", round(same.loc[1], 1))
    add("Geography", "Avg delivery days, cross-state seller", round(same.loc[0], 1))

    # ---- 6. Trend ------------------------------------------------------------------
    monthly = d.groupby("purchase_month").agg(
        delivered_orders=("order_id", "count"), late_rate_pct=("is_late", lambda s: 100 * s.mean()),
        avg_review=("review_score", "mean")).round(2)
    monthly.to_csv(OUT / "monthly_trend.csv")
    peak = monthly.late_rate_pct.idxmax()
    add("Trend", "Worst month for late deliveries", f"{peak} ({monthly.loc[peak, 'late_rate_pct']}%)")
    add("Trend", "Nov 2017 (Black Friday) late rate (%)", monthly.loc["2017-11-01", "late_rate_pct"])

    res = pd.DataFrame(results)
    res.to_csv(OUT / "expected_dashboard_values.csv", index=False)
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
