"""Build the Tableau-ready data model from the raw Olist tables.

Usage (from the repo root):
    python src/build_model.py

Outputs (data/tableau/):
    orders.csv  one row per order: dates, customer location, value, freight, review score,
                and the delivery-time breakdown (approval -> seller handling -> carrier transit)
    items.csv   one row per order line: product category, seller and seller location, price,
                freight, the seller's shipping deadline and whether it was missed

In Tableau, relate the two tables on order_id (Tableau "relationship", not a join) so
order-level measures are never double-counted when you slice by seller or category.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "tableau"

# Analysis window: the months before Jan 2017 and after Aug 2018 have only a handful of
# orders (platform launch / data extraction cut-off), which would distort monthly trends.
START, END = "2017-01-01", "2018-09-01"

STATE_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}
REGIONS = {
    "North": ["AC", "AP", "AM", "PA", "RO", "RR", "TO"],
    "Northeast": ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
    "Central-West": ["DF", "GO", "MT", "MS"],
    "Southeast": ["ES", "MG", "RJ", "SP"],
    "South": ["PR", "RS", "SC"],
}
STATE_REGION = {s: r for r, states in REGIONS.items() for s in states}


def read(name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(RAW / f"{name}.csv.gz", **kw)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    date_cols = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
                 "order_delivered_customer_date", "order_estimated_delivery_date"]
    orders = read("olist_orders_dataset", parse_dates=date_cols)
    orders = orders[(orders.order_purchase_timestamp >= START) & (orders.order_purchase_timestamp < END)]

    customers = read("olist_customers_dataset")
    items = read("olist_order_items_dataset", parse_dates=["shipping_limit_date"])
    items = items[items.order_id.isin(orders.order_id)]
    products = read("olist_products_dataset")
    cat_en = read("product_category_name_translation")
    sellers = read("olist_sellers_dataset")
    payments = read("olist_order_payments_dataset")
    reviews = read("olist_order_reviews_dataset", parse_dates=["review_answer_timestamp"])

    # ---- item level -----------------------------------------------------------------
    products = products.merge(cat_en, on="product_category_name", how="left")
    products["category"] = (products.product_category_name_english
                            .fillna(products.product_category_name)
                            .fillna("unknown").str.replace("_", " "))
    items = (items.merge(products[["product_id", "category", "product_weight_g"]], on="product_id", how="left")
                  .merge(sellers, on="seller_id", how="left")
                  .merge(orders[["order_id", "order_delivered_carrier_date"]], on="order_id", how="left"))
    items["seller_state_name"] = items.seller_state.map(STATE_NAMES)
    items["seller_region"] = items.seller_state.map(STATE_REGION)
    # Seller missed its hand-over deadline to the carrier (only known once handed over)
    items["missed_ship_deadline"] = np.where(
        items.order_delivered_carrier_date.isna(), np.nan,
        (items.order_delivered_carrier_date > items.shipping_limit_date).astype(float))

    # ---- order level ----------------------------------------------------------------
    agg = items.groupby("order_id").agg(
        items_count=("order_item_id", "count"),
        sellers_count=("seller_id", "nunique"),
        product_value=("price", "sum"),
        freight_value=("freight_value", "sum"),
        seller_missed_deadline=("missed_ship_deadline", "max"),
    )
    # Main category / seller = the order's highest-priced line
    main = (items.sort_values(["order_id", "price"], ascending=[True, False])
                 .drop_duplicates("order_id")
                 .set_index("order_id")[["category", "seller_id", "seller_state"]]
                 .rename(columns={"category": "main_category", "seller_id": "main_seller_id",
                                  "seller_state": "main_seller_state"}))
    pay = payments.sort_values(["order_id", "payment_value"], ascending=[True, False])
    pay = pay.groupby("order_id").agg(payment_type=("payment_type", "first"),
                                      payment_installments=("payment_installments", "max"),
                                      payment_value=("payment_value", "sum"))
    # One review per order: keep the most recently answered one
    rev = (reviews.sort_values("review_answer_timestamp")
                  .drop_duplicates("order_id", keep="last")
                  .set_index("order_id")[["review_score"]])

    o = (orders.merge(customers[["customer_id", "customer_unique_id", "customer_city", "customer_state"]],
                      on="customer_id", how="left")
               .set_index("order_id").join([agg, main, pay, rev]).reset_index())

    o["customer_state_name"] = o.customer_state.map(STATE_NAMES)
    o["customer_region"] = o.customer_state.map(STATE_REGION)
    o["country"] = "Brazil"
    o["purchase_date"] = o.order_purchase_timestamp.dt.date
    o["purchase_month"] = o.order_purchase_timestamp.dt.to_period("M").dt.to_timestamp().dt.date
    o["same_state_as_seller"] = (o.customer_state == o.main_seller_state).astype(int)
    o["freight_pct_of_value"] = (100 * o.freight_value / o.product_value).round(1)

    day = pd.Timedelta(days=1)
    o["approval_hours"] = ((o.order_approved_at - o.order_purchase_timestamp) / pd.Timedelta(hours=1)).round(1)
    o["seller_handling_days"] = ((o.order_delivered_carrier_date - o.order_approved_at) / day).round(2)
    o["carrier_transit_days"] = ((o.order_delivered_customer_date - o.order_delivered_carrier_date) / day).round(2)
    o["total_delivery_days"] = ((o.order_delivered_customer_date - o.order_purchase_timestamp) / day).round(2)
    o["promised_days"] = ((o.order_estimated_delivery_date - o.order_purchase_timestamp) / day).round(2)
    # Positive = delivered after the promised date. Compared by calendar date because the
    # estimated date carries no time of day.
    o["days_vs_promise"] = (o.order_delivered_customer_date.dt.normalize()
                            - o.order_estimated_delivery_date.dt.normalize()) / day

    delivered = (o.order_status == "delivered") & o.order_delivered_customer_date.notna()
    o["is_delivered"] = delivered.astype(int)
    o["is_late"] = np.where(delivered, (o.days_vs_promise > 0).astype(float), np.nan)
    o["delay_bucket"] = np.select(
        [~delivered, o.days_vs_promise <= 0, o.days_vs_promise <= 3,
         o.days_vs_promise <= 7, o.days_vs_promise <= 14],
        ["Not delivered", "On time or early", "1-3 days late", "4-7 days late", "8-14 days late"],
        default="15+ days late")
    # Data errors: a handful of orders show the carrier pickup after customer delivery or
    # before approval. Blank those legs rather than let negative durations skew averages.
    for col in ["seller_handling_days", "carrier_transit_days"]:
        o.loc[o[col] < 0, col] = np.nan

    order_cols = [
        "order_id", "customer_unique_id", "order_status", "is_delivered", "purchase_date", "purchase_month",
        "customer_city", "customer_state", "customer_state_name", "customer_region", "country",
        "items_count", "sellers_count", "main_category", "main_seller_id", "main_seller_state",
        "same_state_as_seller", "product_value", "freight_value", "freight_pct_of_value",
        "payment_type", "payment_installments", "payment_value", "review_score",
        "approval_hours", "seller_handling_days", "carrier_transit_days", "total_delivery_days",
        "promised_days", "days_vs_promise", "is_late", "delay_bucket", "seller_missed_deadline",
    ]
    int_cols = ["items_count", "sellers_count", "payment_installments", "review_score",
                "days_vs_promise", "is_late", "seller_missed_deadline"]
    o[int_cols] = o[int_cols].round().astype("Int64")
    o[order_cols].to_csv(OUT / "orders.csv", index=False)

    item_cols = ["order_id", "order_item_id", "product_id", "category", "product_weight_g",
                 "seller_id", "seller_city", "seller_state", "seller_state_name", "seller_region",
                 "price", "freight_value", "shipping_limit_date", "missed_ship_deadline"]
    items["missed_ship_deadline"] = items.missed_ship_deadline.astype("Int64")
    items["product_weight_g"] = items.product_weight_g.round().astype("Int64")
    items[item_cols].to_csv(OUT / "items.csv", index=False)

    print(f"orders.csv  {len(o):,} rows  ({delivered.sum():,} delivered)")
    print(f"items.csv   {len(items):,} rows")
    print("unmapped states:", o.customer_state_name.isna().sum(), items.seller_state_name.isna().sum())
    print("orders without items:", o.items_count.isna().sum(), " without review:", o.review_score.isna().sum())


if __name__ == "__main__":
    main()
