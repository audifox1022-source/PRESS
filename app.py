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
st.set_page_config(page_title="가열로 5호기 성과 검증 리포트", layout="wide")

TARGET_UNIT_COST = 25.52  # 목표 원단위 (23% 절감 기준)

# 폰트 설정 (나눔고딕)
FONT_FILE = 'NanumGothic.ttf'
HAS_KOREAN_FONT = False

# 폰트 파일 존재 여부 확인 및 설정
if os.path.exists(FONT_FILE):
    try:
        font_prop = fm.FontProperties(fname=FONT_FILE)
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
        HAS_KOREAN_FONT = True
    except:
        pass
else:
    # 폰트가 없으면 기본 폰트 사용 (한글 깨짐 방지 위해 영문 추천)
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. 스마트 데이터 로더 (CSV/Excel 호환)
# ---------------------------------------------------------
def smart_read_file(uploaded_file, header_row=0):
    """파일 확장자와 인코딩을 자동으로 판별하여 읽어오는 함수"""
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file, header=header_row)
        else:
            # CSV: cp949(한글) -> utf-8 순서로 시도
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding='cp949', header=header_row)
            except:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, encoding='utf-8', header=header_row)
    except Exception as e:
        return None

# ---------------------------------------------------------
# 3. 데이터 처리 및 분석 로직
# ---------------------------------------------------------
def process_data(df_sensor, df_prod):
    # 1. 컬럼 공백 제거 (오류 방지)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]
    df_prod.columns = [str(c).strip() for c in df_prod.columns]
    
    # 2. 컬럼 매핑 (순서 기반 매핑: 0번째=날짜, 1번째=값)
    
    # [생산실적] 0:일자, 1:장입량
    try:
        df_prod.rename(columns={df_prod.columns[0]: '일자', df_prod.columns[1]: '장입량'}, inplace=True)
        # 콤마 제거 및 숫자 변환
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        df_prod = df_prod.dropna(subset=['일자', '장입량'])
    except Exception as e:
        return None, f"생산실적 데이터 처리 중 오류: {e}"

    # [가열로 데이터] 0:일시, 1:온도, 2:가스지침
    try:
        cols = df_sensor.columns
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except Exception as e:
        return None, f"가열로 데이터 처리 중 오류: {e}"

    # 3. 날짜 매칭 (공통된 날짜 찾기)
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = sorted(list(prod_dates.intersection(sensor_dates)))
    
    if not common_dates:
        return None, f"날짜 매칭 실패. (생산실적 {len(prod_dates)}일, 센서 {len(sensor_dates)}일 중 일치하는 날짜가 없습니다.)"

    # 4. 성과 분석 Loop
    results = []
    for date in common_dates:
        # 해당 날짜 데이터 추출
        prod_row = df_prod[df_prod['일자'] == pd.to_datetime(date)]
        daily_sensor = df_sensor[df_sensor['일시'].dt.date == date]
        
        if prod_row.empty or daily_sensor.empty: continue
        
        charge_kg = prod_row.iloc[0]['장입량']
        if charge_kg <= 0: continue
        
        # 가스 사용량 (종료값 - 시작값)
        # 데이터 튀는 것 방지를 위해 해당 일자의 Min/Max 사용
        gas_start = daily_sensor['가스지침'].min()
        gas_end = daily_sensor['가스지침'].max()
        gas_used = gas_end - gas_start
        
        if gas_used <= 0: continue
        
        # 원단위 계산 및 판정
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
# 4. PDF 리포트 생성 (제출용 양식)
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
    
    # 1. 소제목
    pdf.set_font(font_name, '', 12)
    pdf.cell(0, 10, f"3.5 가열로 5호기 - {row_data['날짜']} (23% 절감 검증)", 0, 1, 'L')
    pdf.ln(5)

    # 2. 데이터 테이블 (요청 서식 구현)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font(font_name, '', 10)
    
    # 헤더 정의
    headers = ["검침 시작", "검침 완료", "③ 가스 사용량\n(②-①=③)", "Cycle 종료", "장입량"]
    widths = [38, 38, 38, 38, 38] # 합계 190mm
    
    x_start = pdf.get_x()
    y_start = pdf.get_y()
    max_h = 12 # 헤더 높이 (2줄 처리 등 여유있게)
    
    # 헤더 출력
    for i, h in enumerate(headers):
        x = x_start + sum(widths[:i])
        pdf.set_xy(x, y_start)
        pdf.multi_cell(widths[i], 6, h, border=1, align='C', fill=True)
    
    # 데이터 출력 (헤더 높이만큼 띄우고 출력)
    pdf.set_xy(x_start, y_start + max_h)
    
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
    
    # 3. 차트 삽입
    pdf.set_font(font_name, '', 12)
    pdf.cell(0, 10, "▶ 열처리 Chart (온도/가스 트렌드)", 0, 1, 'L')
    pdf.image(chart_path, x=10, w=190)
    
    # 4. 하단 요약
    pdf.ln(5)
    pdf.set_font(font_name, '', 10)
    pdf.cell(0, 8, f"* 실적 원단위: {row_data['원단위']} Nm3/ton (목표 25.52 이하 달성)", 0, 1, 'R')
    
    return pdf

