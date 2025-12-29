import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import time
import plotly.express as px
from supabase import create_client, Client

# ==========================================
# 1. 雲端資料庫連線與設定
# ==========================================
st.set_page_config(page_title="原子存股 (雲端版)", page_icon="⚛️", layout="wide")

try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"❌ 雲端連線失敗: {e}")
    st.stop()

# ==========================================
# 讀取資料函式 (確保型態正確)
# ==========================================
def load_data_from_cloud():
    try:
        response = supabase.table("transactions").select("*").execute()
        data = response.data
        if not data: return pd.DataFrame(columns=["Date", "Ticker", "Type", "AssetType", "Shares", "Price", "Currency", "Note"])
        df = pd.DataFrame(data)
        
        # 強制轉換型態
        df['Shares'] = pd.to_numeric(df['Shares'], errors='coerce').fillna(0)
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce').fillna(0)
        df['Date'] = df['Date'].astype(str)
        if 'Type' in df.columns:
             df['Type'] = df['Type'].astype(str).apply(lambda x: x.split(" ")[0] if isinstance(x, str) and " " in x else x).str.strip()
        
        return df
    except Exception as e:
        st.error(f"☁️ 讀取雲端資料失敗: {e}")
        return pd.DataFrame()

# ==========================================
# 2. 核心運算邏輯 (已修正累加位置)
# ==========================================
@st.cache_data(ttl=3600)
def get_usdtwd_rate():
    try:
        return yf.Ticker("TWD=X").history(period="1d")['Close'].iloc[-1]
    except: return 32.5

