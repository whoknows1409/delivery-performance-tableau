# E-commerce Delivery Performance: Tableau Dashboard

**Interactive dashboard:** [View on Tableau Public](TABLEAU_LINK)

**Question:** A Brazilian e-commerce marketplace ships up to 7K orders a month from 3,000+ independent sellers. Where do late deliveries come from, what do they cost in customer satisfaction, and what should operations fix first?

**Answer:** Late deliveries (6.8% of orders) are the single biggest driver of bad reviews, dropping the average from 4.3 to 2.3 stars. Most of the extra delay (87%) is in carrier transit, and it concentrates in the North and Northeast, which receive 11% of orders but 20% of late deliveries. On the seller side, orders where the seller missed its carrier hand-over deadline are late 4× as often.

<!-- After publishing, add screenshots:
![Executive Overview](outputs/screenshots/dashboard_1.png)
![Where Delays Happen](outputs/screenshots/dashboard_2.png)
![Seller Scorecard](outputs/screenshots/dashboard_3.png)
-->

---

## Data

[Olist Brazilian E-commerce Public Dataset](https://github.com/olist/work-at-olist-data): real, anonymised orders from Olist, a Brazilian platform that connects small sellers to large online marketplaces. It spans 8 related tables (orders, items, customers, sellers, products, payments, reviews, category names). The analysis window is **Jan 2017 – Aug 2018 (99,092 orders)**. The few orders before 2017 and after Aug 2018 are platform-launch and cut-off noise.

## Data model (`src/build_model.py`)

The 8 raw tables are modelled into two Tableau tables, **related** (not joined) on `order_id`, so order-level metrics are never double-counted when sliced by seller or product:

- **orders.csv** (one row per order): customer location mapped to state names and the five Brazilian regions, order value, freight, a single review score, and the **delivery timeline split into legs**:
  - payment approval
  - seller handling (approval → hand-over to carrier)
  - carrier transit (carrier → customer)

  It also carries promised vs. actual delivery days, an `is_late` flag and a delay bucket.
- **items.csv** (one row per order line): product category, seller and seller state, price, freight, and whether the seller missed its shipping deadline.

**Data-quality handling:**
- Kept one review per order (the latest).
- Blanked a few legs with negative durations, which are timestamp errors.
- Compared delivery against the promise by calendar date, because the promised date has no time of day.

## Dashboards

1. **Executive Overview:** KPIs, late-rate and volume trend, review score by delay bucket.
2. **Where Delays Happen:** state-level late-rate map, delay broken down by leg, region comparison, same-state vs cross-state delivery.
3. **Seller Scorecard:** each seller plotted by volume vs late rate, the worst sellers, and the impact of missed shipping deadlines.

Filters for region, product category and month apply across every chart.

## Key findings

| Finding | Evidence |
|---|---|
| **Late delivery destroys ratings** | Avg review 4.29★ on time vs 2.27★ late; 61% of late orders get 1–2★ (vs 9% on time). Once an order is 4+ days late, the average falls to about 2★ or below. |
| **The carrier leg is the bottleneck** | Late orders take 22.9 days longer; 87% of that extra time is carrier transit (28 vs 8 days), 13% is seller handling. |
| **Geography drives delay and cost** | 71% of items ship from São Paulo sellers. Northeast late rate is 12.8% vs 6.1% Southeast; median freight is 36% of product value in the North vs 21% in the Southeast. Same-state orders arrive in 7.9 days vs 15.1 cross-state. |
| **Seller discipline matters** | When a seller misses its hand-over deadline (8.9% of orders), the order is late 21.1% of the time vs 5.4% otherwise. The worst 10% of sellers (30+ orders) handle 6.1% of orders but 14.9% of late ones. Seller late rate correlates with seller review at −0.53. |
| **Promises are padded but still miss** | Avg promise 23.6 days vs 12.5 actual; 64% of orders arrive 10+ days early, yet 6.8% are still late. The padding is uniform when it should vary by route. |
| **Peaks break the network** | Late rate jumped to 12.4% in Nov 2017 (Black Friday) and 19.0% in Mar 2018, against a typical 3–6%. |

## Recommendations

1. **Route-specific delivery promises.** Shorten promises on Southeast routes, where the current padding hides fast delivery and likely hurts conversion. Set realistic, longer promises on North and Northeast routes, where most misses happen.
2. **Seller SLA enforcement.** Alert sellers approaching their hand-over deadline and put repeat offenders on a watchlist.
   - *Scenario:* if missed-deadline orders were late only as often as the rest, about 1,350 fewer orders would have been late, cutting the late rate from 6.8% to 5.4%. This is an association, not a proven cause, so validate it with a pilot.
3. **Regional capacity for the North and Northeast.** Recruit sellers or add fulfilment capacity in the Northeast so more orders ship within the state (7.9 vs 15.1 days), and pre-book carrier capacity before Black Friday.

## Limitations

- Review scores are also driven by product quality; delivery explains only part of the variation.
- The seller-deadline scenario is correlational.
- Freight is what the customer paid, not Olist's actual shipping cost.

## Reproduce

```bash
pip install -r requirements.txt
# one-time: fetch the raw Olist tables into data/raw/ (gzipped)
git clone --depth 1 https://github.com/olist/work-at-olist-data.git /tmp/olist
mkdir -p data/raw && for f in /tmp/olist/datasets/*.csv; do gzip -c "$f" > "data/raw/$(basename "$f").gz"; done
python src/build_model.py   # raw tables -> data/tableau/orders.csv, items.csv
python src/analyze.py       # independent check of every dashboard number -> outputs/
```

The Tableau build steps are in [BUILD_GUIDE.md](BUILD_GUIDE.md).

**Tools:** Tableau Public, Python (pandas)
