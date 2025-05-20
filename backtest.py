import os
import streamlit as st
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from PIL import Image
import pandas as pd
import datetime
from datetime import timedelta

def fetch_with_candle_padding(kite, instrument_token, interval, sma_window, from_date, to_date):
    """
    Dynamically fetch enough historical candles so that SMA is valid from the first candle on 'from_date'.
    """


    trading_start = datetime.time(9, 15)
    trading_end = datetime.time(15, 30)

    # 1. Fetch candles before from_date to build SMA base
    pre_buffer_candles = pd.DataFrame()
    days_back = 1
    max_lookback_days = 90  # safe max

    while len(pre_buffer_candles) < sma_window:
        lookback_start = from_date - timedelta(days=days_back)
        try:
            temp = kite.historical_data(
                instrument_token=instrument_token,
                from_date=datetime.datetime.combine(lookback_start, trading_start),
                to_date=datetime.datetime.combine(from_date, trading_start),
                interval=interval
            )
            pre_buffer_candles = pd.concat([pd.DataFrame(temp), pre_buffer_candles])
            pre_buffer_candles.drop_duplicates(inplace=True)
        except:
            pass

        days_back += 1
        if days_back > max_lookback_days:
            break

    if len(pre_buffer_candles) < sma_window:
        return pd.DataFrame()  # not enough history, fail gracefully

    # 2. Fetch actual user-selected data range
    try:
        main_data = kite.historical_data(
            instrument_token=instrument_token,
            from_date=datetime.datetime.combine(from_date, trading_start),
            to_date=datetime.datetime.combine(to_date, trading_end),
            interval=interval
        )
        main_df = pd.DataFrame(main_data)
    except:
        return pd.DataFrame()

    # 3. Combine both
    df = pd.concat([pre_buffer_candles, main_df]).drop_duplicates().sort_values(by="date")
    df['timestamp'] = pd.to_datetime(df['date'])
    df.set_index('timestamp', inplace=True)
    return df


