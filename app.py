import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from fpdf import FPDF
import tempfile
import os

# ---------------------------------------------------------
# 1. 기본 설정 및 폰트 로딩
# ---------------------------------------------------------
st.set_page_config(page_title="가열로 5호기 성과 검증", layout="wide")

TARGET_UNIT_COST = 25.52  # 목표 원단위

# 폰트 설정 (나눔고딕)
FONT_FILE = 'NanumGothic.ttf'
HAS_KOREAN_FONT = False

if os.path.exists(FONT_FILE):
    try:
        font_prop = fm.FontProperties(fname=FONT_FILE)
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
        HAS_KOREAN_FONT = True
    except:
        pass
else:
    # 폰트가 없으면 기본 영문 폰트 사용 (에러 방지)
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. 데이터 처리 함수 (진단 모드 탑재)
# ---------------------------------------------------------
@st.cache_data
def load_and_process_data(sensor_files, prod_file):
    debug_logs = [] # 진단 로그 저장용

    # --- A. 생산 실적 로딩 (Excel) ---
    try:
        df_prod = pd.read_excel(prod_file)
        # 컬럼명 공백 제거
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 첫 번째=날짜, 두 번째=장입량으로 강제 지정
        col_date = df_prod.columns[0]
        col_weight = df_prod.columns[1]
        df_prod.rename(columns={col_date: '일자', col_weight: '장입량'}, inplace=True)
        
        # 날짜 및 숫자 강제 변환
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        
        # 콤마(,) 제거 후 숫자로 변환
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        
        df_prod = df_prod.dropna(subset=['일자'])
        
    except Exception as e:
        return None, f"생산 실적 파일 로딩 오류: {e}"

    # --- B. 가열로 데이터 로딩 (CSV/Excel) ---
    df_list = []
    for f in sensor_files:
        try:
            if f.name.endswith('.xlsx') or f.name.endswith('.xls'):
                temp = pd.read_excel(f)
            else:
                try:
                    temp = pd.read_csv(f, encoding='cp949')
                except:
                    temp = pd.read_csv(f, encoding='utf-8')
            df_list.append(temp)
        except Exception as e:
            return None, f"파일 로딩 오류 ({f.name}): {e}"
    
    if not df_list:
        return None, "가열로 데이터가 비어있습니다."
        
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]

    # 컬럼 매핑 (일시, 온도, 가스지침)
    try:
        cols = df_sensor.columns
        # 첫번째=일시, 두번째=온도, 세번째=가스지침 가정
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        
        # 데이터 강제 형변환
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
        
    except Exception as e:
        return None, f"가열로 데이터 포맷 처리 오류: {e}"

    # --- C. 데이터 매칭 및 분석 ---
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = sorted(list(prod_dates.intersection(sensor_dates)))
    
    if len(common_dates) == 0:
        return None, f"날짜 매칭 실패. (생산실적: {len(prod_dates)}일, 센서: {len(sensor_dates)}일, 일치: 0일)"

    results = []
    
    # 디버깅: 분석 시작 메시지
    st.write(f"🔍 **총 {len(common_dates)}일의 데이터가 날짜 매칭됨. 상세 분석 시작...**")
    
    for date in common_dates:
        date_str = date.strftime('%Y-%m-%d')
        date_ts = pd.to_datetime(date)
        
        # 1. 장입량 확인
        prod_row = df_prod[df_prod['일자'] == date_ts]
        if prod_row.empty: 
            debug_logs.append(f"❌ {date_str}: 생산 실적 행 없음")
            continue
            
        charge_kg = prod_row.iloc[0]['장입량']
        
        # NaN 체크
        if pd.isna(charge_kg) or charge_kg <= 0:
            debug_logs.append(f"❌ {date_str}: 장입량 데이터 오류 (0 또는 비어있음)")
            continue

        # 2. 가스 사용량 확인
        daily_sensor = df_sensor[df_sensor['일시'].dt.date == date]
        if daily_sensor.empty: 
            debug_logs.append(f"❌ {date_str}: 해당 날짜 센서 데이터 없음")
            continue
            
        # 결측치 제거 후 계산
        daily_sensor = daily_sensor.dropna(subset=['가스지침'])
        if daily_sensor.empty:
            debug_logs.append(f"❌ {date_str}: 가스 지침 데이터가 모두 비어있음")
            continue

        gas_start = daily_sensor['가스지침'].min()
        gas_end = daily_sensor['가스지침'].max()
        gas_used = gas_end - gas_start
        
        if gas_used <= 0:
            debug_logs.append(f"❌ {date_str}: 가스 사용량 0 (시작:{gas_start} ~ 종료:{gas_end})")
            continue

        # 3. 사이클 시간 (처음과 끝)
        start_time = daily_sensor.iloc[0]['일시']
        end_time = daily_sensor.iloc[-1]['일시']

        # 4. 원단위 및 판정
        unit_cost = gas_used / (charge_kg / 1000)
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date_str,
            '검침시작': start_time.strftime('%Y-%m-%d %H:%M'),
            '검침완료': end_time.strftime('%Y-%m-%d %H:%M'),
            'Cycle종료': end_time.strftime('%Y-%m-%d %H:%M'),
            '가스사용량(Nm3)': int(gas_used),
            '장입량(kg)': int(charge_kg),
            '원단위': round(unit_cost, 2),
            '달성여부': 'Pass' if is_pass else 'Fail'
        })
    
    if not results:
        # 분석 실패 시 로그 화면에 출력
        st.error("🚨 **분석 실패 원인 리포트 (상위 5개)**")
        for log in debug_logs[:5]:
            st.write(log)
        if len(debug_logs) > 5:
            st.write(f"... 외 {len(debug_logs)-5}건")
            
        return None, "유효한 분석 데이터가 없습니다. 위 에러 로그를 확인하세요."

    return pd.DataFrame(results), df_sensor

