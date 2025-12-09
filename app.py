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

# 목표 원단위 설정 (23% 절감 기준)
TARGET_UNIT_COST = 25.52  

# 폰트 파일 설정 (GitHub 배포 시 같은 폴더에 NanumGothic.ttf가 있어야 함)
FONT_FILE = 'NanumGothic.ttf'
font_name = 'Arial' # 기본값 (폰트 없을 시 영문)

# 폰트 로딩 로직 (클라우드/로컬 호환)
if os.path.exists(FONT_FILE):
    font_prop = fm.FontProperties(fname=FONT_FILE)
    font_name = font_prop.get_name()
    plt.rcParams['font.family'] = font_name
    plt.rcParams['axes.unicode_minus'] = False
    HAS_KOREAN_FONT = True
else:
    # 폰트 파일이 없을 경우 경고 메시지는 사이드바에 작게 표시하거나 생략 가능
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False
    HAS_KOREAN_FONT = False

# ---------------------------------------------------------
# 2. 데이터 처리 함수
# ---------------------------------------------------------
@st.cache_data # 데이터 처리 속도 향상을 위한 캐싱
def load_and_process_data(sensor_files, prod_file):
    # A. 생산 실적 로딩 (Excel)
    try:
        df_prod = pd.read_excel(prod_file)
        # 첫 번째 컬럼: 날짜, 두 번째 컬럼: 장입량(kg)으로 가정 및 표준화
        df_prod.rename(columns={df_prod.columns[0]: '일자', df_prod.columns[1]: '장입량'}, inplace=True)
        df_prod['일자'] = pd.to_datetime(df_prod['일자'])
    except Exception as e:
        return None, f"생산 실적 파일 오류: {e}"

    # B. 센서 데이터 로딩 (CSV 및 Excel 지원)
    df_list = []
    for f in sensor_files:
        try:
            # 파일 확장자 확인
            if f.name.endswith('.xlsx') or f.name.endswith('.xls'):
                # 엑셀 파일 로딩
                temp = pd.read_excel(f)
            else:
                # CSV 파일 로딩 (인코딩 대응)
                try:
                    temp = pd.read_csv(f, encoding='cp949')
                except:
                    temp = pd.read_csv(f, encoding='utf-8')
            
            df_list.append(temp)
            
        except Exception as e:
            return None, f"파일 로딩 오류 ({f.name}): {e}"
    
    if not df_list:
        return None, "업로드된 데이터가 없습니다."
        
    df_sensor = pd.concat(df_list, ignore_index=True)
    
    # 컬럼명 표준화 (사용자 데이터: 일시, 온도, 가스지침 순서 가정)
    try:
        # 안전하게 인덱스로 접근하여 컬럼명 변경
        cols = df_sensor.columns
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'])
        df_sensor = df_sensor.sort_values('일시') # 시간순 정렬
    except Exception as e:
        return None, f"데이터 컬럼 형식 오류: {e}"

    # C. 성과 분석 로직 (일별 집계)
    results = []
    
    # 날짜별 그룹핑
    for date, group in df_sensor.groupby(df_sensor['일시'].dt.date):
        date_ts = pd.to_datetime(date)
        
        # 해당 날짜의 장입량 매칭
        prod_row = df_prod[df_prod['일자'] == date_ts]
        
        if prod_row.empty:
            continue # 생산 실적이 없는 날은 스킵
            
        charge_kg = prod_row.iloc[0]['장입량']
        charge_ton = charge_kg / 1000
        
        if charge_ton <= 0: continue

        # 가스 사용량 계산 (Max - Min)
        gas_start = group['가스지침'].min()
        gas_end = group['가스지침'].max()
        gas_used = gas_end - gas_start
        
        if gas_used <= 0: continue # 가스 사용량이 없으면 스킵

        # 원단위 계산
        unit_cost = gas_used / charge_ton
        
        # 목표 달성 여부 판정
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date_ts.strftime('%Y-%m-%d'),
            '장입량(kg)': int(charge_kg),
            '가스사용량(Nm3)': int(gas_used),
            '원단위(Nm3/ton)': round(unit_cost, 2),
            '목표(23%)': TARGET_UNIT_COST,
            '달성여부': 'Pass' if is_pass else 'Fail'
        })
    
    if not results:
        return None, "매칭되는 데이터가 없습니다. 날짜 형식을 확인해주세요."

    return pd.DataFrame(results), df_sensor

# ---------------------------------------------------------
# 3. PDF 생성 클래스 (한글 지원)
# ---------------------------------------------------------
class PDFReport(FPDF):
    def header(self):
        # 폰트가 있으면 사용, 없으면 Arial
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

