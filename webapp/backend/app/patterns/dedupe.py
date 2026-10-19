"""De-duplication / cooldown: a detector can re-fire on consecutive bars
(e.g. a pullback confirming again a few bars later). Before any backtest
scoring, collapse re-fires of the same (symbol, pattern, direction) that
land within COOLDOWN_BARS trading bars of each other."""
from . import config


def dedupe_detections(detections: list[dict], bar_index: dict[str, dict[str, int]]) -> list[dict]:
    """`detections`: Detection dicts (must have symbol/pattern/direction/
    confirmed_on). `bar_index`: {symbol: {date: bar_index}} — the ascending
    bar position of each date in that symbol's own series. Returns the
    kept subset, grouped by (symbol, pattern, direction), sorted by
    confirmed_on; within a group, any detection whose confirmed_on is
    within COOLDOWN_BARS bars of the last KEPT detection in that group is
    dropped."""
    groups: dict[tuple, list[dict]] = {}
    for d in detections:
        key = (d["symbol"], d["pattern"], d["direction"])
        groups.setdefault(key, []).append(d)

    kept: list[dict] = []
    for (symbol, _pattern, _direction), group in groups.items():
        idx_lookup = bar_index[symbol]
        group_sorted = sorted(group, key=lambda d: idx_lookup[d["confirmed_on"]])
        last_kept_idx: int | None = None
        for d in group_sorted:
            idx = idx_lookup[d["confirmed_on"]]
            if last_kept_idx is None or (idx - last_kept_idx) > config.COOLDOWN_BARS:
                kept.append(d)
                last_kept_idx = idx

    kept.sort(key=lambda d: d["confirmed_on"])
    return kept
