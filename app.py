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
st.set_page_config(page_title="가열로 5호기 성과 검증 리포트", layout="wide")

# 목표 원단위 설정 (23% 절감 기준: 25.52 Nm3/ton)
TARGET_UNIT_COST = 25.52

# 한글 폰트 설정 (PDF 및 차트용)
# 실행 폴더에 'NanumGothic.ttf' 파일이 반드시 있어야 합니다.
FONT_FILE = 'NanumGothic.ttf'
HAS_KOREAN_FONT = False

if os.path.exists(FONT_FILE):
    try:
        # 차트용 폰트 설정
        font_prop = fm.FontProperties(fname=FONT_FILE)
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
        HAS_KOREAN_FONT = True
    except:
        pass
else:
    # 폰트가 없으면 기본 영문 폰트 사용 (한글 깨짐 주의)
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. 데이터 처리 함수 (로딩 및 분석)
# ---------------------------------------------------------
@st.cache_data
def load_and_process_data(sensor_files, prod_file):
    # --- A. 생산 실적 로딩 (Excel) ---
    try:
        df_prod = pd.read_excel(prod_file)
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 컬럼 매핑 (첫번째=날짜, 두번째=장입량)
        col_date = df_prod.columns[0]
        col_weight = df_prod.columns[1]
        df_prod.rename(columns={col_date: '일자', col_weight: '장입량'}, inplace=True)
        
        # 날짜/숫자 변환
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        df_prod = df_prod.dropna(subset=['일자'])
        
    except Exception as e:
        return None, f"생산 실적 파일 오류: {e}"

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
        return None, "데이터가 없습니다."
        
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]

    # 컬럼 매핑 (일시, 온도, 가스지침 순서 가정)
    try:
        cols = df_sensor.columns
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except Exception as e:
        return None, f"가열로 데이터 포맷 오류: {e}"

    # --- C. 데이터 매칭 및 분석 ---
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = prod_dates.intersection(sensor_dates)
    
    if len(common_dates) == 0:
        return None, "날짜 매칭 실패: 날짜 형식을 확인해주세요."

    results = []
    for date in common_dates:
        date_ts = pd.to_datetime(date)
        
        # 1. 장입량
        prod_row = df_prod[df_prod['일자'] == date_ts]
        if prod_row.empty: continue
        
        charge_val = prod_row.iloc[0]['장입량']
        if isinstance(charge_val, str):
            charge_val = float(str(charge_val).replace(',', ''))
        charge_kg = float(charge_val)
        
        if charge_kg <= 0: continue

        # 2. 가스 사용량 및 시간 추출
        daily_sensor = df_sensor[df_sensor['일시'].dt.date == date]
        if daily_sensor.empty: continue
        
        # 시작/종료 시간 및 지침 찾기
        start_row = daily_sensor.iloc[0]
        end_row = daily_sensor.iloc[-1]
        
        start_time = start_row['일시']
        end_time = end_row['일시']
        gas_used = end_row['가스지침'] - start_row['가스지침']
        
        if gas_used <= 0: continue

        # 3. 판정
        unit_cost = gas_used / (charge_kg / 1000)
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date.strftime('%Y-%m-%d'),
            '검침시작': start_time.strftime('%Y-%m-%d %H:%M'),
            '검침완료': end_time.strftime('%Y-%m-%d %H:%M'),
            'Cycle종료': end_time.strftime('%Y-%m-%d %H:%M'), # 데이터상 마지막 시간을 종료로 가정
            '가스사용량(Nm3)': int(gas_used),
            '장입량(kg)': int(charge_kg),
            '원단위': round(unit_cost, 2),
            '달성여부': 'Pass' if is_pass else 'Fail'
        })
    
    if not results:
        return None, "분석 가능한 데이터가 없습니다."

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
        # 테이블 헤더 폰트
        header_font = 'Nanum'
    else:
        header_font = 'Arial'

    # --- 1. 소제목 (회차 표시) ---
    pdf.set_font(header_font, '', 12)
    pdf.cell(0, 10, f"3.5 가열로 5호기 - {row_data['날짜']} (23% 절감 검증)", 0, 1, 'L')
    pdf.ln(2)

    # --- 2. 데이터 테이블 (요청하신 양식) ---
    # 헤더
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font(header_font, '', 10)
    
    headers = ["검침 시작", "검침 완료", "③ 가스 사용량\n(②-①=③)", "Cycle 종료", "장입량"]
    widths = [38, 38, 38, 38, 38] # 전체 너비 약 190
    
    # 헤더 출력
    x_start = pdf.get_x()
    y_start = pdf.get_y()
    
    max_h = 12 # 헤더 높이
    
    for i, h in enumerate(headers):
        x = x_start + sum(widths[:i])
        pdf.set_xy(x, y_start)
        pdf.multi_cell(widths[i], 6, h, border=1, align='C', fill=True)
        
    pdf.set_xy(x_start, y_start + max_h)
    
    # 데이터 출력
    pdf.set_font(header_font, '', 10)
    
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
    
    # --- 3. 차트 삽입 ---
    pdf.set_font(header_font, '', 12)
    pdf.cell(0, 10, "▶ 열처리 Chart (온도/가스 트렌드)", 0, 1, 'L')
    pdf.image(chart_path, x=10, w=190)
    
    # --- 4. 하단 요약 ---
    pdf.ln(5)
    pdf.set_font(header_font, '', 10)
    pdf.cell(0, 8, f"* 원단위 실적: {row_data['원단위']} Nm3/ton (목표 25.52 이하 달성)", 0, 1, 'R')

    return pdf

