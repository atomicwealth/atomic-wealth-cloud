import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import time
import plotly.express as px
# 匯入 Supabase 客戶端與錯誤型態
from supabase import create_client, Client
from gotrue.errors import AuthApiError

# ==========================================
# 1. 系統初始化與設定
# ==========================================
st.set_page_config(page_title="原子存股 (多人SaaS版)", page_icon="⚛️", layout="wide")

# 初始化 Session State (用於記住登入狀態)
if 'user' not in st.session_state:
    st.session_state['user'] = None

# 初始化 Supabase 連線
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    # 注意：這裡建立的客戶端會自動管理使用者的登入 Token
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"❌ 雲端連線失敗，請檢查 secrets.toml: {e}")
    st.stop()

# ==========================================
# 🔐 身份驗證模組 (Authentication)
# ==========================================
def login_form():
    """顯示登入表單"""
    st.subheader("🔐 會員登入")
    email = st.text_input("電子信箱", key="login_email")
    password = st.text_input("密碼", type="password", key="login_password")
    if st.button("登入", type="primary"):
        try:
            with st.spinner("正在驗證身分..."):
                # 向 Supabase Auth 發送登入請求
                response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                # 登入成功，將使用者資訊存入 session_state
                st.session_state['user'] = response.user
                st.success("登入成功！")
                time.sleep(0.5)
                st.rerun() # 重新整理頁面以進入主畫面
        except AuthApiError as e:
            st.error(f"登入失敗: {e.message} (請檢查帳號密碼)")
        except Exception as e:
            st.error(f"發生錯誤: {e}")

def signup_form():
    """顯示註冊表單"""
    st.subheader("📝 註冊新帳號")
    email = st.text_input("電子信箱", key="signup_email")
    password = st.text_input("設定密碼 (至少6碼)", type="password", key="signup_password")
    if st.button("註冊"):
        try:
            with st.spinner("正在建立帳號..."):
                # 向 Supabase Auth 發送註冊請求
                # 注意：Supabase 預設可能會寄發驗證信，需到信箱點擊連結才算啟用成功
                response = supabase.auth.sign_up({"email": email, "password": password})
                st.success("註冊申請已送出！請前往您的電子信箱收取驗證信以啟用帳號。")
                st.info("驗證完成後，請切換到「登入」頁籤進行登入。")
        except AuthApiError as e:
             st.error(f"註冊失敗: {e.message}")
        except Exception as e:
             st.error(f"發生錯誤: {e}")

# ==========================================
# 📦 資料處理與計算邏輯 (與先前版本相同)
# ==========================================
# 注意：這裡的函式不需要任何修改，因為 supabase 客戶端在登入後會自動夾帶 Token，
# 雲端的 RLS 會自動根據 Token 過濾出屬於該使用者的資料。
def load_data_from_cloud():
    try:
        response = supabase.table("transactions").select("*").order("Date", desc=True).execute()
        data = response.data
        if not data: return pd.DataFrame(columns=["id", "Date", "Ticker", "Type", "AssetType", "Shares", "Price", "Currency", "Note"])
        df = pd.DataFrame(data)
        df['Shares'] = pd.to_numeric(df['Shares'], errors='coerce').fillna(0)
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce').fillna(0)
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.date
        if 'Type' in df.columns: df['Type'] = df['Type'].astype(str).apply(lambda x: x.split(" ")[0] if isinstance(x, str) and " " in x else x).str.strip()
        return df
    except Exception as e:
        # 如果是登入過期導致的權限錯誤，這裡可能會捕捉到
        st.error(f"☁️ 讀取雲端資料失敗 (可能是權限問題或連線錯誤): {e}")
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_usdtwd_rate():
    try: return yf.Ticker("TWD=X").history(period="1d")['Close'].iloc[-1]
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
        if current_price is None: info = stock.info; current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
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
             if ttm_dividend is None or ttm_dividend == 0: div_yield = info.get('dividendYield', 0); if div_yield is not None and div_yield > 0: ttm_dividend = current_price * div_yield
        return current_price, ttm_dividend if ttm_dividend is not None else 0
    except: return None, 0

