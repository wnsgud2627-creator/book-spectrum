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
# 0. 페이지 기본 설정
# ==========================================
st.set_page_config(page_title="Book Spectrum v4.7", layout="wide")

# ==========================================
# 1. 로그인 기능
# ==========================================
def login():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if not st.session_state.logged_in:
        st.subheader("🔒 관리자 인증")
        password = st.text_input("비밀번호 입력", type="password")
        if st.button("로그인"):
            if password == "2300":
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")
        return False
    return True

# ==========================================
# 2. 메인 앱 로직
# ==========================================
if login():
    ALADIN_TTB_KEY = st.secrets["ALADIN_TTB_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    MODEL_ID = 'models/gemini-2.5-flash'

    DEFAULT_KEYWORDS = (
        "마음, 용기, 행복, 사랑, 감정, 자신감, 인성, 약속, 성장, 호기심, "
        "가족, 친구, 이웃, 유치원, 선생님, 예절, 도움, 음식, 건강, 생활습관, "
        "잠자기, 화장실, 안전, 동물, 곤충, 바다, 식물, 계산, 날씨, 우주, "
        "지구, 환경, 공룡, 과학, 상상, 모험, 색깔, 소리, 미술, 음악, "
        "마법, 옛이야기, 전통, 장래희망, 공주, 학교, 숫자, 의사소통, 모양, 수학, "
        "생일, 한글, 운동, 우리나라, 탈것, 세계 여러 나라, 놀이, 도구, 옷, 책"
    )

    @st.cache_resource
    def init_gemini_client():
        return genai.Client(api_key=GOOGLE_API_KEY)

    client = init_gemini_client()

    # --- 사이드바 설정 ---
    with st.sidebar:
        st.header("⚙️ 분석 설정")
        age_group = st.radio("📚 대상 연령대", ["유아 (4~7세)", "초등 (8~13세)", "중등 (14~16세)"], index=0)
        
        st.divider()
        user_keyword_list = st.text_area("표준 키워드 사전", value=DEFAULT_KEYWORDS, height=150)
        
        st.divider()
        st.subheader("🎯 추출 항목")
        get_isbn = st.checkbox("ISBN13 추출", value=True)
        get_summary = st.checkbox("줄거리 생성", value=True)
        get_keywords = st.checkbox("키워드 추출", value=True)

        st.divider()
        st.subheader("📊 키워드 수량")
        total_kw_count = st.slider("총 키워드 수", 1, 10, 5)
        std_kw_count = st.slider("표준 키워드 포함 수", 0, total_kw_count, 3)
        
        st.divider()
        uploaded_file = st.file_uploader("엑셀 업로드", type=["xlsx"])
        start_btn = st.button("🚀 분석 시작", type="primary", use_container_width=True)

# --- [수정 1] 단계별 검색 함수 ---
    def fetch_aladin(query):
        url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
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

    def get_book_info_aladin(title, publisher="", author=""):
        if not (get_isbn or get_summary or get_keywords): return None
        
        # 정보 정제 (괄호 제거 및 군더더기 제거)
        clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', str(title)).strip()
        clean_author = re.sub(r'(글|그림|저|역|편저|외|지음|옮김).*$', '', str(author)).strip()
        clean_publisher = str(publisher).strip()
        
        # 1단계 시도: 제목 + 출판사 + 저자 (기울어 한림 이탁근)
        q1 = f"{clean_title} {clean_publisher} {clean_author}".strip()
        result = fetch_aladin(q1)
        
        # 2단계 시도: 실패 시 제목 + 저자 (기울어 이탁근)
        if not result:
            q2 = f"{clean_title} {clean_author}".strip()
            result = fetch_aladin(q2)
            
        return result
        
    def refine_with_gemini(book_data, title, keyword_pool, std_n, total_n, age_group):
        if not (get_summary or get_keywords): return {"summary": "생략", "keywords": []}
        extra_n = total_n - std_n
        
        if "유아" in age_group:
            persona, char_limit = "4~7세 도서 추천 시스템의 전문 카피라이터", 35
        elif "초등" in age_group:
            persona, char_limit = "초등 교육 및 독서 지도 전문가", 50
        else:
            persona, char_limit = "중등 국어 교육 및 문학 분석가", 65

        prompt = f"""
        당신은 {persona}입니다. '{title}'의 정보를 바탕으로 줄거리와 키워드를 생성하세요.

        [작업 1: 줄거리 요약]
        1. **반드시 독립된 3문장**으로 작성하세요.
        2. **전체 합계 글자 수**: 띄어쓰기 포함 **총 {char_limit * 3}자 이내**로 간결하게 작성하세요.
        3. **글자 수 제한**: 각 문장은 띄어쓰기 포함 **{char_limit}자 이내**로 작성하세요.
        4. **금지어**: "안녕", "친구들", "소개할게요", "이 책은", "알아볼까요" 절대 금지.

        [작업 2: 키워드 구성 - 총 {total_n}개 명사형 추출]
        1. **표준 키워드 ({std_n}개)**: 아래 [표준 목록]에서 가장 관련 깊은 단어 선택.
        2. **자유 키워드 ({extra_n}개)**: 주인공(인물/동물/사물), 핵심 소재, 주제어 중 중요도 순으로 추출.
        3. **주의**: 모든 키워드는 반드시 **명사**여야 합니다.
        
        [키워드 추출 예시 기준]
        - 무지개 물고기: ["인성", "친구", "행복", "물고기", "나눔"]
        
        [표준 목록]: {keyword_pool}
        정보 원문: {book_data['desc'][:1000]}
        
        응답 형식(JSON): 
        {{
          "summary": "1문장. 2문장. 3문장.",
          "keywords": ["키워드1", "키워드2", "...", "키워드{total_n}"]
        }}
        """
        try:
            response = client.models.generate_content(model=MODEL_ID, contents=prompt)
            json_text = re.search(r'\{.*\}', response.text, re.DOTALL)
            return json.loads(json_text.group()) if json_text else None
        except: return None

    # --- 메인 화면 실행 ---
    st.title("🌈 도서 데이터 분석기 v4.7")

    if uploaded_file:
        # [해결] 새 파일 업로드 시 세션 초기화 로직
        if "current_file" not in st.session_state or st.session_state.current_file != uploaded_file.name:
            raw_df = pd.read_excel(uploaded_file)
            if get_isbn and 'ISBN13' not in raw_df.columns: raw_df['ISBN13'] = "대기 중..."
            if get_summary and '아이용 줄거리' not in raw_df.columns: raw_df['아이용 줄거리'] = "대기 중..."
            if get_keywords and '추천 키워드' not in raw_df.columns: raw_df['추천 키워드'] = "대기 중..."
            st.session_state.display_df = raw_df
            st.session_state.current_file = uploaded_file.name

        table_placeholder = st.empty()
        table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)

        if start_btn:
            progress_bar = st.progress(0)
            total = len(st.session_state.display_df)
            
            for i, row in st.session_state.display_df.iterrows():
                # 건너뛰기 조건 체크
                check_cols = []
                if get_isbn: check_cols.append('ISBN13')
                if get_summary: check_cols.append('아이용 줄거리')
                if get_keywords: check_cols.append('추천 키워드')
                
                if all(row.get(c) not in ["대기 중...", "검색 실패", "분석 실패"] for c in check_cols):
                    continue