# ---------------------------------------------------------
# 4. 메인 화면
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 23% 절감 검증 리포트 생성기")
    
    # 사이드바
    with st.sidebar:
        st.header("데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        run_btn = st.button("분석 실행")

    if run_btn and prod_file and sensor_files:
        with st.spinner("데이터 분석 및 23% 달성 구간 탐색 중..."):
            df_result, df_raw = load_and_process_data(sensor_files, prod_file)
            
            if df_result is not None:
                st.session_state['res'] = df_result
                st.session_state['raw'] = df_raw
                st.success("분석 완료")
            else:
                st.error(f"오류: {df_raw}")

    if 'res' in st.session_state:
        df = st.session_state['res']
        
        # Pass 데이터만 필터링
        df_pass = df[df['달성여부'] == 'Pass']
        
        st.subheader("1. 23% 절감 달성 리스트 (Golden Cycle)")
        if df_pass.empty:
            st.warning("목표(25.52 Nm3/ton)를 달성한 날짜가 없습니다.")
        else:
            st.dataframe(df_pass)
            
            st.subheader("2. 리포트 생성")
            target_date = st.selectbox("리포트를 출력할 날짜를 선택하세요:", df_pass['날짜'].unique())
            
            if st.button("📄 PDF 리포트 생성"):
                # 데이터 추출
                row = df_pass[df_pass['날짜'] == target_date].iloc[0]
                daily_raw = st.session_state['raw']
                daily_raw = daily_raw[daily_raw['일시'].dt.strftime('%Y-%m-%d') == target_date]
                
                # 차트 그리기
                fig, ax1 = plt.subplots(figsize=(12, 5))
                
                # 온도 (영역 채우기 - 첨부파일 스타일)
                ax1.fill_between(daily_raw['일시'], daily_raw['온도'], color='red', alpha=0.3)
                ax1.plot(daily_raw['일시'], daily_raw['온도'], color='red', label='Temp(C)')
                ax1.set_ylabel('Temp (C)', color='red')
                ax1.tick_params(axis='y', labelcolor='red')
                ax1.grid(True, linestyle='--', alpha=0.5)
                
                # 가스 (꺾은선)
                ax2 = ax1.twinx()
                ax2.plot(daily_raw['일시'], daily_raw['가스지침'], color='blue', linewidth=2, label='Gas(m3)')
                ax2.set_ylabel('Gas Cumulative (m3)', color='blue')
                ax2.tick_params(axis='y', labelcolor='blue')
                
                plt.title(f"Furnace No.5 Cycle Trend - {target_date}")
                
                # 이미지 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
                    fig.savefig(tmp_img.name, bbox_inches='tight')
                    img_path = tmp_img.name
                
                # PDF 생성
                pdf = generate_custom_pdf(row, img_path)
                pdf_bytes = pdf.output(dest='S').encode('latin-1')
                
                st.download_button(
                    label="📥 리포트 다운로드 (제출용)",
                    data=pdf_bytes,
                    file_name=f"Furnace5_Verification_{target_date}.pdf",
                    mime="application/pdf"
                )
                os.remove(img_path)

if __name__ == "__main__":
    main()
