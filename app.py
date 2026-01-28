import streamlit as st
import pandas as pd
import requests
from google import genai
import json
import io
import re
import time
import os

# ==========================================
# 1. 설정 및 API 초기화
# ==========================================
ALADIN_TTB_KEY = st.secrets["ALADIN_TTB_KEY"]
GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
MODEL_ID = 'models/gemini-2.5-flash'

DEFAULT_KEYWORDS = (
    "마음, 용기, 행복, 사랑, 감정, 자신감, 정직, 약속, 나눔, 배려, 성장, 호기심, "
    "가족, 친구, 이웃, 유치원, 선생님, 협력, 인사, 예절, 함께, 도움, "
    "음식, 건강, 청결, 편식, 잠자기, 화장실, 안전, 옷 입기, 규칙, 생활습관, "
    "동물, 곤충, 바다, 숲, 식물, 계절, 날씨, 우주, 지구, 환경, 공룡, 과학, "
    "상상, 모험, 색깔, 소리, 그리기, 만들기, 음악, 마법, 옛이야기, 전통"
)

@st.cache_resource
def init_gemini_client():
    return genai.Client(api_key=GOOGLE_API_KEY)

client = init_gemini_client()

# ==========================================
# 2. 핵심 기능 함수
# ==========================================

def get_book_info_aladin(title, author=""):
    url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
    clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', str(title))
    query = f"{clean_title} {str(author).strip()}"
    params = {
        'ttbkey': ALADIN_TTB_KEY, 'Query': query, 'QueryType': 'Keyword',
        'MaxResults': 1, 'Output': 'js', 'SearchTarget': 'Book',
        'Version': '20131101', 'OptResult': 'Story,fulldescription'
    }
    try:
        response = requests.get(url, params=params, timeout=5)
        data = json.loads(response.text.strip().rstrip(';'))
        if 'item' in data and data['item']:
            item = data['item'][0]
            desc = f"{item.get('description', '')} {item.get('fullDescription', '')} {item.get('story', '')}"
            return {"isbn13": item.get('isbn13', '-'), "desc": re.sub(r'<[^>]*>', ' ', desc)}
    except: pass
    return None

def refine_with_gemini(book_data, title, keyword_pool):
    if not client or not book_data: return None
    prompt = f"""
    당신은 4~7세 도서 추천 시스템의 라벨러입니다. '{title}' 정보 기반으로 작업하세요.
    [규칙] 
    1. 줄거리: 다정한 선생님 말투, 3문장 내외, 마지막은 질문.
    2. 키워드: [표준 목록]에서 3개 필선택, 본문 소재 2개 선택 (총 5개).
    [표준 목록]: {keyword_pool}
    정보 원문: {book_data['desc'][:1000]}
    응답 형식(JSON): {{"summary": "내용", "keywords": ["k1", "k2", "k3", "k4", "k5"]}}
    """
    try:
        response = client.models.generate_content(model=MODEL_ID, contents=prompt)
        json_text = re.search(r'\{.*\}', response.text, re.DOTALL)
        return json.loads(json_text.group()) if json_text else None
    except Exception: return None

# ==========================================
# 3. 스트림릿 UI
# ==========================================
st.set_page_config(page_title="Book Spectrum v3.0", layout="wide")
st.title("🌈 북 스펙트럼 v3.0")

DAILY_MAX_LIMIT = 2500  # 하루 최대 분석 권수 (안전장치)

with st.sidebar:
    st.header("⚙️ 설정")
    user_keyword_list = st.text_area("표준 키워드 사전 관리", value=DEFAULT_KEYWORDS, height=200)
    st.divider()
    uploaded_file = st.file_uploader("원본 엑셀 업로드", type=["xlsx"])
    start_btn = st.button("🚀 분석 시작", type="primary", use_container_width=True)

if uploaded_file:
    # (세션 상태 초기화 부분 생략 - 기존과 동일)
    
    if start_btn:
        progress_bar = st.progress(0)
        analyzed_count = 0  # 이번 실행에서 분석한 권수 카운트
        
        for i, row in st.session_state.display_df.iterrows():
            # 이미 분석된 건 건너뜀
            if row['아이용 줄거리'] not in ["대기 중...", "검색 실패", "분석 실패"]:
                continue
            
            # 🛑 할당량 체크
            if analyzed_count >= DAILY_MAX_LIMIT:
                st.error(f"⚠️ 일일 분석 한도({DAILY_MAX_LIMIT}권)에 도달하여 중단합니다. 설정값은 코드에서 수정 가능합니다.")
                break
            
            title = str(row.get('도서명', '')).strip()
            author = str(row.get('저자', row.get('글쓴이', ''))).strip()
            
            # 분석 실행
            info = get_book_info_aladin(title, author)
            if info:
                st.session_state.display_df.at[i, 'ISBN13'] = info.get('isbn13')
                refined = refine_with_gemini(info, title, user_keyword_list)
                if refined:
                    st.session_state.display_df.at[i, '아이용 줄거리'] = refined.get('summary')
                    st.session_state.display_df.at[i, '추천 키워드'] = ", ".join(refined.get('keywords', []))
                    analyzed_count += 1  # 분석 성공 시 카운트 증가
                else:
                    st.session_state.display_df.at[i, '아이용 줄거리'] = "분석 실패"
            else:
                st.session_state.display_df.at[i, '아이용 줄거리'] = "검색 실패"
            
            # 테이블 업데이트 및 진행바
            table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)
            progress_bar.progress((i + 1) / len(st.session_state.display_df))
            time.sleep(0.5) # 유료 티어이므로 속도를 약간 높여도 됩니다 (기존 1초 -> 0.5초)

        st.success(f"✅ 분석 완료! (이번 세션에서 총 {analyzed_count}권 분석됨)")

    with tab2:
        st.subheader("📌 키워드 분포 및 도서 필터링")
        kw_series = st.session_state.display_df['추천 키워드'].dropna()
        all_keywords = []
        for kw_str in kw_series:
            if kw_str != "대기 중...":
                all_keywords.extend([k.strip() for k in kw_str.split(",")])
        
        if all_keywords:
            kw_counts = pd.Series(all_keywords).value_counts().reset_index()
            kw_counts.columns = ['키워드', '수량']
            
            col1, col2 = st.columns([1, 2])
            with col1:
                st.write("✅ 키워드 리스트 (필터링할 키워드를 선택하세요)")
                # 키워드 선택용 셀렉트박스 추가
                selected_keyword = st.selectbox("조회할 키워드 선택", ["전체 보기"] + list(kw_counts['키워드']))
                st.dataframe(kw_counts, use_container_width=True, height=300)
            
            with col2:
                st.write("📈 키워드 분포 차트")
                st.bar_chart(kw_counts.set_index('키워드').head(15))
            
            st.divider()
            
            # 필터링 결과 출력
            st.subheader(f"📖 '{selected_keyword}' 키워드 포함 도서 목록")
            if selected_keyword == "전체 보기":
                st.dataframe(st.session_state.display_df, use_container_width=True)
            else:
                filtered_df = st.session_state.display_df[
                    st.session_state.display_df['추천 키워드'].str.contains(selected_keyword, na=False)
                ]
                st.dataframe(filtered_df, use_container_width=True)
        else:
            st.info("분석이 완료되면 키워드 통계와 필터링 기능이 활성화됩니다.")

    # 다운로드 버튼
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        st.session_state.display_df.to_excel(writer, index=False)
    st.download_button("📥 최종 결과 엑셀 다운로드", data=output.getvalue(), file_name="Book_Spectrum_Final.xlsx", use_container_width=True)
