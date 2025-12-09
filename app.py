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
    # 폰트가 없으면 기본 영문 폰트 사용 (에러 방지)
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. 데이터 처리 함수 (진단 기능 포함)
# ---------------------------------------------------------
@st.cache_data
def load_and_process_data(sensor_files, prod_file):
    # --- A. 생산 실적 로딩 (Excel) ---
    try:
        df_prod = pd.read_excel(prod_file)
        # 컬럼명 공백 제거 (오류 방지)
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 첫 번째=날짜, 두 번째=장입량으로 강제 지정
        col_date = df_prod.columns[0]
        col_weight = df_prod.columns[1]
        df_prod.rename(columns={col_date: '일자', col_weight: '장입량'}, inplace=True)
        
        # 날짜 변환 (강제)
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        df_prod = df_prod.dropna(subset=['일자']) # 날짜 없는 행 삭제
        
    except Exception as e:
        return None, f"생산 실적 파일 오류: {e}"

    # --- B. 가열로 데이터 로딩 (CSV/Excel) ---
    df_list = []
    for f in sensor_files:
        try:
            if f.name.endswith('.xlsx') or f.name.endswith('.xls'):
                temp = pd.read_excel(f)
            else:
                # CSV 인코딩 시도
                try:
                    temp = pd.read_csv(f, encoding='cp949')
                except:
                    temp = pd.read_csv(f, encoding='utf-8')
            df_list.append(temp)
        except Exception as e:
            return None, f"파일 로딩 오류 ({f.name}): {e}"
    
    if not df_list:
        return None, "업로드된 가열로 데이터가 없습니다."
        
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]

    # 컬럼 매핑 및 날짜 변환
    try:
        cols = df_sensor.columns
        # 첫번째=일시, 두번째=온도, 세번째=가스지침 가정
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except Exception as e:
        return None, f"가열로 데이터 포맷 오류: {e}"

    # --- C. 데이터 매칭 진단 ---
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = prod_dates.intersection(sensor_dates)
    
    if len(common_dates) == 0:
        return None, "날짜 매칭 실패: 생산실적과 가열로 데이터의 날짜가 일치하지 않습니다. (형식 불일치 가능성)"

    # --- D. 성과 분석 ---
    results = []
    for date in common_dates:
        date_ts = pd.to_datetime(date)
        
        # 1. 장입량 가져오기
        prod_row = df_prod[df_prod['일자'] == date_ts]
        if prod_row.empty: continue
        
        charge_val = prod_row.iloc[0]['장입량']
        # 콤마 제거 등 숫자 변환
        if isinstance(charge_val, str):
            charge_val = float(str(charge_val).replace(',', ''))
        charge_kg = float(charge_val)
        charge_ton = charge_kg / 1000
        
        if charge_ton <= 0: continue

        # 2. 가스 사용량 계산
        # 해당 날짜의 데이터만 필터링
        daily_sensor = df_sensor[df_sensor['일시'].dt.date == date]
        if daily_sensor.empty: continue
        
        gas_start = daily_sensor['가스지침'].min()
        gas_end = daily_sensor['가스지침'].max()
        gas_used = gas_end - gas_start
        
        if gas_used <= 0: continue

        # 3. 원단위 및 판정
        unit_cost = gas_used / charge_ton
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date.strftime('%Y-%m-%d'),
            '장입량(kg)': int(charge_kg),
            '가스사용량(Nm3)': int(gas_used),
            '원단위(Nm3/ton)': round(unit_cost, 2),
            '목표(23%)': TARGET_UNIT_COST,
            '달성여부': 'Pass' if is_pass else 'Fail'
        })
    
    if not results:
        return None, "분석 가능한 데이터가 없습니다."

    return pd.DataFrame(results), df_sensor

# ---------------------------------------------------------
# 3. PDF 생성 클래스
# ---------------------------------------------------------
class PDFReport(FPDF):
    def header(self):
        if HAS_KOREAN_FONT:
            self.add_font('Nanum', '', FONT_FILE, uni=True)
            self.set_font('Nanum', '', 16)
        else:
            self.set_font('Arial', 'B', 16)
        self.cell(0, 10, 'Furnace No.5 Performance Report', 0, 1, 'C')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def generate_pdf(row_data, chart_path):
    pdf = PDFReport()
    pdf.add_page()
    
    if HAS_KOREAN_FONT:
        pdf.add_font('Nanum', '', FONT_FILE, uni=True)
        pdf.set_font('Nanum', '', 12)
    else:
        pdf.set_font('Arial', '', 12)
    
    # 요약 정보
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(0, 10, f"Date: {row_data['날짜']}", 0, 1, 'L', 1)
    pdf.ln(5)
    
    info = [
        ("Charge Weight", f"{row_data['장입량(kg)']} kg"),
        ("Gas Usage", f"{row_data['가스사용량(Nm3)']} Nm3"),
        ("Unit Cost", f"{row_data['원단위(Nm3/ton)']} Nm3/ton"),
        ("Target", f"{TARGET_UNIT_COST} Nm3/ton"),
        ("Result", "PASS")
    ]
    
    for k, v in info:
        pdf.cell(90, 10, k, 1)
        pdf.cell(90, 10, v, 1, 1)
    
    pdf.ln(10)
    pdf.cell(0, 10, "Cycle Trend Chart", 0, 1)
    pdf.image(chart_path, x=10, w=190)
    
    return pdf

# ---------------------------------------------------------
# 4. 메인 화면 (UI)
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템")
    
    # 사이드바
    with st.sidebar:
        st.header("파일 업로드")
        prod_file = st.file_uploader("1. 생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("2. 가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
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
                # 에러 메시지 출력 (df_raw에 에러 메시지가 담김)
                st.error(f"분석 실패: {df_raw}")
                
                # 진단용 샘플 데이터 표시 (원인 파악용)
                st.write("---")
                st.warning("👇 **데이터 로딩 상태 확인 (디버깅용)**")
                try:
                    # 생산 실적 미리보기
                    p_df = pd.read_excel(prod_file)
                    st.write("**[생산 실적 파일 미리보기]**", p_df.head(2))
                    
                    # 센서 데이터 미리보기 (첫 번째 파일만)
                    f = sensor_files[0]
                    f.seek(0) # 파일 포인터 초기화
                    if f.name.endswith('csv'):
                        s_df = pd.read_csv(f, encoding='cp949')
                    else:
                        s_df = pd.read_excel(f)
                    st.write("**[가열로 데이터 파일 미리보기]**", s_df.head(2))
                except:
                    pass

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
                    
                    # 차트 생성
                    fig, ax1 = plt.subplots(figsize=(10, 4))
                    ax1.plot(daily_raw['일시'], daily_raw['온도'], 'r-', label='Temp')
                    ax1.set_ylabel('Temp (C)', color='r')
                    ax1.tick_params(axis='y', labelcolor='r')
                    
                    ax2 = ax1.twinx()
                    ax2.plot(daily_raw['일시'], daily_raw['가스지침'], 'b--', label='Gas')
                    ax2.set_ylabel('Gas (m3)', color='b')
                    
                    plt.title(f"Furnace Trend ({selected_date})")
                    
                    # 이미지 저장
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
                        fig.savefig(tmp_img.name)
                        img_path = tmp_img.name
                        
                    # PDF 생성
                    pdf = generate_pdf(row, img_path)
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
