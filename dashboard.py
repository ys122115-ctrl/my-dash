import streamlit as st
import pandas as pd
import numpy as np
from pandas.tseries.offsets import BusinessDay
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(page_title="출고 현황 대시보드", page_icon="📦", layout="wide")

@st.cache_data
def load_raw_data(file):
    ext = file.name.split('.')[-1].lower()
    if ext == 'csv':
        try: df = pd.read_csv(file, encoding='cp949')
        except: df = pd.read_csv(file, encoding='utf-8-sig')
    else:
        df = pd.read_excel(file, sheet_name='Sheet1')
        
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].replace(r'[\x00-\x08\x0B-\x0C\x0E-\x1F]', '', regex=True)
            df[col] = df[col].apply(lambda x: f" {x}" if isinstance(x, str) and str(x).startswith('=') else x)
    df['출고완료'] = pd.to_datetime(df.iloc[:, 4], errors='coerce')
    df['요청일자'] = pd.to_datetime(df.iloc[:, 39], errors='coerce')
    df['출하수량'] = pd.to_numeric(df.iloc[:, 11], errors='coerce').fillna(0)
    return df

def process_data_for_dashboard(df, start_date, end_date):
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date).replace(hour=23, minute=59, second=59)
    p_df = df[(df['출고완료'] >= start) & (df['출고완료'] <= end)].copy()
    if p_df.empty: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), 0.0

    # --- [B2C 3대 유형 매핑] ---
    b2c_mapping = {'B2C': '국내B2C', 'B2C(우체국)': '국내B2C', '택배무상출고': '무상출고', 
                   '택배무상출고(ERP)': '무상출고', '택배판매출고(ERP)': '무상출고', '해외출고': '해외B2C'}
    df_b2c = p_df[p_df.iloc[:, 6].isin(b2c_mapping.keys())].copy()
    df_b2c['변환유형'] = df_b2c.iloc[:, 6].map(b2c_mapping)
    
    # 1. B2C 유형별 요약
    b2c_res = df_b2c.groupby('변환유형').agg({df.columns[0]: 'nunique', '출하수량': 'sum'}).reset_index()
    b2c_res.columns = ['출고유형', '출고건수', '출고수량']
    
    # 2. B2C 거래처별 집계
    df_b2c_pure = df_b2c[df_b2c['변환유형'] == '국내B2C'].copy()
    customer_col = df.columns[8]
    b2c_cust_res = df_b2c_pure.groupby(customer_col).agg({df.columns[0]: 'nunique', '출하수량': 'sum'}).reset_index()
    b2c_cust_res.columns = ['거래처', '출고건수', '출고수량']

    # --- [B2B 정리: 일반 출고 건만 수집] ---
    df_b2b = p_df[p_df.iloc[:, 6] == '일반'].copy()
    if df_b2b.empty: return b2c_res, b2c_cust_res, pd.DataFrame(), 0.0
    
    df_b2b['건수기준'] = df_b2b.iloc[:, 4].astype(str) + "_" + df_b2b.iloc[:, 8].astype(str)
    def calc_adj(dt):
        if pd.isna(dt): return dt
        return dt.replace(hour=0, minute=0, second=0) if dt.hour < 12 else (dt + BusinessDay(1)).replace(hour=0, minute=0, second=0)
    
    df_b2b['조정요청일'] = df_b2b['요청일자'].apply(calc_adj)
    df_b2b['최초등록영업일'] = df_b2b.groupby('건수기준')['조정요청일'].transform('min')
    df_b2b['추가수량값'] = np.where(df_b2b['조정요청일'] > df_b2b['최초등록영업일'], df_b2b['출하수량'], 0)
    
    team_col = df.columns[9]
    u_df = df_b2b.groupby('건수기준').agg({team_col: 'first', '출고완료': 'first', '요청일자': 'max', '조정요청일': 'max'}).reset_index()
    u_df['리드타임'] = (u_df['출고완료'] - u_df['조정요청일']).dt.total_seconds() / 86400
    u_df['긴급건'] = u_df['리드타임'].apply(lambda x: 1 if pd.notna(x) and x <= 2 else 0)
    
    b2b_res = pd.concat([u_df.groupby(team_col).agg({'건수기준': 'count', '긴급건': 'sum', '리드타임': 'mean'}), 
                         df_b2b.groupby(team_col).agg({'출하수량': 'sum', '추가수량값': 'sum'})], axis=1).reset_index()
    b2b_res.columns = ['팀', '출고건수', '긴급건', '평균 리드타임', '총 출하수량', '추가 수량']
    b2b_res = b2b_res[['팀', '출고건수', '총 출하수량', '긴급건', '추가 수량', '평균 리드타임']]
    
    return b2c_res, b2c_cust_res, b2b_res, round(u_df['리드타임'].mean(), 2) if not u_df.empty else 0

# =====================================================================
# 대시보드 UI 구성
# =====================================================================
st.title("📦 물류 통합 대시보드")
st.sidebar.header("📅 기간 설정")

