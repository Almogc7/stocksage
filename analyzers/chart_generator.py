import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import CHART_HEIGHT, CHART_SCALE, CHART_THEME, CHART_WIDTH


def generate_chart_image(symbol: str, df: pd.DataFrame, analysis: dict) -> bytes | None:
    """
    Render a 3-panel PNG chart: candlestick + MA150/200, volume bars, RSI-14.
    Uses df already in memory from Gate 4 — no extra network call.
    Returns PNG bytes on success, None on any exception so callers never block.
    """
    try:
        # Normalise column names (handles MultiIndex from yfinance)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        chart_df = df.tail(90)

        score   = analysis.get("score", 0)
        verdict = analysis.get("verdict", "")

        # ── Indicators (computed on full df, sliced to chart window) ──────────
        ma150 = df["close"].ewm(span=150, adjust=False).mean().tail(90)
        ma200 = df["close"].ewm(span=200, adjust=False).mean().tail(90)

        delta = df["close"].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta).clip(lower=0).rolling(14).mean()
        rsi   = (100 - 100 / (1 + gain / loss)).tail(90)

        # ── Subplots ──────────────────────────────────────────────────────────
        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.60, 0.18, 0.22],
        )

        # Row 1 — Candlestick
        fig.add_trace(go.Candlestick(
            x=chart_df.index,
            open=chart_df["open"], high=chart_df["high"],
            low=chart_df["low"],  close=chart_df["close"],
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            name="OHLC", showlegend=False,
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=chart_df.index, y=ma150,
            name="MA150", line=dict(color="#ff9800", width=1.5),
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=chart_df.index, y=ma200,
            name="MA200", line=dict(color="#ef5350", width=1.5, dash="dash"),
        ), row=1, col=1)

        # Row 2 — Volume bars, coloured green/red by candle direction
        vol_colors = [
            "#26a69a" if pd.notna(c) and pd.notna(o) and float(c) >= float(o) else "#ef5350"
            for c, o in zip(chart_df["close"], chart_df["open"])
        ]
        fig.add_trace(go.Bar(
            x=chart_df.index, y=chart_df["volume"],
            marker_color=vol_colors,
            name="Volume", showlegend=False,
        ), row=2, col=1)

        # Row 3 — RSI
        fig.add_trace(go.Scatter(
            x=chart_df.index, y=rsi,
            name="RSI (14)", line=dict(color="#ab47bc", width=1.5),
            showlegend=False,
        ), row=3, col=1)

        fig.add_hline(y=70, line_dash="dash", line_color="#ef5350", line_width=1, row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#26a69a", line_width=1, row=3, col=1)

        # ── Global styling ────────────────────────────────────────────────────
        fig.update_layout(
            title=dict(
                text=f"{symbol} — 90D  |  Score: {score}/100  {verdict}",
                font=dict(color="#e0e0e0", size=15),
                x=0.5,
            ),
            plot_bgcolor=CHART_THEME,
            paper_bgcolor=CHART_THEME,
            font=dict(color="#9e9e9e"),
            xaxis_rangeslider_visible=False,
            legend=dict(
                orientation="h", y=1.04, x=0,
                bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e0e0e0", size=11),
            ),
            margin=dict(l=55, r=15, t=55, b=15),
        )
        fig.update_xaxes(
            gridcolor="#1e2530", zerolinecolor="#1e2530",
            color="#9e9e9e", showgrid=True,
        )
        fig.update_yaxes(
            gridcolor="#1e2530", zerolinecolor="#1e2530",
            color="#9e9e9e", showgrid=True,
        )

        return fig.to_image(format="png", width=CHART_WIDTH, height=CHART_HEIGHT, scale=CHART_SCALE)

    except Exception as e:
        print(f"[CHART FAIL] {symbol} — {type(e).__name__}: {e}")
        return None
