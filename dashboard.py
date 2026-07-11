from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from analyzers.technical import full_analysis
from config import CATEGORIES, WATCHLIST
from data.fetcher import get_current_price, get_historical, get_multiple_prices
from db.database import (
    add_to_watchlist,
    delete_trade,
    get_today_alerts,
    get_trade_summary,
    get_trades,
    get_watchlist,
    remove_from_watchlist,
)

st.set_page_config(
    page_title="StockSage",
    page_icon="📊",
    layout="wide",
)

st.title("📊 StockSage")

tab1, tab2, tab3, tab4 = st.tabs(["📊 Watchlist", "🔍 ניתוח", "📓 יומן מסחר", "🔔 התראות"])

# ── Tab 1 — Watchlist ─────────────────────────────────────────────────────────

with tab1:
    st.subheader("📊 Watchlist — מחירים חיים")

    wl = get_watchlist()
    all_categories = list(wl.keys())
    all_symbols = [s for syms in wl.values() for s in syms]

    col_filter, col_btn = st.columns([3, 1])
    with col_filter:
        selected_cats = st.multiselect(
            "סינון לפי קטגוריה",
            options=all_categories,
            default=all_categories,
        )
    with col_btn:
        st.write("")
        refresh = st.button("🔄 רענן מחירים", key="refresh_wl")

    if "wl_prices" not in st.session_state or refresh:
        with st.spinner("מושך מחירים..."):
            st.session_state["wl_prices"] = get_multiple_prices(all_symbols)

    prices = st.session_state["wl_prices"]

    for category, symbols in wl.items():
        if category not in selected_cats:
            continue
        with st.expander(f"📂 {category} ({len(symbols)} מניות)", expanded=True):
            cols = st.columns(4)
            for i, symbol in enumerate(symbols):
                p = prices.get(symbol)
                with cols[i % 4]:
                    if p:
                        st.metric(
                            label=symbol,
                            value=f"${p['price']:,.2f}",
                            delta=f"{p['change_pct']:+.2f}%",
                        )
                    else:
                        st.metric(label=symbol, value="N/A", delta=None)

    st.divider()

    # ── Add / Remove forms ────────────────────────────────────────────────────
    col_add, col_remove = st.columns(2)

    with col_add:
        st.markdown("**➕ הוסף מניה**")
        with st.form("add_symbol_form"):
            new_symbol = st.text_input("סימבול", placeholder="לדוגמה: TSLA").upper().strip()
            new_category = st.selectbox("קטגוריה", options=CATEGORIES)
            add_submitted = st.form_submit_button("➕ הוסף")
        if add_submitted:
            if new_symbol:
                add_to_watchlist(new_symbol, new_category)
                st.success(f"✅ {new_symbol} נוסף לקטגוריה {new_category}")
                st.rerun()
            else:
                st.warning("נא להכניס סימבול.")

    with col_remove:
        st.markdown("**🗑 הסר מניה**")
        with st.form("remove_symbol_form"):
            remove_symbol = st.selectbox(
                "בחר מניה להסרה",
                options=all_symbols if all_symbols else ["—"],
            )
            remove_submitted = st.form_submit_button("🗑 הסר")
        if remove_submitted and remove_symbol != "—":
            remove_from_watchlist(remove_symbol)
            st.success(f"🗑 {remove_symbol} הוסר מה-Watchlist")
            st.rerun()

    st.divider()

    # ── Full list expander ────────────────────────────────────────────────────
    with st.expander("📋 רשימה מלאה של כל המניות"):
        for category, symbols in wl.items():
            st.markdown(f"**{category}** ({len(symbols)})")
            st.write(", ".join(symbols))

    st.caption(f"StockSage — עדכון אחרון: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ── Tab 2 — Analysis ──────────────────────────────────────────────────────────

with tab2:
    st.subheader("🔍 ניתוח טכני")

    symbol_input = st.text_input("הכנס מניה לניתוח", placeholder="לדוגמה: NVDA").upper().strip()
    analyze_btn = st.button("🔍 נתח", key="analyze_btn")

    if analyze_btn and symbol_input:
        with st.spinner(f"מנתח את {symbol_input}..."):
            price_data = get_current_price(symbol_input)
            df = get_historical(symbol_input, period="1y")

        if not price_data or df is None:
            st.error(f"❌ לא נמצאו נתונים עבור {symbol_input}")
        else:
            analysis = full_analysis(symbol_input, df, price_data["price"])
            col1, col2, col3 = st.columns([1, 1, 2])

            with col1:
                st.markdown("**אינדיקטורים**")
                st.metric("EMA150", f"${analysis['ema150']:,.2f}", delta=f"{analysis['pct_from_ema']:+.2f}%")
                st.metric("RSI", f"{analysis['rsi']}", delta=analysis["signal"])
                st.metric("MACD Crossover", analysis["crossover"])
                st.metric("Bollinger", analysis["position"])

            with col2:
                st.markdown("**ציון והמלצה**")
                st.progress(analysis["score"] / 100, text=f"ציון קנייה: {analysis['score']}/100")

                verdict = analysis["verdict"]
                rec_color = {
                    "STRONG BUY": "🟢",
                    "BUY":        "🟡",
                    "WEAK BUY":   "🟠",
                    "WATCH":      "⚪",
                    "AVOID":      "🔴",
                }.get(verdict, "⚪")
                st.markdown(f"### {rec_color} {verdict}")

                triggered = analysis.get("triggered_signals", [])
                if triggered:
                    st.caption("איתותים: " + " | ".join(triggered))

                st.divider()
                st.metric("ATR", f"${analysis['atr']:,.2f}", delta=f"{analysis['atr_pct']}% | {analysis['volatility']}")
                st.metric("🛑 Stop Loss (1.5×)", f"${analysis['stop_loss']:,.2f}")
                st.metric("🎯 Take Profit (3×)", f"${analysis['take_profit']:,.2f}")

            with col3:
                chart_type = st.radio(
                    "סוג גרף",
                    ["נרות יפניים", "קו", "OHLC"],
                    horizontal=True,
                )
                st.markdown("**גרף — 90 ימים**")
                chart_df = df.tail(90).copy()

                ema_series = df["close"].ewm(span=150, adjust=False).mean().tail(90)
                bb_mid = df["close"].rolling(20).mean().tail(90)
                bb_std = df["close"].rolling(20).std().tail(90)
                bb_upper = bb_mid + 2 * bb_std
                bb_lower = bb_mid - 2 * bb_std

                fig = go.Figure()

                if chart_type == "נרות יפניים":
                    fig.add_trace(go.Candlestick(
                        x=chart_df.index,
                        open=chart_df["open"],
                        high=chart_df["high"],
                        low=chart_df["low"],
                        close=chart_df["close"],
                        name="OHLC",
                        increasing_line_color="#26a69a",
                        decreasing_line_color="#ef5350",
                    ))
                elif chart_type == "קו":
                    fig.add_trace(go.Scatter(
                        x=chart_df.index,
                        y=chart_df["close"],
                        name="Close",
                        line=dict(color="#26a69a", width=2),
                    ))
                else:  # OHLC
                    fig.add_trace(go.Ohlc(
                        x=chart_df.index,
                        open=chart_df["open"],
                        high=chart_df["high"],
                        low=chart_df["low"],
                        close=chart_df["close"],
                        name="OHLC",
                        increasing_line_color="#26a69a",
                        decreasing_line_color="#ef5350",
                    ))

                fig.add_trace(go.Scatter(
                    x=chart_df.index, y=ema_series,
                    name="EMA150", line=dict(color="#ff9800", width=1.5),
                ))
                fig.add_trace(go.Scatter(
                    x=chart_df.index, y=bb_upper,
                    name="BB Upper", line=dict(color="#7e57c2", width=1, dash="dot"),
                ))
                fig.add_trace(go.Scatter(
                    x=chart_df.index, y=bb_lower,
                    name="BB Lower", line=dict(color="#7e57c2", width=1, dash="dot"),
                    fill="tonexty", fillcolor="rgba(126,87,194,0.07)",
                ))

                fig.update_layout(
                    xaxis_rangeslider_visible=False,
                    height=420,
                    margin=dict(l=0, r=0, t=10, b=0),
                    legend=dict(orientation="h", y=-0.15),
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    font_color="#fafafa",
                )
                st.plotly_chart(fig, width="stretch")

    st.caption(f"StockSage — עדכון אחרון: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ── Tab 3 — Trade Journal ─────────────────────────────────────────────────────

with tab3:
    st.subheader("📓 יומן מסחר")

    trades = get_trades()

    if not trades:
        st.info("אין עסקאות רשומות עדיין.")
    else:
        import pandas as pd

        df_trades = pd.DataFrame(trades)
        df_trades["total"] = df_trades["quantity"] * df_trades["price"]
        df_trades["traded_at"] = pd.to_datetime(df_trades["traded_at"]).dt.strftime("%Y-%m-%d %H:%M")

        display = df_trades[["id", "traded_at", "action", "symbol", "quantity", "price", "total", "note"]].copy()
        display.columns = ["ID", "תאריך", "פעולה", "מניה", "כמות", "מחיר", "סה״כ", "הערה"]

        def _row_color(row):
            color = "background-color: #1b3a2d" if row["פעולה"] == "BUY" else "background-color: #3a1b1b"
            return [color] * len(row)

        styled = display.style.apply(_row_color, axis=1).format({
            "מחיר": "${:,.2f}",
            "סה״כ": "${:,.2f}",
            "כמות": "{:g}",
        })
        st.dataframe(styled, width="stretch", hide_index=True)

    st.divider()

    # ── Delete trade ──────────────────────────────────────────────────────────
    st.markdown("**🗑 מחק עסקה**")
    with st.form("delete_trade_form"):
        trade_id = st.number_input("מספר עסקה (ID)", min_value=1, step=1)
        delete_submitted = st.form_submit_button("🗑 מחק עסקה")
    if delete_submitted:
        delete_trade(int(trade_id))
        st.success(f"✅ עסקה {int(trade_id)} נמחקה")
        st.rerun()

    st.divider()

    # ── Trade summary ─────────────────────────────────────────────────────────
    st.markdown("**סיכום לפי מניה**")
    summary_symbol = st.text_input("הכנס מניה לסיכום", placeholder="לדוגמה: NVDA", key="summary_sym").upper().strip()

    if summary_symbol:
        s = get_trade_summary(summary_symbol)
        if s["total_quantity"] == 0 and s["avg_buy_price"] == 0:
            st.warning(f"אין עסקאות עבור {summary_symbol}")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("מחיר קנייה ממוצע", f"${s['avg_buy_price']:,.4f}")
            c2.metric("כמות נוכחית", f"{s['total_quantity']:g}")
            pnl = s["realized_pnl"]
            c3.metric("רווח/הפסד ממומש", f"${pnl:,.2f}", delta=f"{'▲' if pnl >= 0 else '▼'} {abs(pnl):,.2f}")

    st.caption(f"StockSage — עדכון אחרון: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ── Tab 4 — Alerts ────────────────────────────────────────────────────────────

with tab4:
    st.subheader("🔔 התראות היום")

    alerts = get_today_alerts()

    if not alerts:
        st.info("אין התראות להיום.")
    else:
        for alert in alerts:
            time_str = str(alert["triggered_at"])[11:16]
            msg = f"**[{time_str}] {alert['symbol']}** — {alert['message']}"
            atype = alert["alert_type"]

            # The agent emits only BUY_SIGNAL (agent/core.py:check_alerts);
            # the generic branch renders any legacy alert types still in the DB.
            if atype == "BUY_SIGNAL":
                st.success(msg, icon="🟢")
            else:
                st.info(msg)

    st.caption(f"StockSage — עדכון אחרון: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