today = datetime.today().date()
current_start = today - timedelta(days=6)
past_start = current_start - timedelta(days=7)
past_end = current_start - timedelta(days=1)

c_r = st.sidebar.date_input("비교 기준 기간 (과거)", [past_start, past_end])
n_r = st.sidebar.date_input("현재 분석 기간 (현재)", [current_start, today])

up = st.file_uploader("엑셀 또는 CSV 파일을 올려주세요", type=["xlsx", "xls", "csv"])

if up and len(c_r) == 2 and len(n_r) == 2:
    raw = load_raw_data(up)
    c_b2c, c_b2c_cust, c_b2b, c_lt = process_data_for_dashboard(raw, c_r[0], c_r[1])
    n_b2c, n_b2c_cust, n_b2b, n_lt = process_data_for_dashboard(raw, n_r[0], n_r[1])
    
    # -----------------------------------------------------------------
    # B2C 전주 대비 요약 섹션
    # -----------------------------------------------------------------
    st.subheader(f"🛒 B2C 전주 대비 요약 ({n_r[0]} ~ {n_r[1]})")
    b2c_categories = ['국내B2C', '해외B2C', '무상출고']
    b2c_cols = st.columns(3)

    for i, cat in enumerate(b2c_categories):
        with b2c_cols[i]:
            st.markdown(f"**[{cat}]**")
            
            c_cnt = n_b2c.loc[n_b2c['출고유형'] == cat, '출고건수'].sum() if not n_b2c.empty else 0
            p_cnt = c_b2c.loc[c_b2c['출고유형'] == cat, '출고건수'].sum() if not c_b2c.empty else 0
            c_qty = n_b2c.loc[n_b2c['출고유형'] == cat, '출고수량'].sum() if not n_b2c.empty else 0
            p_qty = c_b2c.loc[c_b2c['출고유형'] == cat, '출고수량'].sum() if not c_b2c.empty else 0
            
            diff_cnt = c_cnt - p_cnt
            pct_cnt = f"({(diff_cnt / p_cnt * 100):+.1f}%)" if p_cnt > 0 else ("(New)" if c_cnt > 0 else "")
            
            diff_qty = c_qty - p_qty
            pct_qty = f"({(diff_qty / p_qty * 100):+.1f}%)" if p_qty > 0 else ("(New)" if c_qty > 0 else "")

            st.metric("출고건수", f"{c_cnt:,} 건", delta=f"{diff_cnt:,} 건 {pct_cnt}")
            st.metric("출하수량", f"{c_qty:,.0f} EA", delta=f"{diff_qty:,.0f} EA {pct_qty}")

    # B2C 거래처별 전주 대비 상세 비교
    st.markdown("#### 🔍 B2C 거래처별 전주 대비 출고 현황 비교")
    if not n_b2c_cust.empty:
        b2c_merged = pd.merge(c_b2c_cust, n_b2c_cust, on='거래처', how='outer', suffixes=('_과거', '_현재')).fillna(0)
        b2c_merged['수량 증감'] = b2c_merged['출고수량_현재'] - b2c_merged['출고수량_과거']
        b2c_merged['건수 증감'] = b2c_merged['출고건수_현재'] - b2c_merged['출고건수_과거']
        
        b2c_merged = b2c_merged[['거래처', '출고건수_과거', '출고건수_현재', '건수 증감', '출고수량_과거', '출고수량_현재', '수량 증감']]
        b2c_merged.columns = ['거래처', '과거 건수', '현재 건수', '건수 증감', '과거 수량(EA)', '현재 수량(EA)', '수량 증감']
        
        b2c_merged = b2c_merged.sort_values(by='현재 건수', ascending=False)
        
        tab1, tab2 = st.tabs(["📊 거래처별 건수 비교 그래프", "📄 상세 데이터 표"])
        with tab1:
            fig = px.bar(
                b2c_merged.head(10), x='거래처', y=['과거 건수', '현재 건수'],
                barmode='group',
                title="B2C 주요 거래처별 출고 건수 비교 상위 10개사 (과거 vs 현재)",
                color_discrete_sequence=['#A7BED3', '#FF6B6B'],
                text_auto='.0f'
            )
            fig.update_layout(
                xaxis_tickangle=-45, 
                yaxis_title="출고 건수 (건)",
                legend_title_text=''
            )
            st.plotly_chart(fig, use_container_width=True)
        with tab2:
            st.dataframe(b2c_merged.style.format({
                "과거 건수": "{:,.0f}", "현재 건수": "{:,.0f}", "건수 증감": "{:+,.0f}",
                "과거 수량(EA)": "{:,.0f}", "현재 수량(EA)": "{:,.0f}", "수량 증감": "{:+,.0f}"
            }), use_container_width=True, hide_index=True)
    else:
        st.info("비교할 B2C 거래처 데이터가 없습니다.")

    st.divider()

    # -----------------------------------------------------------------
    # B2B 요약 섹션
    # -----------------------------------------------------------------
    st.subheader(f"🏢 B2B 전주 대비 요약 ({n_r[0]} ~ {n_r[1]})")
    col1, col2, col3, col4 = st.columns(4)
    
    n_b2b_cnt = n_b2b['출고건수'].sum() if not n_b2b.empty else 0
    c_b2b_cnt = c_b2b['출고건수'].sum() if not c_b2b.empty else 0
    n_b2b_qty = n_b2b['총 출하수량'].sum() if not n_b2b.empty else 0
    c_b2b_qty = c_b2b['총 출하수량'].sum() if not c_b2b.empty else 0
    n_b2b_urg = n_b2b['긴급건'].sum() if not n_b2b.empty else 0
    c_b2b_urg = c_b2b['긴급건'].sum() if not c_b2b.empty else 0

    col1.metric("B2B 총 출고건수", f"{n_b2b_cnt:,} 건", delta=f"{n_b2b_cnt - c_b2b_cnt:,} 건")
    col2.metric("총 출하수량", f"{n_b2b_qty:,.0f} EA", delta=f"{n_b2b_qty - c_b2b_qty:,.0f} EA")
    col3.metric("평균 리드타임", f"{n_lt:.2f} 일", delta=f"{n_lt - c_lt:.2f} 일", delta_color="inverse")
    col4.metric("긴급건", f"{n_b2b_urg:,} 건", delta=f"{n_b2b_urg - c_b2b_urg:,} 건", delta_color="inverse")
    
    st.divider()
    
    # -----------------------------------------------------------------
    # 차트 및 표 데이터
    # -----------------------------------------------------------------
    c1, c2 = st.columns(2)
    with c1: 
        if not n_b2b.empty:
            st.plotly_chart(px.bar(n_b2b, x='팀', y=['총 출하수량', '추가 수량'], barmode='group', title="팀별 수량 분석"), use_container_width=True)
        else: st.info("B2B 차트 데이터가 없습니다.")
    with c2: 
        if not n_b2c.empty:
            st.plotly_chart(px.pie(n_b2c, names='출고유형', values='출고건수', title="B2C 비율", hole=0.4), use_container_width=True)
        else: st.info("B2C 차트 데이터가 없습니다.")
    
    st.markdown("**[ B2B 팀별 출고 현황 ]**")
    if not n_b2b.empty:
        all_teams = n_b2b['팀'].unique().tolist()
        
        # 🔥 [3중 철벽 방어 패치] 메모리에서 단 하나라도 유실되면 무조건 강제 부활시키는 로직으로 대개조!
        if 'selected_b2b' not in st.session_state or 'excluded_b2b' not in st.session_state or st.session_state.get('last_file') != up.name:
            st.session_state.last_file = up.name
            st.session_state.selected_b2b = all_teams
            st.session_state.excluded_b2b = []
        
        from streamlit_sortables import sort_items
        res = sort_items([
            {'header': '📋 조회할 팀 (좌우 드래그로 순서 정렬)', 'items': st.session_state.selected_b2b},
            {'header': '🗑️ 제외할 팀 (이 상자로 던지면 표에서 제외)', 'items': st.session_state.excluded_b2b}
        ])
        
        st.session_state.selected_b2b = res[0]
        st.session_state.excluded_b2b = res[1]
        selected_teams = res[0]
        
        if selected_teams:
            n_b2b_filtered = n_b2b[n_b2b['팀'].isin(selected_teams)].copy()
            
            n_b2b_filtered['팀'] = pd.Categorical(n_b2b_filtered['팀'], categories=selected_teams, ordered=True)
            n_b2b_filtered = n_b2b_filtered.sort_values('팀').reset_index(drop=True)
            
            f_cnt = n_b2b_filtered['출고건수'].sum()
            f_qty = n_b2b_filtered['총 출하수량'].sum()
            f_urg = n_b2b_filtered['긴급건'].sum()
            f_add = n_b2b_filtered['추가 수량'].sum()
            f_lt = n_b2b_filtered['평균 리드타임'].mean()
            
            b2b_tot = pd.DataFrame([['합계', f_cnt, f_qty, f_urg, f_add, f_lt]], columns=n_b2b.columns)
            n_b2b_display = pd.concat([n_b2b_filtered, b2b_tot], ignore_index=True)
            
            def highlight_total_row(row):
                if row['팀'] == '합계':
                    return ['background-color: #f0f2f6; font-weight: bold'] * len(row)
                return [''] * len(row)
            
            st.dataframe(n_b2b_display.style.apply(highlight_total_row, axis=1).format({
                "출고건수": "{:,}", 
                "총 출하수량": "{:,.0f}", 
                "긴급건": "{:,}", 
                "추가 수량": "{:,.0f}", 
                "평균 리드타임": "{:.2f}"
            }), use_container_width=True, hide_index=True)
        else:
            st.warning("조회할 팀이 없습니다. 아래 '제외할 팀' 상자에서 원하는 팀을 위로 드래그해 올리세요!")
    else:
        st.write("해당 기간의 데이터가 없습니다.")