def generate_pdf(row_data, chart_image_path):
    pdf = PDFReport()
    pdf.add_page()
    
    # 폰트 설정
    if HAS_KOREAN_FONT:
        pdf.add_font('Nanum', '', FONT_FILE, uni=True)
        body_font = 'Nanum'
    else:
        body_font = 'Arial'

    pdf.set_font(body_font, '', 12)
    
    # 1. 요약 테이블
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 10, f"Date: {row_data['날짜']}", 0, 1, 'L', 1)
    pdf.ln(5)
    
    # 데이터 리스트
    items = [
        ("Charge Weight", f"{row_data['장입량(kg)']} kg"),
        ("Gas Consumption", f"{row_data['가스사용량(Nm3)']} Nm3"),
        ("Unit Cost", f"{row_data['원단위(Nm3/ton)']} Nm3/ton"),
        ("Target (23% Cut)", f"{TARGET_UNIT_COST} Nm3/ton"),
        ("Verification Result", "PASS (Successful)")
    ]
    
    col_w = 95
    for key, value in items:
        pdf.cell(col_w, 10, key, 1)
        pdf.cell(col_w, 10, str(value), 1, 1)
    
    pdf.ln(10)
    
    # 2. 차트 삽입
    pdf.cell(0, 10, "Temperature & Gas Trend", 0, 1, 'L')
    pdf.image(chart_image_path, x=10, w=190)
    
    return pdf

# ---------------------------------------------------------
# 4. 메인 UI 구성
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 설비 개선 검증 시스템")
    
    # 사이드바: 파일 업로드
    with st.sidebar:
        st.header("1. 데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        # [수정] type에 xlsx, xls 추가
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        
        process_btn = st.button("데이터 분석 실행")
        st.info("⚠️ GitHub 배포 시 데이터 파일은 업로드하지 마세요. (보안)")

    # 메인 화면
    if prod_file and sensor_files and process_btn:
        with st.spinner("데이터 분석 중... (대용량 엑셀은 시간이 조금 걸릴 수 있습니다)"):
            df_result, df_raw = load_and_process_data(sensor_files, prod_file)
            
            if df_result is not None:
                st.session_state['df_result'] = df_result
                st.session_state['df_raw'] = df_raw
                st.success("분석이 완료되었습니다!")
            else:
                st.error(f"분석 실패: {df_raw}")

    # 결과 표출
    if 'df_result' in st.session_state:
        df_result = st.session_state['df_result']
        
        # 탭 구성
        tab1, tab2 = st.tabs(["📊 성과 분석 결과", "📑 리포트 생성"])
        
        with tab1:
            st.subheader("일별 성과 분석 결과")
            
            # Pass/Fail 필터
            filter_option = st.radio("보기 옵션:", ["전체 보기", "✅ Pass 데이터만 보기"], horizontal=True)
            
            if filter_option == "✅ Pass 데이터만 보기":
                df_display = df_result[df_result['달성여부'] == 'Pass']
            else:
                df_display = df_result
                
            st.dataframe(df_display.style.applymap(
                lambda x: 'background-color: #d4edda' if x == 'Pass' else 'background-color: #f8d7da',
                subset=['달성여부']
            ), use_container_width=True)
            
        with tab2:
            st.subheader("검증 리포트(PDF) 생성")
            
            # Pass된 날짜만 선택 가능
            pass_dates = df_result[df_result['달성여부'] == 'Pass']['날짜'].unique()
            
            if len(pass_dates) == 0:
                st.warning("목표(23%)를 달성한 데이터가 없습니다.")
            else:
                selected_date = st.selectbox("리포트를 생성할 날짜를 선택하세요:", pass_dates)
                
                if selected_date:
                    # 선택된 날짜의 데이터 준비
                    row_data = df_result[df_result['날짜'] == selected_date].iloc[0]
                    raw_data = st.session_state['df_raw']
                    # 해당 날짜의 1분 단위 로그 필터링
                    daily_raw = raw_data[raw_data['일시'].dt.strftime('%Y-%m-%d') == selected_date]
                    
                    # 차트 미리보기
                    fig, ax1 = plt.subplots(figsize=(10, 4))
                    ax1.plot(daily_raw['일시'], daily_raw['온도'], 'r-', label='Temperature')
                    ax1.set_ylabel('Temperature (℃)', color='r')
                    ax1.tick_params(axis='y', labelcolor='r')
                    
                    ax2 = ax1.twinx()
                    ax2.plot(daily_raw['일시'], daily_raw['가스지침'], 'b--', label='Gas')
                    ax2.set_ylabel('Gas Cumulative (m3)', color='b')
                    ax2.tick_params(axis='y', labelcolor='b')
                    
                    plt.title(f"Cycle Trend ({selected_date})")
                    st.pyplot(fig)
                    
                    # PDF 생성 버튼
                    if st.button("📥 PDF 다운로드"):
                        # 임시 차트 이미지 저장
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
                            fig.savefig(tmp_img.name)
                            chart_path = tmp_img.name
                        
                        # PDF 생성
                        pdf = generate_pdf(row_data, chart_path)
                        
                        # PDF 파일 바이트 변환
                        pdf_bytes = pdf.output(dest='S').encode('latin-1')
                        
                        st.download_button(
                            label="PDF 파일 저장",
                            data=pdf_bytes,
                            file_name=f"Report_{selected_date}.pdf",
                            mime="application/pdf"
                        )
                        
                        os.remove(chart_path) # 임시 파일 삭제

if __name__ == "__main__":
    main()
