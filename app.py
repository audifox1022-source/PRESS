import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from fpdf import FPDF
import tempfile
import os
from datetime import timedelta

# ---------------------------------------------------------
# 1. 앱 설정 및 폰트
# ---------------------------------------------------------
st.set_page_config(page_title="가열로 5호기 정밀 분석", layout="wide")

# 폰트 설정
FONT_FILE = 'NanumGothic.ttf'
HAS_KOREAN_FONT = False
if os.path.exists(FONT_FILE):
    try:
        font_prop = fm.FontProperties(fname=FONT_FILE)
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
        HAS_KOREAN_FONT = True
    except: pass
else:
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. 로직: 헤더 찾기 & 데이터 로딩
# ---------------------------------------------------------
def smart_read_file(uploaded_file, header_row=0, nrows=None):
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file, header=header_row, nrows=nrows)
        else:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding='cp949', header=header_row, nrows=nrows)
            except:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding='utf-8', header=header_row, nrows=nrows)
    except: return None

# ---------------------------------------------------------
# 3. 핵심 로직: 사이클 감지 및 분석
# ---------------------------------------------------------
def analyze_cycle(daily_data):
    """
    조건:
    1. 시작: 600도 이하
    2. 홀딩: 1230~1270도 구간이 10시간 이상 지속
    3. 종료: 홀딩 이후 900도 이하로 떨어지는 시점
    """
    # 1. 시작점 찾기 (600도 이하 첫 지점)
    start_candidates = daily_data[daily_data['온도'] <= 600]
    if start_candidates.empty:
        return None, "시작 온도(600도 이하) 없음"
    start_row = start_candidates.iloc[0]
    start_time = start_row['일시']

    # 2. 홀딩 구간 찾기 (1230 <= Temp <= 1270)
    # 시작 시간 이후의 데이터만 분석
    post_start_data = daily_data[daily_data['일시'] > start_time].copy()
    
    # 홀딩 조건 마킹
    post_start_data['is_holding'] = (post_start_data['온도'] >= 1230) & (post_start_data['온도'] <= 1270)
    
    # 연속된 홀딩 구간 그룹화
    # (True/False가 바뀌는 지점마다 그룹 ID 부여)
    post_start_data['group'] = (post_start_data['is_holding'] != post_start_data['is_holding'].shift()).cumsum()
    
    holding_end_time = None
    
    # 각 그룹별 지속시간 체크
    for _, group in post_start_data[post_start_data['is_holding']].groupby('group'):
        duration = group['일시'].max() - group['일시'].min()
        if duration >= timedelta(hours=10):
            holding_end_time = group['일시'].max()
            break # 첫 번째 유효 홀딩 구간을 찾으면 중단
            
    if holding_end_time is None:
        return None, "유효 홀딩 구간(10시간 이상) 없음"

    # 3. 종료점 찾기 (홀딩 종료 후 900도 이하)
    post_holding_data = daily_data[daily_data['일시'] > holding_end_time]
    end_candidates = post_holding_data[post_holding_data['온도'] <= 900]
    
    if end_candidates.empty:
        return None, "종료 온도(900도 이하) 도달 안 함"
        
    end_row = end_candidates.iloc[0]
    
    return {
        'start_row': start_row,
        'end_row': end_row,
        'holding_end': holding_end_time
    }, "성공"

