import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
from supabase import create_client, Client
import time

# --- 1. 頁面基礎設定 (必須放在最前面) ---
st.set_page_config(
    page_title="原子存股 (雲端版)",
    page_icon="⚛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- 2. 初始化 Supabase 連線 ---
# 使用 st.cache_resource 確保只連線一次，提高效能
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

# 初始化 session_state 中的使用者狀態
if 'user' not in st.session_state:
    st.session_state['user'] = None

# 取得即時匯率 (USD to TWD) - 移到這裡確保提前執行
@st.cache_data(ttl=3600) # 快取 1 小時
def get_usdtwd_rate():
    try:
        usdtwd = yf.Ticker("TWD=X")
        # 修正語法，確保分行書寫
        history = usdtwd.history(period="1d")
        if not history.empty:
            return history['Close'].iloc[-1]
        return 31.0 # 預設值
    except Exception as e:
        print(f"匯率抓取失敗: {e}")
        return 31.0 # 失敗時的預設值

# 執行匯率抓取
usdtwd_rate = get_usdtwd_rate()


# 批量獲取目前股價的函式
@st.cache_data(ttl=600) # 快取 10 分鐘
def get_current_prices(tickers):
    if not tickers:
        return {}
    try:
        # yfinance 可以一次抓多檔股票，用空白分隔
        tickers_str = " ".join(tickers)
        data = yf.download(tickers_str, period="1d", group_by='ticker')
        prices = {}
        for ticker in tickers:
            try:
                # 處理單一檔或多檔回傳格式的差異
                if len(tickers) == 1:
                     price = data['Close'].iloc[-1]
                else:
                     price = data[ticker]['Close'].iloc[-1]
                prices[ticker] = price
            except:
                prices[ticker] = None # 抓不到就給 None
        return prices
    except Exception as e:
        st.error(f"無法獲取即時股價: {e}")
        return {}


# --- 4. 登入/註冊介面函式 ---
def login_form():
    st.header("🔐 會員登入 / 註冊")
    st.caption("請先登入以管理您的專屬投資組合。您的資料受到最高級別的安全保護。")

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
                        st.success("登入成功！正在進入系統...")
                        time.sleep(1)
                        st.rerun() # 重新執行以進入主畫面
                except Exception as e:
                    st.error(f"登入失敗: {e}")

    with tab2:
        email_reg = st.text_input("電子信箱", key="reg_email")
        password_reg = st.text_input("設定密碼 (至少6位數)", type="password", key="reg_pass")
        if st.button("註冊"):
            if not email_reg or len(password_reg) < 6:
                st.warning("請輸入有效的信箱，且密碼需大於6位數。")
            else:
                try:
                    with st.spinner("正在建立帳號..."):
                        response = supabase.auth.sign_up({"email": email_reg, "password": password_reg})
                        # 檢查是否需要信箱驗證
                        if response.user and response.user.identities and len(response.user.identities) > 0:
                             st.success("註冊成功！請前往您的信箱收取驗證信以啟用帳號。")
                        else:
                             #有時候 Supabase 設定不需驗證會直接註冊成功
                             st.success("註冊成功！請切換到「登入」頁籤進行登入。")

                except Exception as e:
                    st.error(f"註冊失敗: {e} (此信箱可能已被註冊)")


# =========================================
# === 主程式邏輯 (Main Application Flow) ===
# =========================================

# 檢查是否已登入
if st.session_state['user'] is None:
    # --- 狀態 A: 未登入，顯示登入表單 ---
    login_form()

else:
    # --- 狀態 B: 已登入，顯示主儀表板 ---
    user_email = st.session_state['user'].email

    # --- B1. 側邊欄 (Sidebar) ---
    with st.sidebar:
        st.write(f"👤 嗨，**{user_email}**")
        if st.button("登出", type="secondary"):
            supabase.auth.sign_out()
            st.session_state['user'] = None
            st.rerun()

        st.divider()
        st.header("➕ 注入原子能量 (新增交易)")

        with st.form("add_transaction_form", clear_on_submit=True):
            date = st.date_input("日期")
            ticker = st.text_input("代號 (例如: 2330.TW 或 AAPL)").upper().strip()
            trans_type = st.selectbox("交易類別", ["Buy", "Sell"])
            currency = st.selectbox("幣別", ["TWD", "USD"])
            asset_type = st.selectbox("資產類別", ["Stock (股票/ETF)", "Crypto (加密貨幣)", "Cash (現金)"])
            amount = st.number_input("股數/數量", min_value=0.0, format="%.4f")
            price = st.number_input("成交單價 (原幣)", min_value=0.0, format="%.2f")
            notes = st.text_input("備註 (選填)")

            submitted = st.form_submit_button("🚀 確認注入 (Inject)")
            if submitted:
                if not ticker or amount <= 0 or price <= 0:
                    st.error("請輸入正確的代號、數量和價格。")
                else:
                    # 準備要寫入的資料字典
                    # 注意：我們不需要手動加入 user_id，Supabase RLS 會自動處理！
                    new_data = {
                        "date": str(date),
                        "ticker": ticker,
                        "type": trans_type,
                        "currency": currency,
                        "asset_type": asset_type.split(" ")[0],
                        "amount": amount,
                        "price": price,
                        "notes": notes if notes else None
                    }
                    try:
                        with st.spinner("正在寫入區塊鏈..."):
                            supabase.table("transactions").insert(new_data).execute()
                        st.toast("✅ 交易注入成功！", icon="🎉")
                        time.sleep(1)
                        st.rerun() # 重新整理以顯示新資料
                    except Exception as e:
                        st.error(f"寫入失敗: {e}")


    # --- B2. 主畫面內容 (Main Content) ---
    st.title("⚛️ 原子存股 (Atomic Wealth) | ☁️ 雲端版")
    # 使用已定義好的匯率變數
    st.caption(f"即時匯率參考: 1 USD ≈ {usdtwd_rate:.2f} TWD")

    # 1. 從 Supabase 讀取資料 (RLS 會確保只讀到当前使用者的資料)
    try:
        response = supabase.table("transactions").select("*").order("date", desc=True).execute()
        df = pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"讀取資料失敗: {e}")
        df = pd.DataFrame() # 確保 df 存在，避免後面報錯

    if df.empty:
        st.info("👆 您的雲端資料庫目前是空的。請在左側側邊欄注入您的第一筆交易能量！")
    else:
        # 2. 資料處理與計算
        # 確保數值型態正確
        df['amount'] = pd.to_numeric(df['amount'])
        df['price'] = pd.to_numeric(df['price'])
        # 計算原幣總成本
        df['total_cost_original'] = df['amount'] * df['price']
        # 計算台幣總成本 (若是 USD 則乘以匯率)
        df['total_cost_twd'] = df.apply(lambda x: x['total_cost_original'] * (usdtwd_rate if x['currency'] == 'USD' else 1), axis=1)

        # 簡單計算買入總成本 (尚未處理賣出邏輯，v8.1優化)
        total_invested_twd = df[df['type'] == 'Buy']['total_cost_twd'].sum()

        # --- 嘗試計算現值 (進階功能) ---
        # 找出所有不重複的股票代號
        unique_tickers = df['ticker'].unique().tolist()
        # 批量獲取現價
        current_prices = get_current_prices(unique_tickers)

        # 計算每一筆交易的目前價值
        def calculate_current_value(row):
            ticker = row['ticker']
            price = current_prices.get(ticker)
            if price is None or pd.isna(price):
                return None # 抓不到價格就不算
            market_value_original = row['amount'] * price
            # 轉換回台幣
            return market_value_original * (usdtwd_rate if row['currency'] == 'USD' else 1)

        df['market_value_twd'] = df.apply(calculate_current_value, axis=1)
        
        # 計算總市值 (只計算買入且有抓到價格的)
        total_market_value_twd = df[(df['type'] == 'Buy') & (df['market_value_twd'].notnull())]['market_value_twd'].sum()
        
        # 計算損益 (僅供參考，未實現損益)
        unrealized_pl = total_market_value_twd - total_invested_twd
        pl_percentage = (unrealized_pl / total_invested_twd * 100) if total_invested_twd > 0 else 0

        # 3. 顯示關鍵指標 (Metrics)
        st.subheader("📊 投資組合總覽 (Portfolio Overview)")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("總投入成本 (TWD)", f"${total_invested_twd:,.0f}")
        with col2:
            st.metric("目前總市值 (TWD, 估計)", f"${total_market_value_twd:,.0f}", help="根據最近一次收盤價估算，可能有延遲。")
        with col3:
            st.metric("未實現損益 (TWD, 估計)", f"${unrealized_pl:,.0f}", f"{pl_percentage:.2f}%")

        st.divider()

        # 4. 視覺化圖表 (Charts)
        col_chart1, col_chart2 = st.columns(2)
        with col_chart1:
             st.subheader("📈 資產配置 (依類別)")
             if not df.empty:
                 fig_pie = px.pie(df[df['type'] == 'Buy'], values='total_cost_twd', names='asset_type', title='投入成本分佈', hole=0.4)
                 st.plotly_chart(fig_pie, use_container_width=True)

        with col_chart2:
             st.subheader("💰 持股佔比 (依代號)")
             if not df.empty:
                 # 依代號分組加總成本
                 df_grouped = df[df['type'] == 'Buy'].groupby('ticker')['total_cost_twd'].sum().reset_index()
                 fig_bar = px.bar(df_grouped, x='ticker', y='total_cost_twd', title='各標的投入成本', color='ticker')
                 st.plotly_chart(fig_bar, use_container_width=True)


        st.divider()

        # 5. 交易紀錄明細 (Dataframe with Delete)
        st.subheader("📝 交易紀錄明細 (Transaction History)")
        st.caption("勾選左側方塊並點擊下方的刪除按鈕可移除資料。")

        # 使用 st.data_editor 讓使用者可以勾選要刪除的列
        # 增加一個虛擬的 "Delete" 欄位供勾選
        df_to_show = df.copy()
        df_to_show.insert(0, "Delete", False)

        # 設定顯示的欄位順序和格式
        edited_df = st.data_editor(
            df_to_show,
            column_config={
                "Delete": st.column_config.CheckboxColumn(
                    "刪除",
                    help="勾選以準備刪除",
                    default=False,
                ),
                "id": None, # 隱藏系統 ID
                "user_id": None, # 隱藏使用者 ID
                "created_at": None, # 隱藏建立時間
                "date": "日期",
                "ticker": "代號",
                "type": "類別",
                "currency": "幣別",
                "amount": st.column_config.NumberColumn("數量", format="%.4f"),
                "price": st.column_config.NumberColumn("單價", format="$%.2f"),
                "total_cost_original": st.column_config.NumberColumn("總成本(原幣)", format="$%.2f"),
                "total_cost_twd": st.column_config.NumberColumn("總成本(TWD)", format="$%d"),
                "market_value_twd": st.column_config.NumberColumn("現值(TWD,估)", format="$%d"),
            },
            disabled=["date", "ticker", "type", "currency", "amount", "price", "notes", "total_cost_original", "total_cost_twd", "asset_type", "market_value_twd"], # 禁止編輯資料，只允許勾選刪除
            hide_index=True,
            use_container_width=True,
            key="data_editor_rows"
        )

        # 處理刪除動作
        rows_to_delete = edited_df[edited_df["Delete"] == True]
        if not rows_to_delete.empty:
            if st.button(f"⚠️ 確認刪除選取的 {len(rows_to_delete)} 筆資料", type="primary"):
                try:
                    with st.spinner("正在從資料庫移除..."):
                        # 收集要刪除的 ID
                        ids_to_delete = rows_to_delete['id'].tolist()
                        # 呼叫 Supabase 進行刪除 (RLS 會確保只能刪除自己的)
                        supabase.table("transactions").delete().in_("id", ids_to_delete).execute()
                    st.toast("✅ 資料已刪除！")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"刪除失敗: {e}")