# --- [수정 2] 출판사, 글쓴이 데이터 전달 ---
                info = get_book_info_aladin(
                    title=row.get('도서명', ''),
                    publisher=row.get('출판사', ''),
                    author=row.get('글쓴이', row.get('저자', ''))
                )
                if info:
                    if get_isbn: st.session_state.display_df.at[i, 'ISBN13'] = info.get('isbn13')
                    refined = refine_with_gemini(info, row.get('도서명'), user_keyword_list, std_kw_count, total_kw_count, age_group)
                    if refined:
                        if get_summary: st.session_state.display_df.at[i, '아이용 줄거리'] = refined.get('summary')
                        if get_keywords: st.session_state.display_df.at[i, '추천 키워드'] = ", ".join(refined.get('keywords', []))
                    else:
                        if get_summary: st.session_state.display_df.at[i, '아이용 줄거리'] = "분석 실패"
                else:
                    if get_summary: st.session_state.display_df.at[i, '아이용 줄거리'] = "검색 실패"
                
                table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)
                progress_bar.progress((i + 1) / total)
                time.sleep(1) # 유료 등급이지만 안정성을 위해 1초 유지

            st.success("✅ 분석 완료!")

        # 다운로드 버튼
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            st.session_state.display_df.to_excel(writer, index=False)
        st.download_button("📥 최종 결과 다운로드", data=output.getvalue(), file_name=f"Result_{age_group}.xlsx", use_container_width=True)