def process_data(sensor_files, df_prod, col_p_date, col_p_weight, 
                s_header_row, col_s_time, col_s_temp, col_s_gas, target_cost):
    
    # --- 데이터 전처리 ---
    try:
        df_prod = df_prod.rename(columns={col_p_date: '일자', col_p_weight: '장입량'})
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        df_prod = df_prod.dropna(subset=['일자', '장입량'])
    except Exception as e: return None, f"생산실적 오류: {e}"

    df_list = []
    for f in sensor_files:
        f.seek(0)
        df = smart_read_file(f, s_header_row)
        if df is not None: df_list.append(df)
    
    if not df_list: return None, "센서 데이터 없음"
    
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]
    
    try:
        df_sensor = df_sensor.rename(columns={col_s_time: '일시', col_s_temp: '온도', col_s_gas: '가스지침'})
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        df_sensor = df_sensor.dropna(subset=['일시']).sort_values('일시')
    except Exception as e: return None, f"센서 데이터 매핑 오류: {e}"

    # --- 분석 실행 ---
    prod_dates = set(df_prod['일자'].dt.date)
    # 센서 데이터 날짜 범위 확인 (매칭을 위해 앞뒤 하루 여유 고려 가능하지만 일단 정확한 일자 매칭 시도)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = sorted(list(prod_dates.intersection(sensor_dates)))
    
    if not common_dates: return None, "날짜 매칭 실패"

    results = []
    
    for date in common_dates:
        prod_row = df_prod[df_prod['일자'] == pd.to_datetime(date)]
        # 해당 날짜 + 다음날 오전까지 데이터 확보 (사이클이 넘어갈 수 있으므로 48시간 윈도우)
        target_date = pd.to_datetime(date)
        next_date = target_date + timedelta(days=1)
        
        daily_window = df_sensor[
            (df_sensor['일시'] >= target_date) & 
            (df_sensor['일시'] < target_date + timedelta(days=2))
        ]
        
        if daily_window.empty: continue
        
        # 사이클 분석 수행
        cycle_info, msg = analyze_cycle(daily_window)
        
        if cycle_info:
            start = cycle_info['start_row']
            end = cycle_info['end_row']
            
            charge_kg = prod_row.iloc[0]['장입량']
            if charge_kg <= 0: continue
            
            gas_used = end['가스지침'] - start['가스지침']
            if gas_used <= 0: continue
            
            unit = gas_used / (charge_kg / 1000)
            is_pass = unit <= target_cost
            
            results.append({
                '날짜': date.strftime('%Y-%m-%d'),
                '검침시작': start['일시'].strftime('%Y-%m-%d %H:%M'),
                '시작지침': start['가스지침'],
                '검침완료': end['일시'].strftime('%Y-%m-%d %H:%M'),
                '종료지침': end['가스지침'],
                '가스사용량(Nm3)': int(gas_used),
                '장입량(kg)': int(charge_kg),
                '원단위': round(unit, 2),
                '달성여부': 'Pass' if is_pass else 'Fail',
                '비고': f"홀딩종료: {cycle_info['holding_end'].strftime('%H:%M')}"
            })
            
    return pd.DataFrame(results), df_sensor

# ---------------------------------------------------------
# 4. PDF 생성
# ---------------------------------------------------------
class PDFReport(FPDF):
    def header(self):
        font = 'Nanum' if HAS_KOREAN_FONT else 'Arial'
        if HAS_KOREAN_FONT: self.add_font('Nanum', '', FONT_FILE, uni=True)
        self.set_font(font, 'B' if not HAS_KOREAN_FONT else '', 14)
        self.cell(0, 10, '3. 가열로 5호기 검증 DATA (개선 후)', 0, 1, 'L')
        self.ln(5)

def generate_pdf(row_data, chart_path, target):
    pdf = PDFReport()
    pdf.add_page()
    font = 'Nanum' if HAS_KOREAN_FONT else 'Arial'
    
    pdf.set_font(font, '', 12)
    pdf.cell(0, 10, f"3.5 가열로 5호기 - {row_data['날짜']} (23% 절감 검증)", 0, 1, 'L')
    pdf.ln(5)

    pdf.set_fill_color(240, 240, 240)
    pdf.set_font(font, '', 10)
    headers = ["검침 시작", "검침 완료", "③ 가스 사용량\n(②-①=③)", "Cycle 종료", "장입량"]
    widths = [38, 38, 38, 38, 38]
    
    x = pdf.get_x(); y = pdf.get_y()
    for i, h in enumerate(headers):
        pdf.set_xy(x + sum(widths[:i]), y)
        pdf.multi_cell(widths[i], 6, h, border=1, align='C', fill=True)
    
    pdf.set_xy(x, y + 12)
    
    s_txt = f"{row_data['검침시작']}\n({row_data['시작지침']:,.0f})"
    e_txt = f"{row_data['검침완료']}\n({row_data['종료지침']:,.0f})"
    
    vals = [s_txt, e_txt, f"{row_data['가스사용량(Nm3)']:,} Nm3", str(row_data['검침완료']), f"{row_data['장입량(kg)']:,} kg"]
    
    for i, v in enumerate(vals):
        cx = x + sum(widths[:i])
        pdf.set_xy(cx, y + 12)
        pdf.multi_cell(widths[i], 6, v, border=1, align='C')
        
    pdf.ln(5)
    pdf.set_y(y + 12 + 15)
    
    pdf.set_font(font, '', 12)
    pdf.cell(0, 10, "▶ 열처리 Chart (온도/가스 트렌드)", 0, 1, 'L')
    pdf.image(chart_path, x=10, w=190)
    
    pdf.ln(5)
    pdf.set_font(font, '', 10)
    pdf.cell(0, 8, f"* 실적 원단위: {row_data['원단위']} Nm3/ton (목표 {target} 이하 달성)", 0, 1, 'R')
    
    return pdf

