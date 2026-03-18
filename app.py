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
st.set_page_config(page_title="Book Spectrum v5", layout="wide")

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
            if password == st.secrets["PASSWORD"]:
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
        "상상, 성장, 환경, 사랑, 용기, 모험, 호기심, 인성, 놀이, 행복, "
        "감정, 생활습관, 건강, 안전, 의사소통, 장래희망, 동물, 가족, 친구, 음식, "
        "지구, 과학, 공룡, 우주, 바다, 이웃, 전통, 색깔, 모양, 미술, "
        "도구, 자연, 계절, 운동, 학교, 곤충, 음악, 전래, 명작, 지식"

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

        # --- 사이드바 설정 부분에 추가 ---
        st.divider()
        default_categories = "필독서, 문학, 그림책, 역사인물, 수학과학, 사회경제, 학습교양, 백과, 학습만화, 잡지, 명작 동화, 전래 동화, 창작동화, 인물동화"
        user_category_list = st.text_area("도서 분류 사전", value=default_categories, height=100)
        
        st.divider()
        st.subheader("🎯 추출 항목")
        #ISBN 검색은 고정
        get_isbn = st.checkbox("ISBN13 추출(필수)", value=True, disabled=True)
        get_summary = st.checkbox("줄거리 생성", value=True)
        get_keywords = st.checkbox("키워드 추출", value=True)
        get_category = st.checkbox("도서 분류 추출", value=True)

        st.divider()
        st.subheader("📊 키워드 수량")
        total_kw_count = st.slider("총 키워드 수", 1, 10, 5)
        std_kw_count = st.slider("표준 키워드 포함 수", 0, total_kw_count, 2)
        
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
        if not (get_isbn or get_summary or get_keywords or get_category): return None
        
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

        # 3단계 시도: 실패 시 제목 + 출판사 (추가된 부분)
        if not result and clean_publisher:
            q3 = f"{clean_title} {clean_publisher}".strip()
            result = fetch_aladin(q3)
            
        return result
        
    def refine_with_gemini(book_data, title, keyword_pool, category_pool, std_n, total_n, age_group):
        if not (get_summary or get_keywords or get_category): return {"summary": "생략", "keywords": [], "category": "미선택"}
        extra_n = total_n - std_n
        
        if "유아" in age_group:
            persona, char_limit = "베터랑 어린이 도서 카피라이터이자 스토리텔", 35
        elif "초등" in age_group:
            persona, char_limit = "초등 교육 및 독서 지도 전문가", 50
        else:
            persona, char_limit = "중등 국어 교육 및 문학 분석가", 65

        prompt = f"""
        당신은 {persona}입니다. '{title}'의 정보를 바탕으로, {age_group}에게 제공할 줄거리와 키워드를 생성하세요.

        [작업 1: 줄거리 요약]
        1. **서술 방식**: 책의 주제를 설명하지 말고, **책의 첫 장면을 들려주듯 실감 나게** 서술하세요.
        2. **구체성 및 근거**: '음식' 대신 '누룽지'처럼 **[정보 원문]에 명시된 구체적인 단어**를 사용하세요. 
        3. **할루시네이션 방지**: **반드시 제공된 [정보 원문]의 내용 안에서만 작성**하세요. 원문에 없는 인물, 사건, 배경을 상상해서 추가하는 것은 절대 금지입니다.
        4. **감성적 터치**: 원문의 분위기를 바탕으로 아이가 느낄 감정(궁금함, 즐거움 등)을 문장에 녹여내세요.
        5. **마무리**: 마지막 문장은 "~어떤 일이 벌어질까요?" 또는 "~를 만나 보세요!"와 같이 **원문의 범위를 벗어나지 않는 선에서 궁금증을 유발**하며 끝내주세요.
        6. **형식**: 반드시 독립된 3문장, 각 {char_limit}자 이내, 전체 합계 {char_limit * 3}자 이내.
        7. **금지어**: "안녕", "친구들", "소개할게요", "이 책은", "알아볼까요" 절대 금지.

        [작업 2: 키워드 구성 - 총 {total_n}개 명사형 추출]
        1. **표준 키워드 ({std_n}개)**: [표준 목록]에서 가장 관련 깊은 단어 선택.
        2. **자유 키워드 ({extra_n}개)**: [정보 원문]에 등장하는 핵심 소재, 장소, 관계 위주로 추출.
        3. **자유 키워드 금지어**: "아이", "이야기", "자유" 등 추상적인 단어 사용 금지.
        4. **주의**: 반드시 **명사**여야 하며, 등장인물의 이름은 사용하지 마세요.
        5. **선정 기준 참고**: 
           - **장소/배경**: 숲, 마트, 지하철 등 (원문에 언급된 경우만)
           - **구체적 행동**: 숨바꼭질, 요리, 물놀이 등
           - **핵심 소재**: 떡, 기차, 우산 등        

        [작업 3: 도서 분류 선택] (선택여부: {get_category})
        1. 아래 제공된 **[도서 분류 목록]** 중에서 이 책에 가장 잘 어울리는 카테고리를 **딱 1개만** 선택하세요.
        2. 목록에 없는 새로운 분류를 만들지 마세요. 반드시 제공된 목록 내에서만 골라야 합니다.

        [표준 키워드 목록]: {keyword_pool}
        [도서 분류 목록]: {category_pool}
        정보 원문: {book_data['desc'][:1000]}
    
        응답 형식(JSON): 
        {{
          "summary": "1문장. 2문장. 3문장.",
          "keywords": ["키워드1", "키워드2", "...", "키워드{total_n}"],
          "category": "선택된 분류명"
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
            if get_category and '도서 분류' not in raw_df.columns: raw_df['도서 분류'] = "대기 중..."
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
                if get_category: check_cols.append('도서 분류')
                
                if all(row.get(c) not in ["대기 중...", "검색 실패", "분석 실패"] for c in check_cols):
                    continue

# --- [수정 2] 출판사, 글쓴이 데이터 전달 ---
                info = get_book_info_aladin(
                    title=row.get('도서명', ''),
                    publisher=row.get('출판사', ''),
                    author=row.get('글쓴이', row.get('저자', ''))
                )
                if info:
                    # 1. ISBN은 무조건 기록 (성공의 증거)
                    st.session_state.display_df.at[i, 'ISBN13'] = info.get('isbn13')
                    
                    # 2. 줄거리나 키워드가 하나라도 체크된 경우에만 Gemini 호출
                    if get_summary or get_keywords or get_category:
                        refined = refine_with_gemini(info, row.get('도서명'), user_keyword_list, user_category_list, std_kw_count, total_kw_count, age_group)
                        
                        if refined:
                            if get_summary: 
                                st.session_state.display_df.at[i, '아이용 줄거리'] = refined.get('summary')
                            if get_keywords: 
                                st.session_state.display_df.at[i, '추천 키워드'] = ", ".join(refined.get('keywords', []))
                            if get_category: 
                            # 제미나이가 리턴한 JSON 데이터에서 'category' 값을 가져와서 표에 넣음
                                st.session_state.display_df.at[i, '도서 분류'] = refined.get('category')
                        else:
                            # Gemini 분석 자체가 실패한 경우
                            if get_summary: st.session_state.display_df.at[i, '아이용 줄거리'] = "분석 실패"
                            if get_keywords: st.session_state.display_df.at[i, '추천 키워드'] = "분석 실패"
                            if get_category: st.session_state.display_df.at[i, '도서 분류'] = "분석 실패"
                    else:
                        # 줄거리/키워드를 아예 체크 안 한 경우 (빈칸 유지 또는 완료 처리)
                        if get_summary: st.session_state.display_df.at[i, '아이용 줄거리'] = "제외됨"
                        if get_keywords: st.session_state.display_df.at[i, '추천 키워드'] = "제외됨"
                        if get_category: st.session_state.display_df.at[i, '도서 분류'] = "제외됨"

                else:
                    # 알라딘 검색 자체가 실패한 경우
                    if get_isbn: st.session_state.display_df.at[i, 'ISBN13'] = "검색 실패"
                    if get_summary: st.session_state.display_df.at[i, '아이용 줄거리'] = "검색 실패"
                    if get_keywords: st.session_state.display_df.at[i, '추천 키워드'] = "검색 실패"
                    if get_category: st.session_state.display_df.at[i, '도서 분류'] = "검색 실패"
                
                table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)
                progress_bar.progress((i + 1) / total)
                time.sleep(1) # 유료 등급이지만 안정성을 위해 1초 유지

            st.success("✅ 분석 완료!")

        # 다운로드 버튼
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            st.session_state.display_df.to_excel(writer, index=False)
        st.download_button("📥 최종 결과 다운로드", data=output.getvalue(), file_name=f"Result_{age_group}.xlsx", use_container_width=True)
