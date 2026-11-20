# Amazon Ad Revenue

How much of Amazon's shopping business comes from advertising, 2015–2025.

Amazon reports advertising revenue only from 2019. It has never reported advertising profit, so there's no profit figure for any year. Everything below comes from Amazon's 10-K filings on SEC EDGAR (2015–2025). The percentages are computed from those figures.

| Year | Ad revenue ($B) | Retail revenue ($B) | Ads as % of retail revenue | Retail operating profit ($B) |
|---|---|---|---|---|
| 2015 | ≤1.7\* | 99.1 | ≤1.7% | 0.7 |
| 2016 | ≤3.0\* | 123.8 | ≤2.4% | 1.1 |
| 2017 | ≤4.7\* | 160.4 | ≤2.9% | −0.2 |
| 2018 | ≤10.1\* | 207.2 | ≤4.9% | 5.1 |
| 2019 | 12.6 | 245.5 | 5.1% | 5.3 |
| 2020 | 19.8 | 340.7 | 5.8% | 9.4 |
| 2021 | 31.2 | 407.6 | 7.6% | 6.3 |
| 2022 | 37.7 | 433.9 | 8.7% | −10.6 |
| 2023 | 46.9 | 484.0 | 9.7% | 12.2 |
| 2024 | 56.2 | 530.4 | 10.6% | 28.8 |
| 2025 | 68.6 | 588.2 | 11.7% | 34.4 |

\* Upper bound; see "2015–2018 ads" below.

## Notes on the figures

- **"Retail":** Amazon's North America and International segments combined, the closest thing Amazon reports to its shopping business. It includes ads and subscriptions but excludes AWS (Amazon's cloud business). Retail profit is the operating income of those two segments.
- **2015–2018 ads:** Amazon only reported an "Other" line, which was mostly advertising but also included things like co-branded credit cards. The figures are upper bounds, taken from the 2017 and 2018 10-Ks. The 2019 "Other" figure was $14.1B before Amazon separated out advertising, which then restated 2019 ads to $12.6B.
- **Retail profit restatements:** Amazon restated the 2015 segment results in its 2016 10-K; the restated numbers are used.
- **Ads aren't only shopping:** the advertising line also covers Prime Video ads, Twitch and ads shown off Amazon, so it overstates ads on the shopping site itself.

## Ads and profit

There's no "ads as % of shopping profit" for any year, because Amazon doesn't report ad profit. Retail profit was also negative in 2017 and 2022, so a percentage wouldn't mean much in those years anyway.

- Ad revenue has been larger than all retail operating profit every year since 2019, except 2020.
- The one analyst figure found put ad margins above 50%. It came from an older article, so treat it as unreliable. If it were true, ads would account for roughly all of 2025's $34.4B retail operating profit. That's a what-if, not a reported figure.

## Not included

Segment-level shopping sales (online stores, third-party seller services and physical stores) are left out. They could be added as a separate denominator.

## Sources

Amazon 10-K filings, 2015–2025, from SEC EDGAR. The downloaded filings and the extracted XBRL facts were saved on Mauricio's Mac (`/tmp/amz10k` and `/tmp/amzn_facts.json`), not in this repo.

## Why this matters for Pattie

Context.md's Rule 5 assumes advertising is a large share of Amazon's profit. These figures support that: ads grew from about 5% to almost 12% of retail revenue between 2019 and 2025, and every year except 2020 ad revenue exceeded all retail operating profit.