# ---------------------------------------------------------
# 3. PDF 리포트 생성 (양식 맞춤형)
# ---------------------------------------------------------
class PDFReport(FPDF):
    def header(self):
        if HAS_KOREAN_FONT:
            self.add_font('Nanum', '', FONT_FILE, uni=True)
            self.set_font('Nanum', '', 14)
        else:
            self.set_font('Arial', 'B', 14)
        self.cell(0, 10, '3. 가열로 5호기 검증 DATA (개선 후)', 0, 1, 'L')
        self.ln(5)

def generate_custom_pdf(row_data, chart_path):
    pdf = PDFReport()
    pdf.add_page()
    
    if HAS_KOREAN_FONT:
        pdf.add_font('Nanum', '', FONT_FILE, uni=True)
        header_font = 'Nanum'
    else:
        header_font = 'Arial'

    # 소제목
    pdf.set_font(header_font, '', 12)
    pdf.cell(0, 10, f"3.5 가열로 5호기 - {row_data['날짜']} (23% 절감 검증)", 0, 1, 'L')
    pdf.ln(2)

    # 데이터 테이블
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font(header_font, '', 10)
    
    headers = ["검침 시작", "검침 완료", "③ 가스 사용량\n(②-①=③)", "Cycle 종료", "장입량"]
    widths = [38, 38, 38, 38, 38]
    
    x_start = pdf.get_x()
    y_start = pdf.get_y()
    max_h = 12
    
    for i, h in enumerate(headers):
        x = x_start + sum(widths[:i])
        pdf.set_xy(x, y_start)
        pdf.multi_cell(widths[i], 6, h, border=1, align='C', fill=True)
        
    pdf.set_xy(x_start, y_start + max_h)
    
    data_row = [
        str(row_data['검침시작']),
        str(row_data['검침완료']),
        f"{row_data['가스사용량(Nm3)']} Nm3",
        str(row_data['Cycle종료']),
        f"{row_data['장입량(kg)']} kg"
    ]
    
    for i, d in enumerate(data_row):
        pdf.cell(widths[i], 10, d, border=1, align='C')
        
    pdf.ln(15)
    
    # 차트 삽입
    pdf.set_font(header_font, '', 12)
    pdf.cell(0, 10, "▶ 열처리 Chart (온도/가스 트렌드)", 0, 1, 'L')
    pdf.image(chart_path, x=10, w=190)
    
    # 하단 요약
    pdf.ln(5)
    pdf.set_font(header_font, '', 10)
    pdf.cell(0, 8, f"* 원단위 실적: {row_data['원단위']} Nm3/ton (목표 25.52 이하 달성)", 0, 1, 'R')

    return pdf