def run_backtest(api_key, access_token, instrument_token,
                 start_date, end_date, interval,
                 sma_window, target, stoploss):
    from datetime import time
    import pandas as pd
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    df = fetch_with_candle_padding(
        kite=kite,
        instrument_token=instrument_token,
        interval=interval,
        sma_window=sma_window,
        from_date=start_date,
        to_date=end_date
    )

    if df.empty:
        print("No data returned. Check instrument token or date range.")
        return None, None, None

    df['SMA'] = df['close'].rolling(window=sma_window).mean()
    trade_dates = pd.date_range(start=start_date, end=end_date).date

    positions = []
    alerts = []

    position = None
    pending_bzc = None
    pending_szc = None
    pending_alert = None
    last_exit_type = None
    last_exit_date = None

    reversal_signal_type = None
    reversal_signal_time = None
    reversal_pending = False

    for i in range(sma_window, len(df) - 1):
        prev_candle = df.iloc[i - 1]
        current_candle = df.iloc[i]
        candle_time = current_candle.name.time()
        candle_date = current_candle.name.date()

        if candle_date not in trade_dates:
            continue
        if candle_time < time(9, 16) or candle_time > time(15, 15):
            continue
        if candle_time > time(14, 30) and position is None:
            continue

        # === EXIT LOGIC ===
        if position:
            exit_price, pnl, reason = None, None, None

            if position['type'] == 'BUY' and prev_candle[['open', 'high', 'low', 'close']].max() < prev_candle['SMA']:
                exit_price = prev_candle['close']
                pnl = exit_price - position['entry_price']
                reason = 'SZC formed - Early Exit BUY'
                exit_time = prev_candle.name

                pending_alert = {
                    'type': 'SELL',
                    'trigger_price': current_candle['low'],
                    'signal_time': current_candle.name
                }

                alerts.append({
                    'Alert Type': 'SELL',
                    'Trigger Price': current_candle['low'],
                    'Alert Time': current_candle.name,
                    'Reason': 'Reversal after BUY exit'
                })

                reversal_signal_type = 'SELL'
                reversal_signal_time = current_candle.name
                reversal_pending = True

            elif position['type'] == 'SELL' and prev_candle[['open', 'high', 'low', 'close']].min() > prev_candle['SMA']:
                exit_price = prev_candle['close']
                pnl = position['entry_price'] - exit_price
                reason = 'BZC formed - Early Exit SELL'
                exit_time = prev_candle.name

                pending_alert = {
                    'type': 'BUY',
                    'trigger_price': current_candle['high'],
                    'signal_time': current_candle.name
                }

                alerts.append({
                    'Alert Type': 'BUY',
                    'Trigger Price': current_candle['high'],
                    'Alert Time': current_candle.name,
                    'Reason': 'Reversal after SELL exit'
                })

                reversal_signal_type = 'BUY'
                reversal_signal_time = current_candle.name
                reversal_pending = True

            elif position['type'] == 'BUY':
                if current_candle['high'] >= position['entry_price'] + target:
                    exit_price = position['entry_price'] + target
                    pnl = target
                    reason = 'Target Hit'
                    exit_time = current_candle.name
                elif current_candle['low'] <= position['entry_price'] - stoploss:
                    exit_price = position['entry_price'] - stoploss
                    pnl = -stoploss
                    reason = 'Stoploss Hit'
                    exit_time = current_candle.name
                elif candle_time == time(15, 0):
                    exit_price = current_candle['close']
                    pnl = exit_price - position['entry_price']
                    reason = 'Market Close'
                    exit_time = current_candle.name

            elif position['type'] == 'SELL':
                if current_candle['low'] <= position['entry_price'] - target:
                    exit_price = position['entry_price'] - target
                    pnl = target
                    reason = 'Target Hit'
                    exit_time = current_candle.name
                elif current_candle['high'] >= position['entry_price'] + stoploss:
                    exit_price = position['entry_price'] + stoploss
                    pnl = -stoploss
                    reason = 'Stoploss Hit'
                    exit_time = current_candle.name
                elif candle_time == time(15, 0):
                    exit_price = current_candle['close']
                    pnl = position['entry_price'] - exit_price
                    reason = 'Market Close'
                    exit_time = current_candle.name

            if exit_price is not None:
                positions.append({
                    'Position Type': position['type'],
                    'Entry Time': position['entry_time'],
                    'Entry Price': position['entry_price'],
                    'Exit Time': exit_time,
                    'Exit Price': exit_price,
                    'PnL': pnl,
                    'Exit Reason': reason})
                last_exit_type = position['type']
                last_exit_date = candle_date
                position = None

        # === INDEPENDENT REVERSAL TRACKING (after exit) ===
        if position is None and not pending_alert:
            if last_exit_type == 'BUY' and prev_candle[['open', 'high', 'low', 'close']].max() < prev_candle['SMA']:
                # SZC formed
                pending_alert = {
                    'type': 'SELL',
                    'trigger_price': current_candle['low'],
                    'signal_time': current_candle.name
                }
                reversal_signal_type = 'SELL'
                reversal_signal_time = current_candle.name
                reversal_pending = True

            elif last_exit_type == 'SELL' and prev_candle[['open', 'high', 'low', 'close']].min() > prev_candle['SMA']:
                # BZC formed
                pending_alert = {
                    'type': 'BUY',
                    'trigger_price': current_candle['high'],
                    'signal_time': current_candle.name
                }
                reversal_signal_type = 'BUY'
                reversal_signal_time = current_candle.name
                reversal_pending = True

        # === UNBLOCK SAME-SIDE ENTRY IF REVERSAL FAILED ===
        if last_exit_type and reversal_pending and pending_alert:
            bars_since_reversal = (current_candle.name - reversal_signal_time).seconds // 60
            if bars_since_reversal >= 3:  # give it 3 minutes to trigger
                last_exit_type = None
                last_exit_date = None
                pending_alert = None
                reversal_signal_type = None
                reversal_signal_time = None
                reversal_pending = False

        # === ENTRY LOGIC ===
        if position is None:
            skip_buy = last_exit_type == 'BUY' and last_exit_date == candle_date
            skip_sell = last_exit_type == 'SELL' and last_exit_date == candle_date

            if not skip_buy and pending_bzc is None and \
               prev_candle[['open', 'high', 'low', 'close']].min() > prev_candle['SMA']:
                pending_bzc = prev_candle
                pending_szc = None

            elif not skip_sell and pending_szc is None and \
                 prev_candle[['open', 'high', 'low', 'close']].max() < prev_candle['SMA']:
                pending_szc = prev_candle
                pending_bzc = None

            if pending_bzc is not None and current_candle['high'] > pending_bzc['high']:
                position = {
                    'type': 'BUY',
                    'entry_price': pending_bzc['high'],
                    'entry_time': current_candle.name
                }
                pending_bzc = None
                pending_alert = None
                reversal_signal_type = None
                reversal_pending = False

            elif pending_szc is not None and current_candle['low'] < pending_szc['low']:
                position = {
                    'type': 'SELL',
                    'entry_price': pending_szc['low'],
                    'entry_time': current_candle.name
                }
                pending_szc = None
                pending_alert = None
                reversal_signal_type = None
                reversal_pending = False

            elif pending_alert:
                if pending_alert['type'] == "BUY" and current_candle['high'] > pending_alert['trigger_price']:
                    position = {
                        'type': 'BUY',
                        'entry_price': pending_alert['trigger_price'],
                        'entry_time': current_candle.name
                    }
                    pending_alert = None
                    reversal_signal_type = None
                    reversal_pending = False

                elif pending_alert['type'] == "SELL" and current_candle['low'] < pending_alert['trigger_price']:
                    position = {
                        'type': 'SELL',
                        'entry_price': pending_alert['trigger_price'],
                        'entry_time': current_candle.name
                    }
                    pending_alert = None
                    reversal_signal_type = None
                    reversal_pending = False

    positions_df = pd.DataFrame(positions)
    alerts_df = pd.DataFrame(alerts)
    total_pnl = positions_df['PnL'].sum() if not positions_df.empty else 0.0

    return positions_df, alerts_df, total_pnl






