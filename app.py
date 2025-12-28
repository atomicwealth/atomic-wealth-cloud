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
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Supabase 連線失敗，請檢查 Secrets 設定。錯誤: {e}")
        st.stop()

supabase: Client = init_connection()

# --- 3. 全域變數與輔助函式 ---
if 'user' not in st.session_state:
    st.session_state['user'] = None

# 取得即時匯率 (快取 1 小時)
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

# 批量獲取目前股價 (快取 10 分鐘)
@st.cache_data(ttl=600)
def get_current_prices(tickers):
    if not tickers: return {}
    try:
        tickers_str = " ".join(tickers)
        # 使用 yf.download 批量獲取
        data = yf.download(tickers_str, period="1d", group_by='ticker')
        prices = {}
        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    # 如果只有一支股票，資料結構不同
                    price = data['Close'].iloc[-1]
                else:
                    price = data[ticker]['Close'].iloc[-1]
                prices[ticker] = price
            except Exception:
                 # 抓不到就填 None
                prices[ticker] = None
        return prices
    except Exception as e:
        st.warning(f"股價獲取部分失敗: {e}")
        return {}

# --- 4. 登入/註冊介面函式 ---
def login_form():
    st.header("🔐 會員登入 / 註冊")
    tab1, tab2 = st.tabs(["登入", "註冊新帳號"])
    
    # ... (登入 Tab) ...
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
                    # 這裡可以捕捉具體的錯誤訊息，例如密碼錯誤
                    st.error(f"登入失敗: 請檢查帳號密碼。({e})")

    # ... (註冊 Tab) ...
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
                        # 檢查是否需要信箱驗證
                        if response.user and response.user.identities and len(response.user.identities) > 0:
                             st.success("註冊成功！請去信箱收驗證信，驗證後即可登入。")
                        else:
                             # 有些 Supabase 設定是註冊後自動登入，或不需要驗證
                             st.success("註冊成功！請切換到「登入」頁籤登入。")
                except Exception as e:
                    st.error(f"註冊失敗: {e}")

# === 主程式邏輯 ===
if st.session_state['user'] is None:
    # 如果沒登入，顯示登入表單
    login_form()