def calculate_portfolio(df, usdtwd_rate):
    total_market_value_twd = 0; total_cost_twd = 0; total_annual_dividend_twd = 0; total_received_dividend_twd = 0; results = []
    if not df.empty:
        if 'AssetType' not in df.columns: df['AssetType'] = 'Stock'
        dividend_transactions = df[df['Type'] == "DIVIDEND"]
        for _, row in dividend_transactions.iterrows():
            price_val = row['Price'] if pd.notnull(row['Price']) else 0
            fx_rate = usdtwd_rate if row['Currency'] == 'USD' else 1.0
            total_received_dividend_twd += price_val * fx_rate
        grouped = df.groupby('Ticker')
        for ticker, group_df in grouped:
            buys = group_df[group_df['Type'] == 'BUY']; sells = group_df[group_df['Type'] == 'SELL']
            total_shares = buys['Shares'].sum() - sells['Shares'].sum()
            if total_shares <= 0: continue
            avg_cost = (buys['Price'] * buys['Shares']).sum() / buys['Shares'].sum() if not buys.empty else 0
            currency = group_df['Currency'].iloc[0]; asset_type = group_df['AssetType'].iloc[0]
            current_price, ttm_dividend = get_stock_info(ticker)
            if current_price is None or current_price <= 0: current_price = 0
            fx_rate = usdtwd_rate if currency == 'USD' else 1.0
            market_value = current_price * total_shares * fx_rate; cost_value = avg_cost * total_shares * fx_rate; annual_dividend = ttm_dividend * total_shares * fx_rate
            unrealized_pl = market_value - cost_value
            total_market_value_twd += market_value; total_cost_twd += cost_value; total_annual_dividend_twd += annual_dividend
            results.append({"代號": ticker, "資產類別": asset_type, "股數": total_shares, "現價": f"{current_price:.2f} ({currency})", "市值(TWD)": market_value, "成本(TWD)": cost_value, "未實現損益": unrealized_pl, "報酬率%": (unrealized_pl / cost_value) if cost_value > 0 else 0, "成本殖利率(YoC)%": (annual_dividend / cost_value) if cost_value > 0 else 0, "預估年息(TWD)": annual_dividend})
        portfolio_df = pd.DataFrame(results)
    else: portfolio_df = pd.DataFrame()
    total_return_numerator = (total_market_value_twd + total_received_dividend_twd) - total_cost_twd
    metrics = {"total_market_value": total_market_value_twd, "total_cost": total_cost_twd, "total_annual_dividend": total_annual_dividend_twd, "monthly_passive_income": total_annual_dividend_twd / 12, "total_return_pct": (total_return_numerator / total_cost_twd * 100) if total_cost_twd > 0 else 0, "avg_yoc_pct": (total_annual_dividend_twd / total_cost_twd * 100) if total_cost_twd > 0 else 0, "total_received_dividend": total_received_dividend_twd}
    return metrics, portfolio_df

# ==========================================
# 🚀 主程式流程控制 (Main App Flow)
# ==========================================

# --- 階段 A: 檢查是否已登入 ---
if st.session_state['user'] is None:
    # 👉 如果沒登入，顯示登入/註冊介面
    st.title("⚛️ 原子存股 | 歡迎")
    st.write("請先登入或註冊以開始管理您的投資組合。")
    
    tab1, tab2 = st.tabs(["登入", "註冊"])
    with tab1: login_form()
    with tab2: signup_form()

    # 🛑 關鍵：沒登入就停在這裡，不執行下方的程式碼
    st.stop()

# --- 階段 B: 已登入，顯示主畫面 ---
# (程式碼執行到這裡，代表 st.session_state['user'] 一定有資料)
user_email = st.session_state['user'].email

# --- 側邊欄 (含登出按鈕) ---
with st.sidebar:
    st.write(f"👤 Hi, **{user_email}**")
    if st.button("登出", type="secondary"):
        # 執行登出動作
        supabase.auth.sign_out()
        st.session_state['user'] = None
        st.rerun()
    
    st.divider()
    st.header("➕ 注入原子能量")
    # ... (新增交易表單程式碼與之前相同，省略以節省篇幅，請直接使用下方完整版) ...
    with st.form("add_transaction_form", clear_on_submit=True):
        col1, col2 = st.columns(2); date_input = col1.date_input("日期", datetime.today()); ticker_input = col2.text_input("代號", value="").upper().strip()
        col3, col4 = st.columns(2); trans_type_input = col3.selectbox("交易類別", ["BUY (買入)", "SELL (賣出)", "DIVIDEND (領息)"]); trans_type_clean = trans_type_input.split(" ")[0]; is_dividend = trans_type_clean == "DIVIDEND"; currency = col4.selectbox("幣別", ["TWD", "USD"]); asset_type_input = st.selectbox("資產類別", ["Stock (股票/ETF)", "Bond (債券/類現金)"]); asset_type_save = "Stock" if "Stock" in asset_type_input else "Bond"
        shares_label = "股數 (股)" if not is_dividend else "股數 (領息請維持 0)"; price_label = "成交單價 (原幣)" if not is_dividend else "領息總金額 (原幣)"
        col5, col6 = st.columns(2); shares_input = col5.number_input(shares_label, min_value=0.00, step=1.0, format="%.2f"); price_input = col6.number_input(price_label, min_value=0.00, step=0.1, format="%.2f"); note_input = st.text_input("備註 (選填)")
        if is_dividend: st.info("💡 領息模式：請在右側「領息總金額」填寫實際收到的金額。")
        submitted = st.form_submit_button("🚀 確認注入 (Inject)")
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
                    # RLS 會自動將此筆資料關聯到當前登入的使用者
                    new_transaction = {"Date": str(date_input),"Ticker": final_ticker,"Type": trans_type_clean,"AssetType": asset_type_save,"Shares": shares_input,"Price": price_input,"Currency": currency,"Note": note_input}
                    supabase.table("transactions").insert(new_transaction).execute()
                    st.success(f"✅ 已成功注入！")
                    time.sleep(0.5); st.rerun()
                except Exception as e: st.error(f"❌ 寫入失敗: {e}")