def main():
    api_key = "d97r33dl25jdqeiq"
    access_token = "zJaTSBkYGo1I7LNUsFQApUcPTVMt9Nkd"

    # Page Config for Title & Icon
    st.set_page_config(
        page_title="Tradeon Backtester Pro",
        page_icon="🚀",
        layout="wide"
    )

    # Custom Styling for a Professional UI
    st.markdown("""
        <style>
            /* Full Page Styling */
            .block-container {
                max-width: 85%;
                padding-top: 1.8rem !important;
                padding-bottom: 2rem !important;
            }

            /* Premium Card Layout */
            .card {
                background: #FFFFFF;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0px 4px 10px rgba(0, 0, 0, 0.1);
                margin-bottom: 20px;
            }

            /* Title Styling */
            .title {
                font-size: 3rem;
                font-weight: bold;
                color: #00264D;
                text-align: center;
                padding-bottom: 10px;
                text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.2);
            }

            /* Subtitle */
            .subtitle {
                font-size: 1.3rem;
                text-align: center;
                color: #005A9C;
                margin-bottom: 25px;
            }

            /* Tip Box (More Premium) */
            .stAlert {
                background-color: #F0F7FF !important;
                border-left: 6px solid #007ACC;
                padding: 12px;
                border-radius: 8px;
                box-shadow: 0px 3px 8px rgba(0, 0, 0, 0.1);
            }

            /* Professional Button */
            /* Keep "Run Backtest Now!" always visible */
            .stButton > button {
                position: fixed;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                width: 280px;
                padding: 14px 24px !important;
                font-size: 1.2rem !important;
                background: linear-gradient(135deg, #007ACC 0%, #00BFFF 100%) !important;
                color: white !important;
                font-weight: bold !important;
                border-radius: 8px !important;
                border: none !important;
                box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.2);
                transition: 0.3s ease-in-out;
            }
            .stButton > button:hover {
                background: linear-gradient(135deg, #005A9C 0%, #009ACD 100%) !important;
                box-shadow: 2px 2px 15px rgba(0, 0, 0, 0.3);
            }

            /* Improve table readability */
            tbody tr:nth-child(odd) {
                background-color: #F5F5F5 !important;
            }
            tbody tr:hover {
                background-color: #E3F2FD !important;
            }

            /* Profit/Loss Styling */
            .profit { color: #008000; font-weight: bold; }
            .loss { color: #FF0000; font-weight: bold; }

            /* Section Headers */
            .stMarkdown h2 {
                font-size: 1.5rem;
                font-weight: bold;
                color: #005A9C;
                padding-bottom: 5px;
                margin-bottom: 15px;
            }

            /* Instrument Token Styling */
            .inst-token {
                background: #E6F7EB;
                border-radius: 8px;
                padding: 10px;
                font-weight: bold;
                color: #005A9C;
                text-align: center;
                box-shadow: 0px 3px 6px rgba(0, 0, 0, 0.1);
            }

        </style>
    """, unsafe_allow_html=True)

    # Title Section
    st.markdown('<div class="title">🚀 Tradeon Backtester Pro</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">SMA-based Intraday Backtesting Tool</div>', unsafe_allow_html=True)
    st.info("💡 Tip: Select your instrument, strategy parameters, and hit **Run Backtest** to simulate trades!")

    if not api_key or not access_token:
        st.error("❌ Missing API_KEY or ACCESS_TOKEN in environment.")
        return

    # Instrument Selection (Always Visible Now)
    with st.container():
        st.markdown("## 🎯 Select Instrument")
        instruments = {
            "NIFTY25MAYFUT": 14626050,
            "NIFTY25JUNFUT": 14536962,
            "NIFTY25JULFUT": 13623298,
            "BANKNIFTY25MAYFUT": 14625282,
            "BANKNIFTY25JUNFUT": 14536194,
            "BANKNIFTY25JULFUT": 13622530
        }
        selected_instrument = st.selectbox("📊 Choose instrument:", list(instruments.keys()))
        instrument_token = instruments[selected_instrument]
        st.success(f"✅ Instrument Token: `{instrument_token}`")


    # Date Selection
    with st.container():
        st.markdown("## 📅 Backtest Date Range")
        col1, col2 = st.columns([1, 1])
        with col1:
            start_date = st.date_input("📅 Start Date")
        with col2:
            end_date = st.date_input("📅 End Date")

    # Strategy Parameters (No Default Values Now)
    with st.container():
        st.markdown("## ⚙️ Strategy Parameters")
        col3, col4, col5 = st.columns([1, 1, 1])
        with col3:
            sma_window = st.number_input("📏 SMA Window", min_value=2, max_value=200, value=5)
        with col4:
            interval = st.selectbox("🕒 Candle Interval", [
                "1minute", "3minute", "5minute", "10minute", "15minute", "30minute", "day"
            ],index=2)
        with col5:
            target = st.number_input("🎯 Target", min_value=1,value=75)
            stoploss = st.number_input("🛑 Stoploss", min_value=1, value=75)

    # Run Backtest Button
    # st.markdown("## ✅ Run Backtest")
    if st.button("🚀 Run Backtest Now!"):
        with st.spinner("⏳ Running backtest... crunching candles..."):
            positions_df, alerts_df, total_pnl = run_backtest(
                api_key=api_key,
                access_token=access_token,
                instrument_token=instrument_token,
                start_date=start_date,
                end_date=end_date,
                interval=interval,
                sma_window=sma_window,
                target=target,
                stoploss=stoploss
            )

        if positions_df is None:
            st.error("❌ No data returned. Please check your date range or instrument.")
        elif positions_df.empty:
            st.warning("⚠️ No trades triggered in the selected range.")
        else:
            st.success("✅ Backtest complete!")

            # Move button up when results appear
            st.markdown(
                """
                <style>
                    .stButton > button {
                        position: relative !important;
                        bottom: auto !important;
                        margin-top: 20px !important;
                    }
                </style>
                """,
                unsafe_allow_html=True
            )

            # Display results
            st.markdown("### 🏆 Trade Results")
            st.write(positions_df.to_html(escape=False), unsafe_allow_html=True)
            # Create CSV for download
            csv = positions_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Trade Results as CSV",
                data=csv,
                file_name="backtest_results.csv",
                mime="text/csv"
            )


            # Show Final PnL
            pnl_color = "green" if total_pnl > 0 else "red"
            st.markdown(
                f"### 💰 <span style='color:{pnl_color}; font-weight:bold;'>Total PnL: {total_pnl:.2f} points</span>",
                unsafe_allow_html=True
            )

            st.markdown("---")
            st.markdown("🧠 *Powered by SMA logic + Tradeon backtesting engine*")
            st.markdown("Developed by Tradeon❤️")
            st.markdown("---")


if __name__ == "__main__":
    main()
