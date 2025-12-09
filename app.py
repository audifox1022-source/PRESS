import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from fpdf import FPDF
import tempfile
import os

# ---------------------------------------------------------
# 1. 앱 설정
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
    except: pass
else:
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 2. [핵심] 헤더 자동 감지 함수 (알아서 줄 찾기)
# ---------------------------------------------------------
def find_header_row(file, file_type, keywords):
    """
    파일의 앞부분(20줄)을 읽어서 keywords(예: '일자', '장입량')가 
    포함된 행 번호를 자동으로 찾아냅니다.
    """
    try:
        file.seek(0)
        # 앞 20줄만 읽어봄
        if file_type == 'excel':
            df_preview = pd.read_excel(file, header=None, nrows=20)
        else:
            try:
                df_preview = pd.read_csv(file, header=None, nrows=20, encoding='cp949')
            except:
                file.seek(0)
                df_preview = pd.read_csv(file, header=None, nrows=20, encoding='utf-8')
        
        # 행별로 검사
        for idx, row in df_preview.iterrows():
            row_str = row.astype(str).values.tolist()
            # 행에 키워드가 하나라도 포함되어 있으면 그 줄이 헤더!
            # (공백 제거 후 비교)
            row_text = "".join([str(x).strip() for x in row_str])
            for kw in keywords:
                if kw in row_text:
                    file.seek(0) # 파일 포인터 초기화 (중요)
                    return idx # 찾은 행 번호 반환
                    
        file.seek(0)
        return 0 # 못 찾으면 0번 줄로 가정
    except:
        file.seek(0)
        return 0

# ---------------------------------------------------------
# 3. 데이터 로딩 및 처리
# ---------------------------------------------------------
@st.cache_data
def load_and_process_data(sensor_files, prod_file):
    # --- A. 생산 실적 로딩 (자동 감지) ---
    try:
        # 1. '일자' 또는 '장입량' 단어가 있는 줄을 찾음
        header_idx = find_header_row(prod_file, 'excel', ['일자', '장입량', 'Date', 'Charge'])
        
        df_prod = pd.read_excel(prod_file, header=header_idx)
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 컬럼 매핑 (첫번째=일자, 두번째=장입량)
        if len(df_prod.columns) >= 2:
            df_prod.rename(columns={df_prod.columns[0]: '일자', df_prod.columns[1]: '장입량'}, inplace=True)
            
            # 전처리
            df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
            if df_prod['장입량'].dtype == object:
                df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
            df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
            df_prod = df_prod.dropna(subset=['일자', '장입량'])
        else:
            return None, "생산실적 파일 양식을 확인해주세요."
            
    except Exception as e:
        return None, f"생산 실적 오류: {e}"

    # --- B. 가열로 데이터 로딩 (자동 감지) ---
    df_list = []
    for f in sensor_files:
        try:
            # '일시', '온도', '가스' 단어가 있는 줄을 찾음
            header_idx = 0
            is_excel = f.name.endswith('.xlsx') or f.name.endswith('.xls')
            file_type = 'excel' if is_excel else 'csv'
            
            header_idx = find_header_row(f, file_type, ['일시', '온도', '가스', 'Time', 'Temp'])
            
            if is_excel:
                temp = pd.read_excel(f, header=header_idx)
            else:
                try:
                    temp = pd.read_csv(f, encoding='cp949', header=header_idx)
                except:
                    temp = pd.read_csv(f, encoding='utf-8', header=header_idx)
            df_list.append(temp)
        except Exception as e:
            return None, f"파일 로딩 오류 ({f.name}): {e}"
    
    if not df_list: return None, "데이터 없음"
    
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]

    # 컬럼 매핑
    try:
        df_sensor.rename(columns={df_sensor.columns[0]: '일시', df_sensor.columns[1]: '온도', df_sensor.columns[2]: '가스지침'}, inplace=True)
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except:
        return None, "가열로 데이터 포맷 오류"

    # --- C. 날짜 매칭 및 분석 ---
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = sorted(list(prod_dates.intersection(sensor_dates)))
    
    if not common_dates:
        return None, f"날짜 매칭 실패 (생산 {len(prod_dates)}일, 센서 {len(sensor_dates)}일 감지됨)"

    results = []
    for date in common_dates:
        prod_row = df_prod[df_prod['일자'] == pd.to_datetime(date)]
        daily = df_sensor[df_sensor['일시'].dt.date == date]
        
        if prod_row.empty or daily.empty: continue
        
        charge_kg = prod_row.iloc[0]['장입량']
        if charge_kg <= 0: continue
        
        gas_used = daily['가스지침'].max() - daily['가스지침'].min()
        if gas_used <= 0: continue
        
        unit = gas_used / (charge_kg / 1000)
        is_pass = unit <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date.strftime('%Y-%m-%d'),
            '검침시작': daily.iloc[0]['일시'].strftime('%Y-%m-%d %H:%M'),
            '검침완료': daily.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            'Cycle종료': daily.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            '가스사용량(Nm3)': int(gas_used),
            '장입량(kg)': int(charge_kg),
            '원단위': round(unit, 2),
            '달성여부': 'Pass' if is_pass else 'Fail'
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