# --- 主頁面內容 ---
st.title("⚛️ 原子存股 (Atomic Wealth)")
st.caption(f"即時匯率參考: 1 USD ≈ {usdtwd_rate:.2f} TWD")

# 讀取資料 (現在只會讀到屬於該使用者的資料)
with st.spinner("正在同步您的雲端資料..."):
    df_transactions = load_data_from_cloud()
    usdtwd_rate = get_usdtwd_rate()
    metrics, df_portfolio = calculate_portfolio(df_transactions, usdtwd_rate)

if df_transactions.empty:
    st.info("👈 您的投資組合目前是空的。請在左側注入第一筆交易能量！")
else:
    # 1. 頂部核心指標 (與先前版本相同)
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("💰 總資產現值 (TWD)", f"${metrics['total_market_value']:,.0f}")
    col_m2.metric("📈 總含息報酬率", f"{metrics['total_return_pct']:+.2f}%", delta=f"{metrics['total_return_pct']:+.2f}%")
    col_m3.metric("💎 總成本殖利率 (YoC)", f"{metrics['avg_yoc_pct']:.2f}%")
    col_m4.metric("💵 已領取歷史總股息 (TWD)", f"${metrics['total_received_dividend']:,.0f}")
    st.divider()

    # 2. 被動收入大廈 (與先前版本相同)
    st.header("🗼 被動收入大廈")
    monthly_income = metrics['monthly_passive_income']
    GOAL_LEVELS = [{"name": "🧱 L1: 帳單自由", "goal": 3000},{"name": "🍜 L2: 伙食補貼", "goal": 10000},{"name": "🏠 L3: 生存基石", "goal": 25000},{"name": "🚗 L4: 薪資替代", "goal": 50000},{"name": "🗽 L5: 財富自由", "goal": 100000},]
    st.markdown(f"""<div style="text-align: center;"><h1 style="font-size: 4rem; margin-bottom: 0; color: #00E5FF;">${monthly_income:,.0f}</h1><p style="font-size: 1.2rem; color: gray;">預估平均每月被動收入 (TWD)</p></div>""", unsafe_allow_html=True); st.write("")
    current_level_idx = 0; 
    for i, level in enumerate(GOAL_LEVELS): 
        if monthly_income < level["goal"]: current_level_idx = i; break
        else: current_level_idx = len(GOAL_LEVELS) - 1
    target_level = GOAL_LEVELS[current_level_idx]; goal_amount = target_level["goal"]; goal_name = target_level["name"]; prev_goal_amount = 0
    if current_level_idx > 0: prev_goal_amount = GOAL_LEVELS[current_level_idx - 1]["goal"]
    if goal_amount > prev_goal_amount: progress = (monthly_income - prev_goal_amount) / (goal_amount - prev_goal_amount)
    else: progress = 1.0
    st.write(f"🎯 **當前目標：{goal_name}** (${goal_amount:,.0f}/月)"); st.progress(max(0.0, min(1.0, progress)))
    if monthly_income < goal_amount: st.caption(f"加油！距離目標還差 ${goal_amount - monthly_income:,.0f} / 月")
    if monthly_income >= GOAL_LEVELS[-1]["goal"]: st.balloons(); st.success("🎉 太神啦！恭喜達成最終財富自由目標！🎉")
    st.divider()

    # 3. 圖表與明細 (與先前版本相同)
    if not df_portfolio.empty:
        st.header("📊 原子結構分析")
        col_chart1, col_chart2, col_chart3 = st.columns(3)
        with col_chart1:
            st.subheader("股債配置 (市值)")
            df_asset_alloc = df_portfolio.groupby('資產類別')['市值(TWD)'].sum().reset_index()
            if not df_asset_alloc.empty:
                fig_asset = px.pie(df_asset_alloc, values='市值(TWD)', names='資產類別', hole=0.4, color='資產類別', color_discrete_map={'Stock': '#2196F3', 'Bond': '#FF9800'})
                fig_asset.update_traces(textposition='inside', textinfo='percent+label'); fig_asset.update_layout(margin=dict(t=0, b=0, l=0, r=0), showlegend=False); st.plotly_chart(fig_asset, use_container_width=True)
        with col_chart2:
            st.subheader("持股佔比 (個股)")
            fig_donut = px.pie(df_portfolio, values='市值(TWD)', names='代號', hole=0.4, color_discrete_sequence=px.colors.qualitative.Set3)
            fig_donut.update_traces(textposition='inside', textinfo='percent'); fig_donut.update_layout(margin=dict(t=0, b=0, l=0, r=0), showlegend=True); st.plotly_chart(fig_donut, use_container_width=True)
        with col_chart3:
            st.subheader("股息貢獻主力 (年預估)")
            df_bar = df_portfolio[df_portfolio['預估年息(TWD)'] > 0].sort_values(by='預估年息(TWD)', ascending=True)
            if not df_bar.empty:
                fig_bar = px.bar(df_bar, x='預估年息(TWD)', y='代號', orientation='h', text='預估年息(TWD)', color='預估年息(TWD)', color_continuous_scale='Tealgrn')
                fig_bar.update_traces(texttemplate='$%{text:,.0f}', textposition='outside', cliponaxis=False); fig_bar.update_layout(xaxis_title="", yaxis_title="", coloraxis_showscale=False, margin=dict(t=20, b=20, l=0, r=100), xaxis=dict(showticklabels=False)); st.plotly_chart(fig_bar, use_container_width=True)
        st.divider()
        st.subheader("🗃️ 資產庫明細")
        st.dataframe(df_portfolio.style.format({"股數": "{:,.2f}", "市值(TWD)": "${:,.0f}", "未實現損益": "${:+,.0f}", "報酬率%": "{:+.2%}", "成本殖利率(YoC)%": "{:.2%}", "預估年息(TWD)": "${:,.0f}"}).applymap(lambda v: 'color: #ff4b4b;' if v < 0 else 'color: #00c853;' if v > 0 else None, subset=["未實現損益", "報酬率%"]), use_container_width=True, hide_index=True, height=300)
    
    # 4. 雲端交易紀錄管理 (與先前版本相同)
    with st.expander("📜 雲端交易紀錄管理 (修改/刪除)", expanded=False):
        st.info("💡 提示：在此處直接修改資料，或勾選「刪除」，最後點擊下方按鈕同步至雲端。")
        df_to_edit = df_transactions.copy(); df_to_edit.insert(0, "Delete", False)
        edited_df = st.data_editor(df_to_edit, column_config={"id": None, "Delete": st.column_config.CheckboxColumn("勾選刪除", default=False), "Date": st.column_config.DateColumn("日期", required=True), "Type": st.column_config.SelectboxColumn("交易類別", options=["BUY", "SELL", "DIVIDEND"], required=True), "AssetType": st.column_config.SelectboxColumn("資產類別", options=["Stock", "Bond"], required=True), "Shares": st.column_config.NumberColumn("股數", format="%.2f", required=True), "Price": st.column_config.NumberColumn("價格/金額", format="%.2f", required=True), "Ticker": st.column_config.TextColumn("代號", required=True), "Currency": st.column_config.SelectboxColumn("幣別", options=["TWD", "USD"], required=True)}, disabled=["Ticker", "Currency"], use_container_width=True, hide_index=True, key="data_editor_cloud", num_rows="fixed")
        if st.button("💾 同步修改至雲端 (Sync to Cloud)", type="primary"):
            with st.spinner("正在同步資料至 Supabase..."):
                try:
                    to_delete_ids = edited_df[edited_df["Delete"]]["id"].tolist()
                    if to_delete_ids: supabase.table("transactions").delete().in_("id", to_delete_ids).execute(); st.toast(f"🗑️ 已刪除 {len(to_delete_ids)} 筆紀錄", icon="🗑️")
                    to_update_df = edited_df[~edited_df["Delete"]]; updates_count = 0
                    for index, row in to_update_df.iterrows():
                        row_id = row['id']; update_data = {"Date": str(row["Date"]), "Type": row["Type"], "AssetType": row["AssetType"], "Shares": row["Shares"], "Price": row["Price"], "Note": row["Note"]}
                        supabase.table("transactions").update(update_data).eq("id", row_id).execute(); updates_count += 1
                    if updates_count > 0: st.toast(f"✏️ 已更新 {updates_count} 筆紀錄", icon="✏️")
                    st.success("✅ 雲端資料同步完成！"); time.sleep(1); st.rerun()
                except Exception as e: st.error(f"❌ 同步失敗: {e}")