import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from fpdf import FPDF
import tempfile
import os

# ... [기본 설정 및 폰트 로딩 부분은 기존과 동일하게 유지] ...
# (기존 코드의 맨 윗부분은 그대로 두시고, def load_and_process_data 부터 아래 내용으로 바꾸세요)

# ---------------------------------------------------------
# 2. 데이터 처리 함수 (수정됨: 강력한 날짜 변환 및 디버깅 추가)
# ---------------------------------------------------------
@st.cache_data
def load_and_process_data(sensor_files, prod_file):
    # ==========================================
    # 1. 생산 실적 로딩 (Excel)
    # ==========================================
    try:
        df_prod = pd.read_excel(prod_file)
        # 컬럼명 공백 제거
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 디버깅: 컬럼명 확인
        st.write("🔍 **[진단] 생산 실적 파일 컬럼:**", df_prod.columns.tolist())
        
        # 첫 번째 컬럼을 날짜, 두 번째를 장입량으로 강제 지정
        col_date = df_prod.columns[0]
        col_weight = df_prod.columns[1]
        
        df_prod.rename(columns={col_date: '일자', col_weight: '장입량'}, inplace=True)
        
        # 날짜 변환 (에러 발생 시 강제 변환 시도)
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        
        # 날짜 변환 실패(NaT) 데이터 제거
        if df_prod['일자'].isnull().sum() > 0:
            st.warning(f"생산 실적에서 날짜 변환 실패한 행이 {df_prod['일자'].isnull().sum()}개 있습니다. (제외됨)")
            df_prod = df_prod.dropna(subset=['일자'])
            
    except Exception as e:
        return None, f"생산 실적 파일 오류: {e}"

    # ==========================================
    # 2. 센서 데이터 로딩 (CSV/Excel)
    # ==========================================
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
        return None, "업로드된 데이터가 없습니다."
        
    df_sensor = pd.concat(df_list, ignore_index=True)
    
    # 컬럼명 공백 제거
    df_sensor.columns = [str(c).strip() for c in df_sensor.columns]
    
    # 디버깅: 센서 데이터 컬럼 확인
    st.write("🔍 **[진단] 가열로 데이터 파일 컬럼:**", df_sensor.columns.tolist())

    try:
        # 컬럼 위치 기반 매핑 (첫번째=시간, 두번째=온도, 세번째=가스)
        cols = df_sensor.columns
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        
        # 날짜 변환
        df_sensor['일시'] = pd.to_datetime(df_sensor['일시'], errors='coerce')
        df_sensor = df_sensor.dropna(subset=['일시']) # 날짜 없는 행 삭제
        df_sensor = df_sensor.sort_values('일시')
        
    except Exception as e:
        return None, f"가열로 데이터 컬럼 형식 오류: {e}"

    # ==========================================
    # 3. 데이터 매칭 테스트 (디버깅용)
    # ==========================================
    sensor_dates = set(df_sensor['일시'].dt.date.unique())
    prod_dates = set(df_prod['일자'].dt.date.unique())
    common_dates = sensor_dates.intersection(prod_dates)
    
    st.info(f"📅 **날짜 매칭 진단 결과:**\n"
            f"- 생산실적 날짜 수: {len(prod_dates)}일\n"
            f"- 가열로 데이터 날짜 수: {len(sensor_dates)}일\n"
            f"- **일치하는 날짜: {len(common_dates)}일** (여기가 0이면 매칭 실패)")

    if len(common_dates) == 0:
        st.error("❌ 일치하는 날짜가 하나도 없습니다. 엑셀과 CSV의 날짜 형식을 확인해주세요.")
        # 데이터 샘플 보여주기 (원인 파악용)
        st.write("### 데이터 샘플 (형식 확인용)")
        col1, col2 = st.columns(2)
        with col1:
            st.write("생산 실적 (상위 5개)", df_prod.head())
        with col2:
            st.write("가열로 데이터 (상위 5개)", df_sensor.head())
        return None, "날짜 매칭 실패"

    # ==========================================
    # 4. 성과 분석 로직 (기존과 동일)
    # ==========================================
    results = []
    
    for date, group in df_sensor.groupby(df_sensor['일시'].dt.date):
        # 교집합에 있는 날짜만 분석
        if date not in common_dates:
            continue
            
        date_ts = pd.to_datetime(date)
        prod_row = df_prod[df_prod['일자'] == date_ts]
        
        charge_kg = prod_row.iloc[0]['장입량']
        
        # 문자열로 들어온 경우 숫자 변환 (예: "100,000" -> 100000)
        if isinstance(charge_kg, str):
            charge_kg = float(charge_kg.replace(',', ''))
            
        charge_ton = charge_kg / 1000
        
        if charge_ton <= 0: continue

        gas_start = group['가스지침'].min()
        gas_end = group['가스지침'].max()
        gas_used = gas_end - gas_start
        
        if gas_used <= 0: continue

        unit_cost = gas_used / charge_ton
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date_ts.strftime('%Y-%m-%d'),
            '장입량(kg)': int(charge_kg),
            '가스사용량(Nm3)': int(gas_used),
            '원단위(Nm3/ton)': round(unit_cost, 2),
            '목표(23%)': TARGET_UNIT_COST,
            '달성여부': '✅ PASS' if is_pass else '❌ FAIL'
        })
    
    return pd.DataFrame(results), df_sensor

# ... [이후 PDFReport 클래스 및 main 함수는 기존과 동일하게 유지] ...
