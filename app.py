# ---------------------------------------------------------
# 2. 데이터 처리 함수 (진단 모드 강화형)
# ---------------------------------------------------------
@st.cache_data
def load_and_process_data(sensor_files, prod_file):
    debug_logs = [] # 진단 로그 저장용

    # --- A. 생산 실적 로딩 ---
    try:
        df_prod = pd.read_excel(prod_file)
        df_prod.columns = [str(c).strip() for c in df_prod.columns]
        
        # 첫 번째=날짜, 두 번째=장입량
        col_date = df_prod.columns[0]
        col_weight = df_prod.columns[1]
        df_prod.rename(columns={col_date: '일자', col_weight: '장입량'}, inplace=True)
        
        # [수정] 날짜 및 숫자 강제 변환
        df_prod['일자'] = pd.to_datetime(df_prod['일자'], errors='coerce')
        # 콤마(,) 제거 후 숫자로 변환
        if df_prod['장입량'].dtype == object:
            df_prod['장입량'] = df_prod['장입량'].astype(str).str.replace(',', '')
        df_prod['장입량'] = pd.to_numeric(df_prod['장입량'], errors='coerce')
        
        df_prod = df_prod.dropna(subset=['일자'])
        
    except Exception as e:
        return None, f"생산 실적 파일 로딩 오류: {e}"

    # --- B. 가열로 데이터 로딩 ---
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
        # [중요] 컬럼 순서가 다르면 여기서 수정해야 합니다 (현재: 0=시간, 1=온도, 2=가스)
        df_sensor.rename(columns={cols[0]: '일시', cols[1]: '온도', cols[2]: '가스지침'}, inplace=True)
        
        # [수정] 데이터 강제 형변환 (숫자가 문자로 인식되는 것 방지)
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
    
    # 디버깅: 분석 과정 추적
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

        # 3. 판정
        unit_cost = gas_used / (charge_kg / 1000)
        is_pass = unit_cost <= TARGET_UNIT_COST
        
        results.append({
            '날짜': date_str,
            '검침시작': daily_sensor.iloc[0]['일시'].strftime('%Y-%m-%d %H:%M'),
            '검침완료': daily_sensor.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
            'Cycle종료': daily_sensor.iloc[-1]['일시'].strftime('%Y-%m-%d %H:%M'),
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
