import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from fpdf import FPDF
import tempfile
import os

# ---------------------------------------------------------
# 1. 앱 설정 및 폰트 로딩
# ---------------------------------------------------------
st.set_page_config(page_title="가열로 5호기 성과 검증", layout="wide")

TARGET_UNIT_COST = 25.52

# 폰트 설정
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
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. 스마트 데이터 로더 (미리보기용)
# ---------------------------------------------------------
def get_preview_data(uploaded_file, header_row=0):
    """파일을 읽어서 앞부분만 보여주는 함수"""
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file, header=header_row, nrows=5)
        else:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding='cp949', header=header_row, nrows=5)
            except:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding='utf-8', header=header_row, nrows=5)
    except Exception as e:
        return None

# ---------------------------------------------------------
# 3. 데이터 처리 및 분석 로직 (전체 로딩)
# ---------------------------------------------------------
def process_data(sensor_files, prod_file, header_idx):
    # --- A. 생산 실적 로딩 ---
    try:
        df_prod = pd.read_excel(prod_file, header=header_idx)
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 컬럼이 최소 2개 이상이어야 함
        if len(df_prod.columns) < 2:
            return None, "생산실적 파일의 컬럼이 2개 미만입니다."

        # 첫 번째=날짜, 두 번째=장입량 매핑
        col_date = df_prod.columns[0]
        col_weight = df_prod.columns[1]
        df_prod.rename(columns={col_date: '일자', col_weight: '장입량'}, inplace=True)
        
        # 전처리
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        
        # 장입량 숫자 변환
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        
        df_prod = df_prod.dropna(subset=['일자', '장입량'])
        
    except Exception as e:
        return None, f"생산 실적 로딩 오류: {e}"

    # --- B. 가열로 데이터 로딩 ---
    df_list = []
    for f in sensor_files:
        try:
            if f.name.endswith('.xlsx') or f.name.endswith('.xls'):
                temp = pd.read_excel(f, header=header_idx)
            else:
                try:
                    f.seek(0)
                    temp = pd.read_csv(f, encoding='cp949', header=header_idx)
                except:
                    f.seek(0)
                    temp = pd.read_csv(f, encoding='utf-8', header=header_idx)
            df_list.append(temp)
        except Exception as e:
            return None, f"파일 로딩 오류 ({f.name}): {e}"
    
    if not df_list:
        return None, "가열로 데이터가 없습니다."
        
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]

    # 컬럼 매핑 (0:일시, 1:온도, 2:가스)
    if len(df_sensor.columns) < 3:
        return None, "가열로 데이터 컬럼이 3개 미만입니다."

    try:
        cols = df_sensor.columns
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except Exception as e:
        return None, f"가열로 데이터 포맷 오류: {e}"

    # --- C. 날짜 매칭 ---
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = sorted(list(prod_dates.intersection(sensor_dates)))
    
    if not common_dates:
        return None, f"날짜 매칭 실패. (생산실적: {len(prod_dates)}일, 센서: {len(sensor_dates)}일 중 일치하는 날짜 없음)"

    # --- D. 성과 분석 ---
    results = []
    for date in common_dates:
        prod_row = df_prod[df_prod['일자'] == pd.to_datetime(date)]
        daily_sensor = df_sensor[df_sensor['일시'].dt.date == date]
        
        if prod_row.empty or daily_sensor.empty: continue
        
        charge_kg = prod_row.iloc[0]['장입량']
        if charge_kg <= 0: continue
        
        gas_start = daily_sensor['가스지침'].min()
        gas_end = daily_sensor['가스지침'].max()
        gas_used = gas_end - gas_start
        
        if gas_used <= 0: continue
        
        unit_cost = gas_used / (charge_kg / 1000)
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date.strftime('%Y-%m-%d'),
            '검침시작': daily_sensor.iloc[0]['일시'].strftime('%Y-%m-%d %H:%M'),
            '검침완료': daily_sensor.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            'Cycle종료': daily_sensor.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            '가스사용량(Nm3)': int(gas_used),
            '장입량(kg)': int(charge_kg),
            '원단위': round(unit_cost, 2),
            '달성여부': 'Pass' if is_pass else 'Fail'
        })
        
    return pd.DataFrame(results), df_sensor

# ---------------------------------------------------------
# 4. PDF 생성
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

def generate_pdf(row_data, chart_path):
    pdf = PDFReport()
    pdf.add_page()
    font_name = 'Nanum' if HAS_KOREAN_FONT else 'Arial'
    
    pdf.set_font(font_name, '', 12)
    pdf.cell(0, 10, f"3.5 가열로 5호기 - {row_data['날짜']} (23% 절감 검증)", 0, 1, 'L')
    pdf.ln(5)

    pdf.set_fill_color(240, 240, 240)
    pdf.set_font(font_name, '', 10)
    
    headers = ["검침 시작", "검침 완료", "③ 가스 사용량\n(②-①=③)", "Cycle 종료", "장입량"]
    widths = [38, 38, 38, 38, 38]
    
    x_start = pdf.get_x()
    y_start = pdf.get_y()
    
    for i, h in enumerate(headers):
        x = x_start + sum(widths[:i])
        pdf.set_xy(x, y_start)
        pdf.multi_cell(widths[i], 6, h, border=1, align='C', fill=True)
    
    pdf.set_xy(x_start, y_start + 12)
    vals = [
        str(row_data['검침시작']),
        str(row_data['검침완료']),
        f"{row_data['가스사용량(Nm3)']:,} Nm3",
        str(row_data['Cycle종료']),
        f"{row_data['장입량(kg)']:,} kg"
    ]
    
    for i, v in enumerate(vals):
        pdf.cell(widths[i], 10, v, border=1, align='C')
    
    pdf.ln(15)
    pdf.set_font(font_name, '', 12)
    pdf.cell(0, 10, "▶ 열처리 Chart (온도/가스 트렌드)", 0, 1, 'L')
    pdf.image(chart_path, x=10, w=190)
    
    pdf.ln(5)
    pdf.set_font(font_name, '', 10)
    pdf.cell(0, 8, f"* 실적 원단위: {row_data['원단위']} Nm3/ton (목표 25.52 이하 달성)", 0, 1, 'R')
    
    return pdf

