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
# 2. 데이터 처리 함수 (완전 맞춤형)
# ---------------------------------------------------------
def process_data(sensor_files, df_prod, col_p_date, col_p_weight, 
                s_header_row, col_s_time, col_s_temp, col_s_gas):
    
    # === A. 생산 실적 처리 ===
    try:
        # 선택된 컬럼으로 이름 변경
        df_prod = df_prod.rename(columns={col_p_date: '일자', col_p_weight: '장입량'})
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        
        # 장입량 숫자 변환
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        
        df_prod = df_prod.dropna(subset=['일자', '장입량'])
    except Exception as e:
        return None, f"생산 실적 처리 중 오류: {e}"

    # === B. 가열로 데이터 로딩 ===
    df_list = []
    for f in sensor_files:
        try:
            # 파일 포인터 초기화
            f.seek(0)
            if f.name.endswith('.xlsx') or f.name.endswith('.xls'):
                temp = pd.read_excel(f, header=s_header_row)
            else:
                try:
                    temp = pd.read_csv(f, encoding='cp949', header=s_header_row)
                except:
                    temp = pd.read_csv(f, encoding='utf-8', header=s_header_row)
            df_list.append(temp)
        except Exception as e:
            return None, f"파일 로딩 오류 ({f.name}): {e}"
    
    if not df_list: return None, "가열로 데이터 없음"
    
    df_sensor = pd.concat(df_list, ignore_index=True)
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns] # 공백 제거

    # 가열로 컬럼 매핑
    try:
        df_sensor = df_sensor.rename(columns={col_s_time: '일시', col_s_temp: '온도', col_s_gas: '가스지침'})
        
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor['온도'] = pd.to_numeric(df_sensor['온도'], errors='coerce')
        df_sensor['가스지침'] = pd.to_numeric(df_sensor['가스지침'], errors='coerce')
        
        df_sensor = df_sensor.dropna(subset=['일시'])
        df_sensor = df_sensor.sort_values('일시')
    except Exception as e:
        return None, f"가열로 데이터 컬럼 매핑 오류: {e}"

    # === C. 날짜 매칭 ===
    prod_dates = set(df_prod['일자'].dt.date)
    sensor_dates = set(df_sensor['일시'].dt.date)
    common_dates = sorted(list(prod_dates.intersection(sensor_dates)))
    
    if not common_dates:
        return None, f"매칭 실패 (생산 {len(prod_dates)}일 vs 센서 {len(sensor_dates)}일). 날짜 형식을 확인하세요."

    # === D. 분석 Loop ===
    results = []
    for date in common_dates:
        prod_row = df_prod[df_prod['일자'] == pd.to_datetime(date)]
        daily = df_sensor[df_sensor['일시'].dt.date == date]
        
        if prod_row.empty or daily.empty: continue
        
        charge = prod_row.iloc[0]['장입량']
        if charge <= 0: continue
        
        gas_used = daily['가스지침'].max() - daily['가스지침'].min()
        if gas_used <= 0: continue
        
        unit = gas_used / (charge / 1000)
        is_pass = unit <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date.strftime('%Y-%m-%d'),
            '검침시작': daily.iloc[0]['일시'].strftime('%Y-%m-%d %H:%M'),
            '검침완료': daily.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            'Cycle종료': daily.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            '가스사용량(Nm3)': int(gas_used),
            '장입량(kg)': int(charge),
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
# 5. 메인 UI (양쪽 파일 모두 설정 가능)
# ---------------------------------------------------------
def main():
    st.title("🏭 가열로 5호기 성과 검증 시스템 (정밀 설정)")
    
    with st.sidebar:
        st.header("1. 데이터 업로드")
        prod_file = st.file_uploader("생산 실적 (Excel)", type=['xlsx'])
        sensor_files = st.file_uploader("가열로 데이터 (CSV/Excel)", type=['csv', 'xlsx', 'xls'], accept_multiple_files=True)
        
        st.divider()
        st.header("2. 생산실적 설정")
        p_header = st.number_input("생산실적 제목 행", 0, 10, 0, key='p_h')
        
        st.divider()
        st.header("3. 가열로 데이터 설정")
        s_header = st.number_input("가열로 데이터 제목 행", 0, 20, 0, key='s_h')
        st.info("csv 파일의 제목 줄 위치를 맞춰주세요.")
        
        run_btn = st.button("🚀 분석 실행", type="primary")

    # --- 설정 및 미리보기 화면 ---
    if prod_file and sensor_files:
        st.subheader("🛠️ 데이터 컬럼 지정 (필수)")
        c1, c2 = st.columns(2)
        
        # 1. 생산 실적 설정
        with c1:
            st.markdown("##### 📄 생산 실적")
            try:
                df_p = pd.read_excel(prod_file, header=p_header)
                st.dataframe(df_p.head(2))
                
                col_p_date = st.selectbox("📅 날짜 컬럼", df_p.columns, index=0)
                col_p_weight = st.selectbox("⚖️ 장입량 컬럼", df_p.columns, index=1 if len(df_p.columns)>1 else 0)
            except:
                st.error("생산 실적 파일을 읽을 수 없습니다. 제목 행을 확인하세요.")

        # 2. 가열로 데이터 설정
        with c2:
            st.markdown("##### 🌡️ 가열로 데이터 (첫 파일 기준)")
            try:
                # 첫 번째 파일만 읽어서 컬럼 확인
                f = sensor_files[0]
                f.seek(0)
                if f.name.endswith('csv'):
                    try:
                        df_s = pd.read_csv(f, encoding='cp949', header=s_header, nrows=5)
                    except:
                        f.seek(0)
                        df_s = pd.read_csv(f, encoding='utf-8', header=s_header, nrows=5)
                else:
                    df_s = pd.read_excel(f, header=s_header, nrows=5)
                
                st.dataframe(df_s.head(2))
                
                col_s_time = st.selectbox("⏰ 시간(일시) 컬럼", df_s.columns, index=0)
                col_s_temp = st.selectbox("🔥 온도 컬럼", df_s.columns, index=1 if len(df_s.columns)>1 else 0)
                col_s_gas = st.selectbox("⛽ 가스(지침/유량) 컬럼", df_s.columns, index=2 if len(df_s.columns)>2 else 0)
            except:
                st.error("가열로 데이터 파일을 읽을 수 없습니다. 제목 행을 확인하세요.")

        # --- 분석 실행 ---
        if run_btn:
            with st.spinner("데이터 정밀 분석 중..."):
                # 전체 읽기 및 처리
                f_prod = pd.read_excel(prod_file, header=p_header) # 다시 읽기
                
                res, raw = process_data(sensor_files, f_prod, 
                                      col_p_date, col_p_weight, 
                                      s_header, col_s_time, col_s_temp, col_s_gas)
                
                if res is not None:
                    st.session_state['res'] = res
                    st.session_state['raw'] = raw
                    st.success(f"분석 성공! 총 {len(res)}일 데이터가 매칭되었습니다.")
                else:
                    st.error(f"분석 실패: {raw}")

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
