# Setup replay confirmatory analysis

**Label: CONFIRMATORY**

## Header

- git commit: `74c6a9d0f6a3bf23e9646a011fb87d97b3949011`
- pre-registration file sha256 (HEAD): `512f3194dbfe4be183ff431b4b1493886fbdc4cab1a377048adf6626718ac5fe`
- results DB run_id: 1, mode: run_outcomes, variants: ["V3"]
- results DB universe_hash: 28118c4d6d292b5b9ab7e5e0bfd1602c7702efb13a8e26200d3dc39fdfb9e13d
- results DB dates: 2021-10-25 .. 2026-08-14 (fetch through 2026-10-23)
- library versions: {"anthropic": "0.117.0", "yfinance": "1.5.1", "pandas": "3.0.3", "numpy": "2.5.1", "ta": "0.11.0", "python-telegram-bot": "22.8", "python-dotenv": "1.2.2", "sqlalchemy": "2.0.51", "requests": "2.34.2", "textblob": "0.20.0", "newsapi-python": "0.2.7", "aiohttp": "3.14.1", "schedule": "1.2.2", "streamlit": "1.59.2", "plotly": "6.9.0", "kaleido": "1.3.0"}
- seed=20261029, draws=10000, run header slippage_bps=10.0 (frozen: seed=20261029, draws=10000, slippage_bps=10.0)

## Section 7 — sample sufficiency (condition 1)

- trades: 697 (need >= 100)
- distinct symbols: 250 (need >= 30)
- distinct fill months: 52 (need >= 24)
- distinct calendar years: 6 (need >= 3)
- meets minimum: True
- no-peer excluded trades (§6/§13.4): 0
- mean net R, ALL primary trades incl. excluded (§13.4, unfiltered): -0.1246

## Section 7 — bootstrap (condition 3)

- point estimate (mean paired difference): -0.1684
- 95% interval: [-0.3018, -0.0277]
- draws: 10000, seed: 20261029

## Section 7 — per-period table (condition 4)

| year | trades | mean paired diff | positive | qualifies (>=30 trades) |
|---|---|---|---|---|
| 2021 | 42 | -0.2411 | False | True |
| 2022 | 34 | -0.0748 | False | True |
| 2023 | 155 | 0.0040 | True | True |
| 2024 | 173 | -0.2818 | False | True |
| 2025 | 97 | -0.2697 | False | True |
| 2026 | 196 | -0.1552 | False | True |

## Section 7 — concentration (condition 5)

- max symbol share: 1.29% (HWC)
- max month share: 7.60% ((2026, 6))

## Section 7 — decide()

**Verdict: FAIL**
- mean net R of the setups is not greater than 0 (condition 2)
- lower bound of the interval is not greater than 0 (condition 3)
- paired difference positive in only 1 of 6 qualifying periods (condition 4)

## Section 8 — non-confirmatory (descriptive only, never fed to decide())

- total accepted setups: 1836

- not-trade counts by status (invalidated/expired/chase-rejected):
  - INVALIDATED_BEFORE_FILL: 538 (29.30% of all 1836 accepted setups; 47.23% within the rejected/excluded/no-peer bucket)
  - EXPIRED_UNFILLED: 445 (24.24% of all 1836 accepted setups; 39.07% within the rejected/excluded/no-peer bucket)
  - CHASE_RR_BELOW_3: 156 (8.50% of all 1836 accepted setups; 13.70% within the rejected/excluded/no-peer bucket)
- excluded counts by status (DATA_GAP/OPEN_AT_END):
  - DATA_GAP: 0 (0.00% of all 1836 accepted setups; 0.00% within the rejected/excluded/no-peer bucket)
  - OPEN_AT_END: 0 (0.00% of all 1836 accepted setups; 0.00% within the rejected/excluded/no-peer bucket)
- no-peer excluded count: 0 (0.00% of all 1836 accepted setups; 0.00% within the rejected/excluded/no-peer bucket)
- rejection/exclusion shares (raw, both denominators labeled): {'total_accepted': 1836, 'within_rejected_excluded_bucket': {'INVALIDATED_BEFORE_FILL': 0.4723441615452151, 'CHASE_RR_BELOW_3': 0.1369622475856014, 'EXPIRED_UNFILLED': 0.3906935908691835, 'no_peer': 0.0}, 'of_all_accepted_setups': {'INVALIDATED_BEFORE_FILL': 0.29302832244008714, 'CHASE_RR_BELOW_3': 0.08496732026143791, 'EXPIRED_UNFILLED': 0.24237472766884532, 'no_peer': 0.0}}
- win rate / R stats: {'count': 697, 'win_rate': 0.3486370157819225, 'avg_win_r': 1.4026070340236787, 'avg_loss_r': -0.9419952907251862, 'avg_mae_r': -0.9213153598994315, 'avg_mfe_r': 1.0968809791864091, 'timeout_share': 0.39598278335724535}
- by target_source: {'MEASURED_3R': {'count': 695, 'mean_r': -0.1307486546438841}, 'RESISTANCE': {'count': 2, 'mean_r': 2.0189811280093997}}
- by year: {2021: {'count': 42, 'mean_r': -0.6690750960132448}, 2026: {'count': 196, 'mean_r': -0.13219162061977963}, 2024: {'count': 173, 'mean_r': -0.27817651396251586}, 2025: {'count': 97, 'mean_r': 0.036059767551332805}, 2023: {'count': 155, 'mean_r': 0.26001747407654935}, 2022: {'count': 34, 'mean_r': -0.8381650019493163}}
- by regime: {'BULL': {'count': 697, 'mean_r': -0.12458013302938399}}

### Secondary: slippage 20bps (§8/§13.7, recomputed from stored prices, not resimulated)
- point estimate: -0.1716, 95% interval: [-0.3046, -0.0312]
- decide() at 20bps: FAIL ['mean net R of the setups is not greater than 0 (condition 2)', 'lower bound of the interval is not greater than 0 (condition 3)', 'paired difference positive in only 1 of 6 qualifying periods (condition 4)']

## Integrity

- stored-vs-recomputed mismatches (§13.5): 0
- cache-vs-manifest sha256 mismatches (§13.6): []