# ---------------------------------------------------------
# 4. 메인 화면 (UI)
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템")
    
    # 사이드바
    with st.sidebar:
        st.header("1. 데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        run_btn = st.button("분석 실행")

    # 실행 로직
    if run_btn and prod_file and sensor_files:
        with st.spinner("데이터 분석 중... (잠시만 기다려주세요)"):
            df_result, df_raw = load_and_process_data(sensor_files, prod_file)
            
            if df_result is not None:
                st.session_state['data_result'] = df_result
                st.session_state['data_raw'] = df_raw
                st.success("분석 완료!")
            else:
                st.error(f"분석 실패: {df_raw}")

    # 결과 화면
    if 'data_result' in st.session_state:
        df_res = st.session_state['data_result']
        df_raw = st.session_state['data_raw']
        
        tab1, tab2 = st.tabs(["📊 분석 결과", "📑 리포트 출력"])
        
        with tab1:
            st.subheader("일별 성과 리스트")
            st.dataframe(df_res.style.applymap(
                lambda x: 'background-color: #d4edda' if x == 'Pass' else 'background-color: #f8d7da',
                subset=['달성여부']
            ), use_container_width=True)
            
        with tab2:
            st.subheader("PDF 리포트 생성")
            # Pass 데이터만 선택 가능
            pass_data = df_res[df_res['달성여부'] == 'Pass']
            
            if pass_data.empty:
                st.warning("목표(23%)를 달성한 날짜가 없어 리포트를 생성할 수 없습니다.")
            else:
                date_list = pass_data['날짜'].unique()
                selected_date = st.selectbox("날짜 선택:", date_list)
                
                if st.button("리포트 생성 및 다운로드"):
                    # 데이터 준비
                    row = pass_data[pass_data['날짜'] == selected_date].iloc[0]
                    daily_raw = df_raw[df_raw['일시'].dt.strftime('%Y-%m-%d') == selected_date]
                    
                    # 차트 생성 (첨부파일 스타일)
                    fig, ax1 = plt.subplots(figsize=(12, 5))
                    
                    # 온도: 빨간색 채우기
                    ax1.fill_between(daily_raw['일시'], daily_raw['온도'], color='red', alpha=0.3)
                    ax1.plot(daily_raw['일시'], daily_raw['온도'], color='red', label='Temp(C)')
                    ax1.set_ylabel('Temp (C)', color='red')
                    ax1.tick_params(axis='y', labelcolor='red')
                    ax1.grid(True, linestyle='--', alpha=0.5)
                    
                    # 가스: 파란색 실선
                    ax2 = ax1.twinx()
                    ax2.plot(daily_raw['일시'], daily_raw['가스지침'], color='blue', linewidth=2, label='Gas(m3)')
                    ax2.set_ylabel('Gas Cumulative (m3)', color='blue')
                    ax2.tick_params(axis='y', labelcolor='blue')
                    
                    plt.title(f"Cycle Trend ({selected_date})")
                    
                    # 이미지 저장
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
                        fig.savefig(tmp_img.name, bbox_inches='tight')
                        img_path = tmp_img.name
                        
                    # PDF 생성
                    pdf = generate_custom_pdf(row, img_path)
                    pdf_bytes = pdf.output(dest='S').encode('latin-1')
                    
                    st.download_button(
                        label="📄 PDF 다운로드",
                        data=pdf_bytes,
                        file_name=f"Report_{selected_date}.pdf",
                        mime="application/pdf"
                    )
                    os.remove(img_path)

if __name__ == "__main__":
    main()