# ---------------------------------------------------------
# 5. 메인 UI
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 정밀 검증 시스템")
    
    with st.sidebar:
        st.header("1. 데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        
        st.divider()
        st.header("2. 분석 기준 설정")
        target_cost = st.number_input("목표 원단위 (Nm3/ton)", value=25.53, step=0.1, format="%.2f")
        st.info(f"기준: 10hr Holding (1250±20℃), 종료 < 900℃")
        
        st.divider()
        st.header("3. 엑셀/CSV 설정")
        p_header = st.number_input("생산실적 제목행", 0, 10, 0)
        s_header = st.number_input("가열로 데이터 제목행", 0, 20, 0)
        
        run_btn = st.button("🚀 분석 실행", type="primary")

    if prod_file and sensor_files:
        st.subheader("🛠️ 데이터 컬럼 지정")
        c1, c2 = st.columns(2)
        
        with c1:
            try:
                df_p = smart_read_file(prod_file, p_header, 3)
                st.dataframe(df_p)
                col_p_date = st.selectbox("📅 날짜", df_p.columns, index=0)
                col_p_weight = st.selectbox("⚖️ 장입량", df_p.columns, index=1 if len(df_p.columns)>1 else 0)
            except: st.error("생산실적 읽기 실패")

        with c2:
            try:
                f = sensor_files[0]; f.seek(0)
                df_s = smart_read_file(f, s_header, 3)
                st.dataframe(df_s)
                col_s_time = st.selectbox("⏰ 일시", df_s.columns, index=0)
                col_s_temp = st.selectbox("🔥 온도", df_s.columns, index=1 if len(df_s.columns)>1 else 0)
                col_s_gas = st.selectbox("⛽ 가스지침", df_s.columns, index=2 if len(df_s.columns)>2 else 0)
            except: st.error("가열로 데이터 읽기 실패")

        if run_btn:
            with st.spinner("정밀 분석 중... (홀딩 구간 탐색)"):
                # 파일 다시 읽기 (전체)
                f_prod = smart_read_file(prod_file, p_header)
                
                res, raw = process_data(sensor_files, f_prod, 
                                      col_p_date, col_p_weight, 
                                      s_header, col_s_time, col_s_temp, col_s_gas,
                                      target_cost)
                
                if res is not None:
                    st.session_state['res'] = res
                    st.session_state['raw'] = raw
                    st.success(f"분석 완료! 유효 사이클 {len(res)}건 발견.")
                else:
                    st.error("분석 실패 (조건에 맞는 데이터 없음)")

    if 'res' in st.session_state:
        df = st.session_state['res']
        st.divider()
        t1, t2 = st.tabs(["📊 분석 결과", "📑 리포트"])
        
        with t1:
            st.dataframe(df.style.applymap(lambda x: 'background-color:#d4edda' if x=='Pass' else 'background-color:#f8d7da', subset=['달성여부']), use_container_width=True)
            
        with t2:
            df_pass = df[df['달성여부'] == 'Pass']
            if df_pass.empty:
                st.warning("목표 달성 데이터 없음")
            else:
                s_date = st.selectbox("데이터 선택:", df_pass['날짜'].unique())
                if st.button("PDF 생성"):
                    row = df_pass[df_pass['날짜'] == s_date].iloc[0]
                    
                    # 차트 데이터 (시작~종료 구간)
                    full_raw = st.session_state['raw']
                    s_ts = pd.to_datetime(row['검침시작'])
                    e_ts = pd.to_datetime(row['검침완료'])
                    # 앞뒤로 1시간 여유 두기
                    chart_data = full_raw[(full_raw['일시'] >= s_ts - timedelta(hours=1)) & (full_raw['일시'] <= e_ts + timedelta(hours=1))]
                    
                    fig, ax1 = plt.subplots(figsize=(12, 5))
                    ax1.fill_between(chart_data['일시'], chart_data['온도'], color='red', alpha=0.3)
                    ax1.plot(chart_data['일시'], chart_data['온도'], 'r-', label='Temp')
                    ax1.set_ylabel('Temp', color='r')
                    # 홀딩 구간 표시선
                    ax1.axhline(y=1230, color='gray', linestyle=':', alpha=0.5)
                    ax1.axhline(y=1270, color='gray', linestyle=':', alpha=0.5)
                    
                    ax2 = ax1.twinx()
                    ax2.plot(chart_data['일시'], chart_data['가스지침'], 'b-', label='Gas')
                    ax2.set_ylabel('Gas', color='b')
                    
                    # 시작/종료 포인트 마커
                    ax1.scatter([s_ts, e_ts], [chart_data.loc[chart_data['일시']>=s_ts, '온도'].iloc[0], chart_data.loc[chart_data['일시']<=e_ts, '온도'].iloc[-1]], color='green', s=100, zorder=5)
                    
                    plt.title(f"Cycle: {row['검침시작']} ~ {row['검침완료']}")
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                        fig.savefig(tmp.name, bbox_inches='tight')
                        img_path = tmp.name
                    
                    pdf = generate_pdf(row, img_path, target_cost)
                    pdf_bytes = pdf.output(dest='S').encode('latin-1')
                    st.download_button("📥 다운로드", pdf_bytes, f"Report_{s_date}.pdf", "application/pdf")
                    os.remove(img_path)

if __name__ == "__main__":
    main()