# ---------------------------------------------------------
# 5. 메인 UI
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템")
    
    with st.sidebar:
        st.header("1. 데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        
        st.markdown("---")
        st.header("2. 데이터 조정 (중요)")
        st.info("👇 **미리보기를 보며 제목 행 숫자를 조절하세요!**")
        header_row_idx = st.number_input("헤더(제목) 행 위치", min_value=0, max_value=20, value=0)
        
        run_btn = st.button("분석 실행", type="primary")

    # --- 실시간 미리보기 (분석 전 확인용) ---
    if prod_file and sensor_files:
        st.subheader("👀 데이터 미리보기 (제목 행 위치를 맞춰주세요)")
        c1, c2 = st.columns(2)
        
        # 생산실적 미리보기
        prev_prod = get_preview_data(prod_file, header_row_idx)
        if prev_prod is not None:
            c1.markdown(f"**📄 생산실적 (헤더: {header_row_idx}번 행)**")
            c1.dataframe(prev_prod)
        else:
            c1.error("생산실적 파일 읽기 실패")
            
        # 가열로 데이터 미리보기 (첫 파일만)
        prev_sensor = get_preview_data(sensor_files[0], header_row_idx)
        if prev_sensor is not None:
            c2.markdown(f"**🌡️ 가열로 데이터 (헤더: {header_row_idx}번 행)**")
            c2.dataframe(prev_sensor)
        else:
            c2.error("가열로 데이터 파일 읽기 실패")
            
        st.info("👆 위 표의 **첫 번째 줄(굵은 글씨)**이 올바른 항목명(일자, 장입량 / 일시, 온도, 가스)이어야 합니다. 아니면 왼쪽 숫자를 조절하세요.")

    # 분석 실행
    if run_btn:
        if not prod_file or not sensor_files:
            st.error("파일을 모두 업로드해주세요.")
        else:
            with st.spinner("데이터 분석 중..."):
                res, raw = process_data(sensor_files, prod_file, header_row_idx)
                
                if res is not None:
                    st.session_state['result'] = res
                    st.session_state['raw'] = raw
                    st.success(f"분석 완료! 총 {len(res)}일 데이터가 매칭되었습니다.")
                else:
                    st.error(f"분석 실패: {raw}")

    # 결과 화면
    if 'result' in st.session_state:
        df = st.session_state['result']
        
        st.divider()
        tab1, tab2 = st.tabs(["📊 분석 결과 리스트", "📑 리포트 출력"])
        
        with tab1:
            st.dataframe(df.style.applymap(
                lambda x: 'background-color: #d4edda' if x == 'Pass' else 'background-color: #f8d7da',
                subset=['달성여부']
            ), use_container_width=True)
            
        with tab2:
            df_pass = df[df['달성여부'] == 'Pass']
            if df_pass.empty:
                st.warning("목표(23%)를 달성한 데이터가 없습니다.")
            else:
                s_date = st.selectbox("출력할 날짜 선택:", df_pass['날짜'].unique())
                if st.button("📄 PDF 리포트 생성"):
                    row = df_pass[df_pass['날짜'] == s_date].iloc[0]
                    daily_raw = st.session_state['raw']
                    daily_raw = daily_raw[daily_raw['일시'].dt.strftime('%Y-%m-%d') == s_date]
                    
                    fig, ax1 = plt.subplots(figsize=(12, 5))
                    ax1.fill_between(daily_raw['일시'], daily_raw['온도'], color='red', alpha=0.3)
                    ax1.plot(daily_raw['일시'], daily_raw['온도'], color='red', label='Temp')
                    ax1.set_ylabel('Temp', color='red')
                    
                    ax2 = ax1.twinx()
                    ax2.plot(daily_raw['일시'], daily_raw['가스지침'], color='blue', label='Gas')
                    ax2.set_ylabel('Gas', color='blue')
                    
                    plt.title(f"Cycle Trend ({s_date})")
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                        fig.savefig(tmp.name, bbox_inches='tight')
                        img_path = tmp.name
                    
                    pdf = generate_pdf(row, img_path)
                    pdf_bytes = pdf.output(dest='S').encode('latin-1')
                    
                    st.download_button("📥 PDF 다운로드", pdf_bytes, f"Report_{s_date}.pdf", "application/pdf")
                    os.remove(img_path)

if __name__ == "__main__":
    main()
