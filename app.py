import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from fpdf import FPDF
import tempfile
import os

# ---------------------------------------------------------
# 1. 기본 설정
# ---------------------------------------------------------
st.set_page_config(page_title="가열로 5호기 데이터 진단기", layout="wide")
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
# 2. 데이터 로딩 함수
# ---------------------------------------------------------
def smart_read_file(uploaded_file, header_row=0, nrows=None):
    """파일을 읽되, 헤더 위치를 지정해서 읽음"""
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
    except Exception as e:
        return None

# ---------------------------------------------------------
# 3. 데이터 처리 및 분석
# ---------------------------------------------------------
def process_data(df_sensor, df_prod):
    # 컬럼 공백 제거
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]
    df_prod.columns = [str(c).strip() for c in df_prod.columns]
    
    # [생산실적] 0:일자, 1:장입량
    try:
        df_prod.rename(columns={df_prod.columns[0]: '일자', df_prod.columns[1]: '장입량'}, inplace=True)
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        df_prod = df_prod.dropna(subset=['일자', '장입량'])
    except Exception as e:
        return None, f"생산실적 처리 오류: {e}"

    # [가열로] 0:일시, 1:온도, 2:가스
    try:
        df_sensor.rename(columns={df_sensor.columns[0]: '일시', df_sensor.columns[1]: '온도', df_sensor.columns[2]: '가스지침'}, inplace=True)
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except Exception as e:
        return None, f"가열로 데이터 처리 오류: {e}"

    # 매칭
    common_dates = sorted(list(set(df_prod['일자'].dt.date) & set(df_sensor['일시'].dt.date)))
    
    if not common_dates:
        return None, f"날짜 매칭 실패. (생산실적 {len(set(df_prod['일자'].dt.date))}일 vs 가열로 {len(set(df_sensor['일시'].dt.date))}일)"

    # 분석
    results = []
    for date in common_dates:
        prod_row = df_prod[df_prod['일자'] == pd.to_datetime(date)]
        daily_sensor = df_sensor[df_sensor['일시'].dt.date == date]
        
        if prod_row.empty or daily_sensor.empty: continue
        
        charge_kg = prod_row.iloc[0]['장입량']
        if charge_kg <= 0: continue
        
        gas_used = daily_sensor['가스지침'].max() - daily_sensor['가스지침'].min()
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
# 5. 메인 UI (실시간 미리보기 기능)
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템")
    
    with st.sidebar:
        st.header("1. 데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        
        st.markdown("---")
        st.header("2. 제목 줄 맞추기 (필수)")
        st.info("오른쪽 미리보기 표의 **굵은 글씨(첫줄)**가 '일자', '장입량' 등이 되도록 숫자를 조절하세요.")
        header_idx = st.number_input("제목 행 번호 (0부터 시작)", min_value=0, max_value=20, value=0)
        
        st.markdown("---")
        run_btn = st.button("🚀 분석 실행", type="primary")

    # --- 실시간 미리보기 (버튼 안 눌러도 보임) ---
    if prod_file and sensor_files:
        st.subheader("👀 데이터 미리보기 (제목 행을 맞춰주세요!)")
        c1, c2 = st.columns(2)
        
        with c1:
            st.markdown("##### 📄 생산실적 (상위 5행)")
            df_p = smart_read_file(prod_file, header_idx, nrows=5)
            if df_p is not None:
                st.dataframe(df_p)
                st.caption(f"첫 번째 열: **{df_p.columns[0]}** (날짜여야 함)")
            
        with c2:
            st.markdown("##### 🌡️ 가열로 데이터 (상위 5행)")
            df_s = smart_read_file(sensor_files[0], header_idx, nrows=5)
            if df_s is not None:
                st.dataframe(df_s)
                st.caption(f"첫 번째 열: **{df_s.columns[0]}** (시간이어야 함)")
        
        st.warning("👆 위 표의 첫 줄(헤더)이 이상하다면 사이드바의 숫자를 올려보세요.")

    # --- 분석 실행 ---
    if run_btn and prod_file and sensor_files:
        with st.spinner("데이터 분석 중..."):
            # 전체 읽기
            df_prod_full = smart_read_file(prod_file, header_idx)
            df_sensor_list = []
            for f in sensor_files:
                d = smart_read_file(f, header_idx)
                if d is not None: df_sensor_list.append(d)
            
            if df_prod_full is not None and df_sensor_list:
                df_sensor_full = pd.concat(df_sensor_list, ignore_index=True)
                
                # 처리
                res, raw = process_data(df_sensor_full, df_prod_full)
                
                if res is not None:
                    st.session_state['res'] = res
                    st.session_state['raw'] = raw
                    st.success(f"분석 성공! {len(res)}일의 데이터가 확인되었습니다.")
                else:
                    st.error(f"분석 실패: {raw}")
            else:
                st.error("파일 읽기 실패")

    # --- 결과 화면 ---
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