def generate_pdf(row_data, chart_path):
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
    
    x = pdf.get_x()
    y = pdf.get_y()
    for i, h in enumerate(headers):
        pdf.set_xy(x + sum(widths[:i]), y)
        pdf.multi_cell(widths[i], 6, h, border=1, align='C', fill=True)
    
    pdf.set_xy(x, y + 12)
    vals = [str(row_data['검침시작']), str(row_data['검침완료']), f"{row_data['가스사용량(Nm3)']:,} Nm3", str(row_data['Cycle종료']), f"{row_data['장입량(kg)']:,} kg"]
    for i, v in enumerate(vals):
        pdf.cell(widths[i], 10, v, border=1, align='C')
    
    pdf.ln(15)
    pdf.set_font(font, '', 12)
    pdf.cell(0, 10, "▶ 열처리 Chart (온도/가스 트렌드)", 0, 1, 'L')
    pdf.image(chart_path, x=10, w=190)
    pdf.ln(5)
    pdf.set_font(font, '', 10)
    pdf.cell(0, 8, f"* 실적 원단위: {row_data['원단위']} Nm3/ton (목표 25.52 이하 달성)", 0, 1, 'R')
    return pdf

# ---------------------------------------------------------
# 5. 메인 UI
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템 (AI 자동감지)")
    
    with st.sidebar:
        st.header("데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        st.info("💡 파일 제목줄을 자동으로 찾습니다.")
        run_btn = st.button("🚀 분석 실행", type="primary")

    if run_btn and prod_file and sensor_files:
        with st.spinner("데이터 분석 및 헤더 자동 탐색 중..."):
            res, raw = load_and_process_data(sensor_files, prod_file)
            
            if res is not None:
                st.session_state['res'] = res
                st.session_state['raw'] = raw
                st.success(f"분석 완료! 총 {len(res)}일 데이터가 매칭되었습니다.")
            else:
                st.error(f"분석 실패: {raw}")

    if 'res' in st.session_state:
        df = st.session_state['res']
        
        st.divider()
        t1, t2 = st.tabs(["📊 분석 결과", "📑 리포트 출력"])
        
        with t1:
            st.dataframe(df.style.applymap(lambda x: 'background-color:#d4edda' if x=='Pass' else 'background-color:#f8d7da', subset=['달성여부']), use_container_width=True)
            
        with t2:
            df_pass = df[df['달성여부'] == 'Pass']
            if df_pass.empty:
                st.warning("목표(23%) 달성 데이터가 없습니다.")
            else:
                s_date = st.selectbox("날짜 선택:", df_pass['날짜'].unique())
                if st.button("PDF 생성"):
                    row = df_pass[df_pass['날짜'] == s_date].iloc[0]
                    daily = st.session_state['raw']
                    daily = daily[daily['일시'].dt.strftime('%Y-%m-%d') == s_date]
                    
                    fig, ax1 = plt.subplots(figsize=(12, 5))
                    ax1.fill_between(daily['일시'], daily['온도'], color='red', alpha=0.3)
                    ax1.plot(daily['일시'], daily['온도'], 'r-', label='Temp')
                    ax1.set_ylabel('Temp', color='r')
                    
                    ax2 = ax1.twinx()
                    ax2.plot(daily['일시'], daily['가스지침'], 'b-', label='Gas')
                    ax2.set_ylabel('Gas', color='b')
                    
                    plt.title(f"Trend ({s_date})")
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                        fig.savefig(tmp.name, bbox_inches='tight')
                        img_path = tmp.name
                    
                    pdf = generate_pdf(row, img_path)
                    pdf_bytes = pdf.output(dest='S').encode('latin-1')
                    st.download_button("📥 다운로드", pdf_bytes, f"Report_{s_date}.pdf", "application/pdf")
                    os.remove(img_path)

if __name__ == "__main__":
    main()
