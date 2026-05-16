WATCHLIST: dict[str, list[str]] = {
    "Tech": ["NVDA", "AAPL", "MSFT", "GOOGL", "META"],
    "Finance": ["JPM", "BAC", "GS"],
    "ETFs": ["SPY", "QQQ", "VTI"],
}

CATEGORIES: list[str] = [
    "Tech",
    "Finance",
    "ETFs",
    "AI & Semiconductors",
    "Energy",
    "Healthcare",
    "Consumer",
    "Crypto",
]

ALERT_THRESHOLD_PCT: float = 3.0
INDEX_ALERT_THRESHOLD_PCT: float = 1.5
CHECK_INTERVAL_MINUTES: int = 15
