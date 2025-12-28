import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
from supabase import create_client, Client
import time

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="原子存股 (雲端版)", page_icon="⚛️", layout="wide")

# --- 2. 初始化 Supabase 連線 ---
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase: Client = init_connection()
except Exception as e:
    st.error(f"無法連接到資料庫，請檢查 secrets 設定。錯誤訊息: {e}")
    st.stop()

# --- 3. 全域變數與輔助函式 ---
if 'user' not in st.session_state:
    st.session_state['user'] = None

# 取得即時匯率
@st.cache_data(ttl=3600)
def get_usdtwd_rate():
    try:
        usdtwd = yf.Ticker("TWD=X")
        history = usdtwd.history(period="1d")
        if not history.empty:
            return history['Close'].iloc[-1]
        return 31.0
    except:
        return 31.0

usdtwd_rate = get_usdtwd_rate()

# 批量獲取目前股價
@st.cache_data(ttl=600)
def get_current_prices(tickers):
    if not tickers: return {}
    try:
        tickers_str = " ".join(tickers)
        data = yf.download(tickers_str, period="1d", group_by='ticker')
        prices = {}
        for ticker in tickers:
            try:
                if len(tickers) == 1: price = data['Close'].iloc[-1]
                else: price = data[ticker]['Close'].iloc[-1]
                prices[ticker] = price
            except: prices[ticker] = None
        return prices
    except: return {}

# --- 4. 登入/註冊介面函式 ---
def login_form():
    st.header("🔐 會員登入 / 註冊")
    tab1, tab2 = st.tabs(["登入", "註冊新帳號"])
    with tab1:
        email_in = st.text_input("電子信箱", key="login_email")
        password_in = st.text_input("密碼", type="password", key="login_pass")
        if st.button("登入", type="primary"):
            if not email_in or not password_in:
                st.warning("請輸入信箱和密碼。")
            else:
                try:
                    with st.spinner("正在驗證身分..."):
                        response = supabase.auth.sign_in_with_password({"email": email_in, "password": password_in})
                        st.session_state['user'] = response.user
                        st.success("登入成功！")
                        time.sleep(0.5)
                        st.rerun()
                except Exception as e:
                    st.error(f"登入失敗: {e}")
    with tab2:
        email_reg = st.text_input("電子信箱", key="reg_email")
        password_reg = st.text_input("設定密碼 (至少6位數)", type="password", key="reg_pass")
        if st.button("註冊"):
            if not email_reg or len(password_reg) < 6:
                st.warning("請輸入有效的信箱，密碼需>6位。")
            else:
                try:
                    with st.spinner("正在建立帳號..."):
                        response = supabase.auth.sign_up({"email": email_reg, "password": password_reg})
                        if response.user and response.user.identities and len(response.user.identities) > 0:
                             st.success("註冊成功！請去信箱收驗證信。")
                        else:
                             st.success("註冊成功！請切換到「登入」頁籤登入。")
                except Exception as e:
                    st.error(f"註冊失敗: {e}")

# === 主程式邏輯 ===
if st.session_state['user'] is None:
    login_form()
else:
    user_email = st.session_state['user'].email
    # --- B1. 側邊欄 ---
    with st.sidebar:
        st.write(f"👤 **{user_email}**")
        if st.button("登出", type="secondary"):
            supabase.auth.sign_out()
            st.session_state['user'] = None
            st.rerun()
        st.divider()
        st.header("➕ 新增交易")
        with st.form("add_trans", clear_on_submit=True):
            date = st.date_input("日期")
            ticker = st.text_input("代號").upper().strip()
            trans_type = st.selectbox("類別", ["Buy", "Sell"])
            currency = st.selectbox("幣別", ["TWD", "USD"])
            asset_type = st.selectbox("資產", ["Stock", "Crypto", "Cash"])
            amount = st.number_input("數量", min_value=0.0, format="%.4f")
            price = st.number_input("單價", min_value=0.0, format="%.2f")
            submitted = st.form_submit_button("🚀 確認注入")
            if submitted:
                if not ticker or amount <= 0 or price <= 0:
                    st.error("資料不完整。")
                else:
                   # [🔥🔥🔥 強制手動加入 user_id 🔥🔥🔥]
                    # 我們不再信任資料庫的自動填寫功能，直接在程式碼裡把 ID 塞進去
                    current_user_id = st.session_state['user'].id

                    new_data = {
                        "user_id": current_user_id, # <--- 關鍵！明確告訴資料庫這是誰的資料
                        "date": str(date),
                        "ticker": ticker,
                        "type": trans_type,
                        "currency": currency,
                        "asset_type": asset_type.split(" ")[0],
                        "amount": amount,
                        "price": price
                    }
                    
                    # [🔍 除錯用] 把要傳送的資料印在網頁上給我們看，證明 ID 有在裡面
                    st.write("準備寫入資料庫的 Payload:", new_data)
                    try:
                        with st.spinner("正在寫入..."):
                            supabase.table("transactions").insert(new_data).execute()
                        st.toast("✅ 交易成功！", icon="🎉")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"寫入失敗: {e}")

    # --- B2. 主畫面 ---
    st.title("⚛️ 原子存股 (雲端版)")
    st.caption(f"匯率參考: 1 USD ≈ {usdtwd_rate:.2f} TWD")
    
    try:
        response = supabase.table("transactions").select("*").order("date", desc=True).execute()
        df = pd.DataFrame(response.data)
    except: df = pd.DataFrame()

    if df.empty:
        st.info("目前無資料，請從左側新增。")
    else:
        df['amount'] = pd.to_numeric(df['amount'])
        df['price'] = pd.to_numeric(df['price'])
        df['total_cost_twd'] = df.apply(lambda x: (x['amount'] * x['price']) * (usdtwd_rate if x['currency'] == 'USD' else 1), axis=1)
        total_invested = df[df['type'] == 'Buy']['total_cost_twd'].sum()
        
        st.metric("總投入成本 (TWD)", f"${total_invested:,.0f}")
        st.divider()
        st.subheader("📝 交易紀錄")
        st.dataframe(df, use_container_width=True, hide_index=True)