else:
    # 已登入，顯示主畫面
    user_email = st.session_state['user'].email

    # --- B1. 側邊欄 (輸入資料) ---
    with st.sidebar:
        st.write(f"👤 **{user_email}**")
        if st.button("登出", type="secondary"):
            supabase.auth.sign_out()
            st.session_state['user'] = None
            st.rerun()
        
        st.divider()
        st.header("➕ 新增交易")
        
        # 使用 form 來包裹輸入項，避免每次輸入都重新整理
        with st.form("add_trans", clear_on_submit=True):
            date = st.date_input("日期")
            ticker = st.text_input("代號 (例如: 2330.TW, AAPL)").upper().strip()
            col1, col2 = st.columns(2)
            with col1:
                trans_type = st.selectbox("類別", ["Buy", "Sell"])
                currency = st.selectbox("幣別", ["TWD", "USD"])
            with col2:
                asset_type = st.selectbox("資產", ["Stock", "ETF", "Crypto"])
                
            amount = st.number_input("數量 (股/顆)", min_value=0.0001, format="%.4f")
            price = st.number_input("單價", min_value=0.0001, format="%.2f")
            notes = st.text_area("備註 (選填)")

            submitted = st.form_submit_button("🚀 確認注入", type="primary")
            
            if submitted:
                # 基本驗證
                if not ticker:
                    st.error("請輸入股票代號。")
                elif amount <= 0 or price <= 0:
                    st.error("數量和價格必須大於 0。")
                else:
                    # --- 關鍵修改：準備要寫入的資料 ---
                    # 我們已經重建資料庫，設定好 user_id 會自動填寫。
                    # 所以這裡只要準備交易資料本身就好，不需要手動加 user_id。
                    new_data = {
                        "date": str(date),
                        "ticker": ticker,
                        "type": trans_type,
                        "currency": currency,
                        "asset_type": asset_type.split(" ")[0], # 只取第一個單字
                        "amount": amount,
                        "price": price,
                        "notes": notes if notes else None # 如果沒寫備註就傳 None
                    }
                    
                    try:
                        with st.spinner("正在寫入區塊鏈... (誤) 正在寫入資料庫..."):
                            # 呼叫 Supabase 寫入資料
                            data, count = supabase.table("transactions").insert(new_data).execute()
                        
                        st.toast("✅ 交易成功注入！", icon="🎉")
                        # 暫停一下讓使用者看到成功訊息
                        time.sleep(1)
                        # 重新整理網頁以顯示最新資料
                        st.rerun()
                        
                    except Exception as e:
                        # 如果失敗，把錯誤訊息印出來，方便除錯
                        st.error(f"寫入失敗，請截圖給開發者: {e}")

    # --- B2. 主畫面 (儀表板) ---
    st.title("⚛️ 原子存股 (雲端版)")
    st.caption(f"即時匯率參考: 1 USD ≈ {usdtwd_rate:.2f} TWD")
    
    # 1. 從 Supabase 讀取目前使用者的資料
    try:
        # RLS 政策會確保只撈到自己的資料
        response = supabase.table("transactions").select("*").order("date", desc=True).execute()
        df = pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"讀取資料失敗: {e}")
        df = pd.DataFrame() # 發生錯誤時建立空 DataFrame 避免後面崩潰

    # 2. 判斷是否有資料
    if df.empty:
        st.info("👋 歡迎！目前還沒有任何交易紀錄。請從左側側邊欄新增您的第一筆投資！")
        # 可以在這裡放一張空的示意圖或教學
    else:
        # 3. 資料處理與計算
        # 確保數值欄位是數字型態
        df['amount'] = pd.to_numeric(df['amount'])
        df['price'] = pd.to_numeric(df['price'])
        
        # 計算每一筆的總成本 (換算回台幣)
        df['total_cost_twd'] = df.apply(
            lambda x: (x['amount'] * x['price']) * (usdtwd_rate if x['currency'] == 'USD' else 1),
            axis=1
        )

        # 計算關鍵指標
        # 總投入 = 所有「買入」類別的總成本加總
        total_invested = df[df['type'] == 'Buy']['total_cost_twd'].sum()
        
        # 取得所有持股的現價
        unique_tickers = df['ticker'].unique().tolist()
        current_prices = get_current_prices(unique_tickers)

        # 計算目前市值
        def calculate_current_value(row):
            ticker = row['ticker']
            price = current_prices.get(ticker)
            
            # 如果抓不到價格或價格是空值，就無法計算市值
            if price is None or pd.isna(price):
                return None 

            try:
                # 強制轉型為 float 進行計算，避免型別錯誤
                amount_val = float(row['amount'])
                price_val = float(price)
                market_value_original = amount_val * price_val
                
                # 轉換回台幣
                return market_value_original * (usdtwd_rate if row['currency'] == 'USD' else 1)
            except Exception:
                # 如果計算過程出錯 (例如資料有問題)，回傳 None
                return None

        df['current_value_twd'] = df.apply(calculate_current_value, axis=1)
        
        # 總市值 = 所有成功計算出市值的加總
        total_market_value = df['current_value_twd'].sum()

        # 計算損益
        unrealized_pl = total_market_value - total_invested
        pl_percentage = (unrealized_pl / total_invested * 100) if total_invested > 0 else 0

        # 4. 顯示關鍵指標 (Metrics)
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("💰 總投入成本 (TWD)", f"${total_invested:,.0f}")
        col_m2.metric("📈 目前總市值 (TWD)", f"${total_market_value:,.0f}", 
                      delta=f"${unrealized_pl:,.0f} ({pl_percentage:+.1f}%)")
        # col_m3 可以放其他指標，例如現金餘額或今年股息

        st.divider()

        # 5. 顯示圖表與詳細記錄
        tab_chart, tab_data = st.tabs(["📊 資產分布", "📝 交易紀錄明細"])

        with tab_chart:
            # 簡單的資產圓餅圖
            if total_market_value > 0:
                # 這裡只是一個簡單的範例，用 ticker 來分類
                # 實際應用可能需要更複雜的邏輯來計算每個資產的現值
                fig = px.pie(df, names='asset_type', title='資產類別分布 (以交易筆數計算)')
                st.plotly_chart(fig, use_container_width=True)
            else:
                 st.info("尚未有足夠資料顯示圖表。")

        with tab_data:
            # 整理要顯示的欄位，讓表格更好看
            display_df = df[['date', 'ticker', 'type', 'amount', 'price', 'currency', 'asset_type', 'notes']].copy()
            # 格式化日期
            display_df['date'] = pd.to_datetime(display_df['date']).dt.strftime('%Y-%m-%d')
            
            st.dataframe(
                display_df.style.format({
                    'amount': '{:,.4f}', 
                    'price': '{:,.2f}'
                }),
                use_container_width=True,
                hide_index=True,
                 column_config={
                    "date": "日期",
                    "ticker": "代號",
                    "type": "類別",
                    "amount": "數量",
                    "price": "單價",
                    "currency": "幣別",
                    "asset_type": "資產",
                    "notes": "備註"
                }
            )