# ---------------------------------------------------------
# 5. 메인 UI (수정됨: UI 충돌 해결)
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템")
    
    # 사이드바 설정
    with st.sidebar:
        st.header("1. 데이터 파일 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        
        st.markdown("---")
        st.header("2. 고급 설정 (데이터 조정)")
        st.caption("데이터 파일의 첫 몇 줄이 제목이라면 숫자를 늘리세요.")
        header_row_idx = st.number_input("헤더(제목) 행 위치", min_value=0, max_value=10, value=0)
        
        run_btn = st.button("분석 실행", type="primary")

    # 분석 실행 로직
    if run_btn:
        if not prod_file or not sensor_files:
            st.error("파일을 모두 업로드해주세요.")
        else:
            with st.spinner("데이터 분석 중..."):
                # 파일 읽기
                df_prod = smart_read_file(prod_file, header_row_idx)
                
                df_sensor_list = []
                for f in sensor_files:
                    df = smart_read_file(f, header_row_idx)
                    if df is not None: df_sensor_list.append(df)
                
                if df_prod is not None and df_sensor_list:
                    df_sensor_all = pd.concat(df_sensor_list, ignore_index=True)
                    
                    # [UI 수정 완료] 데이터 미리보기 (컬럼 분리하여 에러 방지)
                    with st.expander("🔍 데이터가 제대로 읽혔는지 확인하기 (클릭)", expanded=False):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.markdown("**📄 생산실적 (상위 3행)**")
                            st.dataframe(df_prod.head(3))
                            
                        with col2:
                            st.markdown("**🌡️ 가열로 데이터 (상위 3행)**")
                            st.dataframe(df_sensor_all.head(3))
                    
                    # 처리 및 분석
                    res, raw = process_data(df_sensor_all, df_prod)
                    
                    if res is not None:
                        st.session_state['result'] = res
                        st.session_state['raw'] = raw
                        st.success(f"분석 완료! 총 {len(res)}개의 데이터가 매칭되었습니다.")
                    else:
                        st.error(f"분석 실패: {raw}")
                else:
                    st.error("파일을 읽을 수 없습니다. 형식을 확인해주세요.")

    # 결과 화면 표시
    if 'result' in st.session_state:
        df = st.session_state['result']
        
        tab1, tab2 = st.tabs(["📊 데이터 리스트", "📑 리포트 출력"])
        
        with tab1:
            st.subheader("일별 성과 분석 결과")
            # Pass/Fail 색상 적용
            st.dataframe(df.style.applymap(
                lambda x: 'background-color: #d4edda' if x == 'Pass' else 'background-color: #f8d7da',
                subset=['달성여부']
            ), use_container_width=True)
            
        with tab2:
            st.subheader("PDF 리포트 생성")
            
            # Pass 데이터 필터링
            df_pass = df[df['달성여부'] == 'Pass']
            
            if df_pass.empty:
                st.warning("목표(23%)를 달성한 'Pass' 데이터가 없습니다. 장입량을 늘리거나 운전을 개선해야 합니다.")
            else:
                s_date = st.selectbox("리포트 출력 날짜 선택:", df_pass['날짜'].unique())
                
                if st.button("📄 리포트 생성"):
                    row = df_pass[df_pass['날짜'] == s_date].iloc[0]
                    daily_raw = st.session_state['raw']
                    daily_raw = daily_raw[daily_raw['일시'].dt.strftime('%Y-%m-%d') == s_date]
                    
                    # 차트 생성
                    fig, ax1 = plt.subplots(figsize=(12, 5))
                    
                    # 온도 그래프 (빨간색 채우기)
                    ax1.fill_between(daily_raw['일시'], daily_raw['온도'], color='red', alpha=0.3)
                    ax1.plot(daily_raw['일시'], daily_raw['온도'], color='red', label='Temp(C)')
                    ax1.set_ylabel('Temp (C)', color='red')
                    ax1.tick_params(axis='y', labelcolor='red')
                    ax1.grid(True, linestyle='--', alpha=0.5)
                    
                    # 가스 그래프 (파란색 선)
                    ax2 = ax1.twinx()
                    ax2.plot(daily_raw['일시'], daily_raw['가스지침'], color='blue', linewidth=2, label='Gas(m3)')
                    ax2.set_ylabel('Gas Cumulative (m3)', color='blue')
                    ax2.tick_params(axis='y', labelcolor='blue')
                    
                    plt.title(f"Cycle Trend ({s_date})")
                    
                    # 이미지 임시 저장
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
                        fig.savefig(tmp_img.name, bbox_inches='tight')
                        img_path = tmp_img.name
                        
                    # PDF 생성
                    pdf = generate_pdf(row, img_path)
                    pdf_bytes = pdf.output(dest='S').encode('latin-1')
                    
                    st.download_button(
                        label="📥 PDF 다운로드",
                        data=pdf_bytes,
                        file_name=f"Report_{s_date}.pdf",
                        mime="application/pdf"
                    )
                    os.remove(img_path)

if __name__ == "__main__":
    main()
