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
    "마음, 용기, 행복, 사랑, 감정, 자신감, 인성, 약속, 성장, 호기심, "
    "가족, 친구, 이웃, 유치원, 선생님, 예절, 도움, 음식, 건강, 생활습관, "
    "잠자기, 화장실, 안전, 동물, 곤충, 바다, 식물, 계절, 날씨, 우주, "
    "지구, 환경, 공룡, 과학, 상상, 모험, 색깔, 소리, 미술, 음악, "
    "마법, 옛이야기, 전통, 장래희망, 공주, 학교, 숫자, 의사소통, 모양, 수학, "
    "생일, 한글, 운동, 우리나라, 탈것, 세계 여러 나라, 놀이, 도구, 옷, 책"
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
    당신은 4~7세 도서 추천 시스템의 전문 카피라이터입니다.
    '{title}'의 정보를 바탕으로 줄거리와 키워드를 생성하세요.

    [작업 1: 줄거리 요약 - 최상위 규정]
    1. **반드시 독립된 3문장**으로 작성하세요.
    2. **글자 수 제한**: 각 문장은 띄어쓰기 포함 **35자 이내**로 짧고 명확하게 끊으세요.
    3. **금지어**: "안녕", "친구들", "소개할게요", "이 책은" 절대 금지.
    4. **모범 답안(이 스타일을 완벽히 복제하세요)**:
       "빨간 코끼리의 길다란 코 위로 개성 넘치는 동물 친구들이 하나둘 등장합니다. 
       기다란 코 위에서 벌어지는 동물들의 유쾌한 이야기를 따라가며 저절로 숫자를 익힐 수 있을 거예요. 
       동물 친구들과 함께 신나는 숫자 세기 놀이에 참여해 볼까요?"

    [작업 2: 키워드 구성 - 5개 명사형 강제]
    1. **키워드 1, 2, 3 (표준)**: 아래 [표준 목록]에서 가장 관련 깊은 단어 **3개** 선택.
    2. **키워드 4 (주인공)**: 책의 주인공(예: 고양이, 의사, 꼬마 유령 등)을 나타내는 **핵심 명사 1개**.
    3. **키워드 5 (주제 및 소재)**: 줄거리를 관통하는 가장 중요한 **소재나 주제어(메시지)** 1개. 
       - 예: 거짓말, 이사, 용기(표준에 없을 시), 우정, 캠핑, 생일선물 등
    4. **주의**: 모든 키워드는 반드시 **명사**여야 합니다.
    5. **추출 예시**:
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
# ==========================================
# 3. 스트림릿 UI
# ==========================================
st.set_page_config(page_title="Book Spectrum v3.0", layout="wide")
st.title("🌈 북 스펙트럼 v3.0")

with st.sidebar:
    st.header("⚙️ 설정")
    user_keyword_list = st.text_area("표준 키워드 사전 관리", value=DEFAULT_KEYWORDS, height=200)
    st.divider()
    uploaded_file = st.file_uploader("원본 엑셀 업로드", type=["xlsx"])
    start_btn = st.button("🚀 분석 시작", type="primary", use_container_width=True)

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
            progress_bar = st.progress(0)
            for i, row in st.session_state.display_df.iterrows():
                if row['아이용 줄거리'] not in ["대기 중...", "검색 실패", "분석 실패"]: continue

                title = str(row.get('도서명', '')).strip()
                author = str(row.get('저자', row.get('글쓴이', ''))).strip()
                info = get_book_info_aladin(title, author)

                if info:
                    st.session_state.display_df.at[i, 'ISBN13'] = info.get('isbn13')
                    refined = refine_with_gemini(info, title, user_keyword_list)
                    if refined:
                        st.session_state.display_df.at[i, '아이용 줄거리'] = refined.get('summary')
                        st.session_state.display_df.at[i, '추천 키워드'] = ", ".join(refined.get('keywords', []))
                    else: st.session_state.display_df.at[i, '아이용 줄거리'] = "분석 실패"
                else: st.session_state.display_df.at[i, '아이용 줄거리'] = "검색 실패"

                table_placeholder.dataframe(st.session_state.display_df, use_container_width=True)
                progress_bar.progress((i + 1) / len(st.session_state.display_df))
                time.sleep(1)
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