def try_fetch_price(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        price = ticker.fast_info.get('last_price')
        if price is not None and price > 0: return price
        time.sleep(0.1) 
        hist = ticker.history(period="1d")
        if not hist.empty and hist['Close'].iloc[-1] > 0: return hist['Close'].iloc[-1]
        return None
    except: return None

@st.cache_data(ttl=1800)
def get_stock_info(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        current_price = stock.fast_info.get('last_price')
        if current_price is None:
             info = stock.info
             current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        if current_price is None: return None, 0

        ttm_dividend = 0
        try:
            dividends = stock.dividends
            if not dividends.empty:
                now = pd.Timestamp.now(tz=dividends.index.tz)
                one_year_ago = now - pd.DateOffset(months=12)
                ttm_dividend = dividends[dividends.index > one_year_ago].sum()
        except: pass
        
        if ttm_dividend == 0 and current_price > 0:
             info = stock.info
             ttm_dividend = info.get('dividendRate', 0)
             if ttm_dividend is None or ttm_dividend == 0:
                 div_yield = info.get('dividendYield', 0)
                 if div_yield is not None and div_yield > 0:
                     ttm_dividend = current_price * div_yield
        return current_price, ttm_dividend if ttm_dividend is not None else 0
    except: return None, 0

def calculate_portfolio(df, usdtwd_rate):
    total_market_value_twd = 0
    total_cost_twd = 0
    total_annual_dividend_twd = 0
    total_received_dividend_twd = 0 
    results = []
    
    if not df.empty:
        if 'AssetType' not in df.columns: df['AssetType'] = 'Stock'

        dividend_transactions = df[df['Type'] == "DIVIDEND"]
        for _, row in dividend_transactions.iterrows():
            price_val = row['Price'] if pd.notnull(row['Price']) else 0
            fx_rate = usdtwd_rate if row['Currency'] == 'USD' else 1.0
            total_received_dividend_twd += price_val * fx_rate
            
        grouped = df.groupby('Ticker')
        for ticker, group_df in grouped:
            buys = group_df[group_df['Type'] == 'BUY']
            sells = group_df[group_df['Type'] == 'SELL']
            total_shares = buys['Shares'].sum() - sells['Shares'].sum()
            
            if total_shares <= 0: continue

            avg_cost = (buys['Price'] * buys['Shares']).sum() / buys['Shares'].sum() if not buys.empty else 0
            currency = group_df['Currency'].iloc[0]
            asset_type = group_df['AssetType'].iloc[0]

            current_price, ttm_dividend = get_stock_info(ticker)

            if current_price is None or current_price <= 0:
                 # 如果抓不到報價，用成本價暫代，避免市值為 0 (可選)
                 # current_price = avg_cost 
                 # 或者就讓它是 0，並顯示警告
                 current_price = 0

            fx_rate = usdtwd_rate if currency == 'USD' else 1.0
            market_value = current_price * total_shares * fx_rate
            cost_value = avg_cost * total_shares * fx_rate
            annual_dividend = ttm_dividend * total_shares * fx_rate
            unrealized_pl = market_value - cost_value
            
            # 🔥🔥🔥 關鍵修正：這三行必須在 for 迴圈裡面 (縮排要正確) 🔥🔥🔥
            total_market_value_twd += market_value
            total_cost_twd += cost_value
            total_annual_dividend_twd += annual_dividend
            # ---------------------------------------------------------
            
            results.append({
                "代號": ticker, "資產類別": asset_type, "股數": total_shares,
                "現價": f"{current_price:.2f} ({currency})",
                "市值(TWD)": market_value, "成本(TWD)": cost_value,
                "未實現損益": unrealized_pl,
                "報酬率%": (unrealized_pl / cost_value) if cost_value > 0 else 0,
                "成本殖利率(YoC)%": (annual_dividend / cost_value) if cost_value > 0 else 0,
                "預估年息(TWD)": annual_dividend
            })
        portfolio_df = pd.DataFrame(results)
    else:
        portfolio_df = pd.DataFrame()

    total_return_numerator = (total_market_value_twd + total_received_dividend_twd) - total_cost_twd
    metrics = {
        "total_market_value": total_market_value_twd,
        "total_cost": total_cost_twd,
        "total_annual_dividend": total_annual_dividend_twd,
        "monthly_passive_income": total_annual_dividend_twd / 12,
        "total_return_pct": (total_return_numerator / total_cost_twd * 100) if total_cost_twd > 0 else 0,
        "avg_yoc_pct": (total_annual_dividend_twd / total_cost_twd * 100) if total_cost_twd > 0 else 0,
        "total_received_dividend": total_received_dividend_twd
    }
    return metrics, portfolio_df

# ==========================================
# 3. 介面呈現 (Streamlit UI)
# ==========================================
with st.spinner("正在從全球市場獲取最新報價與配息資料..."):
    df_transactions = load_data_from_cloud()
    usdtwd_rate = get_usdtwd_rate()
    metrics, df_portfolio = calculate_portfolio(df_transactions, usdtwd_rate)

# --- 側邊欄 (保持不變) ---
with st.sidebar:
    st.header("➕ 注入原子能量 (雲端直連)")
    with st.form("add_transaction_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        date_input = col1.date_input("日期", datetime.today())
        ticker_input = col2.text_input("代號", value="").upper().strip()
        col3, col4 = st.columns(2)
        trans_type_input = col3.selectbox("交易類別", ["BUY (買入)", "SELL (賣出)", "DIVIDEND (領息)"])
        trans_type_clean = trans_type_input.split(" ")[0]
        is_dividend = trans_type_clean == "DIVIDEND"
        currency = col4.selectbox("幣別", ["TWD", "USD"])
        asset_type_input = st.selectbox("資產類別", ["Stock (股票/ETF)", "Bond (債券/類現金)"])
        asset_type_save = "Stock" if "Stock" in asset_type_input else "Bond"
        shares_label = "股數 (股)" if not is_dividend else "股數 (領息請維持 0)"
        price_label = "成交單價 (原幣)" if not is_dividend else "領息總金額 (原幣)"
        col5, col6 = st.columns(2)
        shares_input = col5.number_input(shares_label, min_value=0.00, step=1.0, format="%.2f")
        price_input = col6.number_input(price_label, min_value=0.00, step=0.1, format="%.2f")
        note_input = st.text_input("備註 (選填)")
        if is_dividend: st.info("💡 領息模式：請在右側「領息總金額」填寫實際收到的金額。")
        submitted = st.form_submit_button("🚀 確認注入雲端 (Inject to Cloud)")
        if submitted:
            if not ticker_input or price_input < 0: st.error("請填寫正確的代號和金額")
            elif not is_dividend and shares_input <= 0: st.error("買賣交易請填寫正確的股數 (>0)")
            elif is_dividend and price_input <= 0: st.error("領息交易請填寫正確的總金額 (>0)")
            else:
                final_ticker = ticker_input
                if not is_dividend and currency == "TWD" and "." not in ticker_input:
                    with st.spinner(f"偵測中: {ticker_input}..."):
                        if try_fetch_price(ticker_input + ".TW") is not None: final_ticker = ticker_input + ".TW"
                        elif try_fetch_price(ticker_input + ".TWO") is not None: final_ticker = ticker_input + ".TWO"
                try:
                    new_transaction = {"Date": str(date_input),"Ticker": final_ticker,"Type": trans_type_clean,"AssetType": asset_type_save,"Shares": shares_input,"Price": price_input,"Currency": currency,"Note": note_input}
                    supabase.table("transactions").insert(new_transaction).execute()
                    st.success(f"✅ 已成功注入雲端資料庫！")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e: st.error(f"❌ 寫入雲端失敗: {e}")

# --- 主頁面 (保持不變) ---
st.title("⚛️ 原子存股 (Atomic Wealth) | ☁️ 雲端版")
st.caption(f"即時匯率參考: 1 USD ≈ {usdtwd_rate:.2f} TWD")

if df_transactions.empty:
    st.info("👈 雲端資料庫目前是空的。請在左側注入第一筆交易能量！")
else:
    # 1. 頂部核心指標
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("💰 總資產現值 (TWD)", f"${metrics['total_market_value']:,.0f}")
    col_m2.metric("📈 總含息報酬率", f"{metrics['total_return_pct']:+.2f}%", delta=f"{metrics['total_return_pct']:+.2f}%", help="包含帳面損益與歷史已領股息的總報酬")
    col_m3.metric("💎 總成本殖利率 (YoC)", f"{metrics['avg_yoc_pct']:.2f}%", help="原始投入成本的未來預估股息回報率")
    col_m4.metric("💵 已領取歷史總股息 (TWD)", f"${metrics['total_received_dividend']:,.0f}", help="歷史上實際已領到口袋的現金股息總額")
    
    st.divider()

    # 2. 被動收入層級塔
    st.header("🗼 被動收入層級塔")
    monthly_income = metrics['monthly_passive_income']
    LEVEL_1_GOAL, LEVEL_2_GOAL, LEVEL_3_GOAL = 25000, 60000, 100000
    st.markdown(f"""<div style="text-align: center;"><h1 style="font-size: 4rem; margin-bottom: 0; color: #00E5FF;">${monthly_income:,.0f}</h1><p style="font-size: 1.2rem; color: gray;">預估平均每月被動收入 (TWD)</p></div>""", unsafe_allow_html=True)

    if monthly_income < LEVEL_1_GOAL:
        st.write(f"🧱 **Level 1: 生存基石** (${LEVEL_1_GOAL:,.0f}/月)")
        st.progress(monthly_income / LEVEL_1_GOAL if LEVEL_1_GOAL > 0 else 0)
    elif monthly_income < LEVEL_2_GOAL:
        st.write(f"🏠 **Level 2: 薪資替代** (${LEVEL_2_GOAL:,.0f}/月)")
        st.progress((monthly_income - LEVEL_1_GOAL) / (LEVEL_2_GOAL - LEVEL_1_GOAL) if LEVEL_2_GOAL > LEVEL_1_GOAL else 0)
    else:
        st.write(f"🗽 **Level 3: 財富自由** (${LEVEL_3_GOAL:,.0f}/月)")
        st.progress(min(1.0, (monthly_income - LEVEL_2_GOAL) / (LEVEL_3_GOAL - LEVEL_2_GOAL)) if LEVEL_3_GOAL > LEVEL_2_GOAL else 0)
        st.balloons()

    st.divider()

    # ==========================================
    # 🔥 原子結構分析圖表
    # ==========================================
    if not df_portfolio.empty:
        st.header("📊 原子結構分析")
        col_chart1, col_chart2, col_chart3 = st.columns(3)

        with col_chart1:
            st.subheader("股債配置 (市值)")
            df_asset_alloc = df_portfolio.groupby('資產類別')['市值(TWD)'].sum().reset_index()
            if not df_asset_alloc.empty:
                fig_asset = px.pie(df_asset_alloc, values='市值(TWD)', names='資產類別', hole=0.4,
                    color='資產類別', color_discrete_map={'Stock': '#2196F3', 'Bond': '#FF9800'})
                fig_asset.update_traces(textposition='inside', textinfo='percent+label')
                fig_asset.update_layout(margin=dict(t=0, b=0, l=0, r=0), showlegend=False)
                st.plotly_chart(fig_asset, use_container_width=True)
            else: st.info("無資料")

        with col_chart2:
            st.subheader("持股佔比 (個股)")
            fig_donut = px.pie(df_portfolio, values='市值(TWD)', names='代號', hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set3)
            fig_donut.update_traces(textposition='inside', textinfo='percent')
            fig_donut.update_layout(margin=dict(t=0, b=0, l=0, r=0), showlegend=True)
            st.plotly_chart(fig_donut, use_container_width=True)

        with col_chart3:
            st.subheader("股息貢獻主力 (年預估)")
            df_bar = df_portfolio[df_portfolio['預估年息(TWD)'] > 0].sort_values(by='預估年息(TWD)', ascending=True)
            if not df_bar.empty:
                fig_bar = px.bar(df_bar, x='預估年息(TWD)', y='代號', orientation='h', text='預估年息(TWD)',
                    color='預估年息(TWD)', color_continuous_scale='Tealgrn')
                fig_bar.update_traces(texttemplate='$%{text:,.0f}', textposition='outside', cliponaxis=False)
                fig_bar.update_layout(xaxis_title="", yaxis_title="", coloraxis_showscale=False,
                    margin=dict(t=20, b=20, l=0, r=100), xaxis=dict(showticklabels=False))
                st.plotly_chart(fig_bar, use_container_width=True)
            else: st.info("尚無配息資料")
        
        st.divider()

    # ==========================================
    # 📊 資產庫明細
    # ==========================================
    st.subheader("🗃️ 資產庫明細")
    if not df_portfolio.empty:
        st.dataframe(
            df_portfolio.style.format({
                "股數": "{:,.2f}",
                "市值(TWD)": "${:,.0f}",
                "未實現損益": "${:+,.0f}",
                "報酬率%": "{:+.2%}",
                "成本殖利率(YoC)%": "{:.2%}",
                "預估年息(TWD)": "${:,.0f}",
            })
            .applymap(lambda v: 'color: #ff4b4b;' if v < 0 else 'color: #00c853;' if v > 0 else None, subset=["未實現損益", "報酬率%"]),
            use_container_width=True, hide_index=True, height=300
        )