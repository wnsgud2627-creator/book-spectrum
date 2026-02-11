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
# 0. 페이지 기본 설정 (가장 위에 와야 함)
# ==========================================
st.set_page_config(page_title="Book Spectrum v3.0", layout="wide")

# ==========================================
# 1. 로그인 기능 함수
# ==========================================
def login():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if not st.session_state.logged_in:
        st.subheader("🔒 관리자 인증이 필요합니다")
        password = st.text_input("비밀번호를 입력하세요", type="password")
        
        if st.button("로그인"):
            if password == "2300": # 설정하신 비밀번호
                st.session_state.logged_in = True
                st.rerun() 
            else:
                st.error("비밀번호가 틀렸습니다.")
        return False
    return True

# ==========================================
# 2. 메인 앱 실행 (로그인 성공 시)
# ==========================================
if login():
    # --- 설정 값 ---
    # 알라딘 키는 기존처럼 secrets에서 가져오거나 필요시 입력창으로 뺄 수 있습니다.
    ALADIN_TTB_KEY = st.secrets.get("ALADIN_TTB_KEY", "여기에_기본값_입력")
    MODEL_ID = 'models/gemini-2.0-flash' # 최신 모델명으로 업데이트# --- 화면 구성 (사이드바) ---
    with st.sidebar:
        st.header("⚙️ 설정")
        
        # [추가] Gemini API 키 입력창
        user_gemini_key = st.text_input("Gemini API Key 입력", type="password", help="Google AI Studio에서 발급받은 API 키를 입력하세요.")
        
        st.divider()
        user_keyword_list = st.text_area("표준 키워드 사전 관리", value=DEFAULT_KEYWORDS, height=200)
        st.divider()
        uploaded_file = st.file_uploader("원본 엑셀 업로드", type=["xlsx"])
        start_btn = st.button("🚀 분석 시작", type="primary", use_container_width=True)

    # --- 클라이언트 초기화 함수 ---
    def get_gemini_client():
        if not user_gemini_key:
            return None
        return genai.Client(api_key=user_gemini_key)

    # --- 내부 기능 함수 (줄거리 요약 및 키워드 추출) ---
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

    def refine_with_gemini(client, book_data, title, keyword_pool):
        if not client or not book_data: return None
        
        prompt = f"""
        당신은 4~7세 도서 추천 시스템의 전문 카피라이터입니다.
        '{title}'의 정보를 바탕으로 줄거리와 키워드를 생성하세요.

        [작업 1: 줄거리 요약]
        - 반드시 독립된 3문장, 각 문장 35자 이내로 작성하세요.
        - 모범 스타일: "빨간 코끼리의 길다란 코 위로 동물 친구들이 등장합니다. 코 위에서 벌어지는 유쾌한 이야기를 따라가며 숫자를 익혀요. 우리 함께 신나는 숫자 놀이를 시작해 볼까요?"

        [작업 2: 키워드 구성 - 명사형 5개]
        - 키워드 1, 2, 3: [표준 목록] 내 단어 선택
        - 키워드 4: 주인공 (이야기를 이끄는 핵심 화자)
        - 키워드 5: 주제 또는 핵심 소재 (이야기의 메시지나 주요 사건)

        [명작 동화 예시]
        - 예시 1 (무지개 물고기): ["인성", "친구", "행복", "물고기", "나눔"]
        - 예시 2 (백설공주): ["마음", "질투", "옛이야기", "공주", "사과"]
        - 예시 3 (강아지 똥): ["성장", "사랑", "생명", "강아지똥", "민들레"]
        
        [표준 목록]: {keyword_pool}
        정보 원문: {book_data['desc'][:1000]}
        
        응답 형식(JSON): 
        {{
          "summary": "1문장. 2문장. 3문장.",
          "keywords": ["표준1", "표준2", "표준3", "주인공", "주제_소재"]
        }}
        """
        try:
            response = client.models.generate_content(model=MODEL_ID, contents=prompt)
            json_text = re.search(r'\{.*\}', response.text, re.DOTALL)
            return json.loads(json_text.group()) if json_text else None
        except Exception: return None

    # --- 메인 화면 구성 ---
    st.title("🌈 AI 도서 데이터 분석기_v1.0")

    if uploaded_file:
        if 'display_df' not in st.session_state:
            raw_df = pd.read_excel(uploaded_file)
            for col in ['ISBN13', '아이용 줄거리', '추천 키워드']:
                if col not in raw_df.columns: raw_df[col] = "대기 중..."
            if '그린이' not in raw_df.columns: raw_df['그린이'] = ""
            st.session_state.display_df = raw_df

        tab1, tab2 = st.tabs(["📝 분석 현황", "📊 키워드 통계 및 필터"])

        with tab1:
            table_placeholder = st.empty()
            table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)

            if start_btn:
                # API 키 체크
                client = get_gemini_client()
                if not client:
                    st.error("⚠️ Gemini API Key를 입력해주세요!")
                else:
                    progress_bar = st.progress(0)
                    for i, row in st.session_state.display_df.iterrows():
                        if row['아이용 줄거리'] not in ["대기 중...", "검색 실패", "분석 실패"]: continue

                        title = str(row.get('도서명', '')).strip()
                        author = str(row.get('저자', row.get('글쓴이', ''))).strip()
                        info = get_book_info_aladin(title, author)

                        if info:
                            st.session_state.display_df.at[i, 'ISBN13'] = info.get('isbn13')
                            refined = refine_with_gemini(client, info, title, user_keyword_list)
                            if refined:
                                st.session_state.display_df.at[i, '아이용 줄거리'] = refined.get('summary')
                                st.session_state.display_df.at[i, '추천 키워드'] = ", ".join(refined.get('keywords', []))
                            else: st.session_state.display_df.at[i, '아이용 줄거리'] = "분석 실패"
                        else: st.session_state.display_df.at[i, '아이용 줄거리'] = "검색 실패"

                        table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)
                        progress_bar.progress((i + 1) / len(st.session_state.display_df))
                        time.sleep(1) # API 레이트 리밋 방지용
                    st.success("✅ 분석 완료!")

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
                    selected_keyword = st.selectbox("조회할 키워드 선택", ["전체 보기"] + list(kw_counts['키워드']))
                    st.dataframe(kw_counts, use_container_width=True, height=300)
                with col2:
                    st.bar_chart(kw_counts.set_index('키워드').head(15))
                
                st.divider()
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

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            st.session_state.display_df.to_excel(writer, index=False)
        st.download_button("📥 최종 결과 엑셀 다운로드", data=output.getvalue(), file_name="Book_Spectrum_Final.xlsx", use_container_width=True)
