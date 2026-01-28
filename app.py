import streamlit as st
import pandas as pd
import requests
from google import genai
import json
import io
import re
import time

# ==========================================
# 1. 설정 및 API 초기화
# ==========================================
ALADIN_TTB_KEY = st.secrets["ALADIN_TTB_KEY"]
GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
MODEL_ID = 'gemini-1.5-flash' 

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
    1. 줄거리: 다정한 선생님 말투, 3문장 내외, 마지막은 질문.
    2. 키워드: [표준 목록]에서 3개 선택, 본문 소재 2개 선택 (총 5개).
    [표준 목록]: {keyword_pool}
    정보 원문: {book_data['desc'][:1000]}
    응답 형식(JSON): {{"summary": "내용", "keywords": ["k1", "k2", "k3", "k4", "k5"]}}
    """
    try:
        response = client.models.generate_content(model=MODEL_ID, contents=prompt)
        json_text = re.search(r'\{.*\}', response.text, re.DOTALL)
        return json.loads(json_text.group()) if json_text else None
    except: return None

# ==========================================
# 3. 스트림릿 UI
# ==========================================
st.set_page_config(page_title="Book Spectrum", layout="wide")
st.title("🌈 북 스펙트럼 (심플 버전)")

with st.sidebar:
    st.header("⚙️ 설정")
    user_keyword_list = st.text_area("키워드 사전", value=DEFAULT_KEYWORDS, height=200)
    uploaded_file = st.file_uploader("엑셀 업로드", type=["xlsx"])
    start_btn = st.button("🚀 분석 시작")

if uploaded_file:
    # 데이터 로드
    if 'df' not in st.session_state:
        st.session_state.df = pd.read_excel(uploaded_file)
        for col in ['ISBN13', '아이용 줄거리', '추천 키워드']:
            if col not in st.session_state.df.columns:
                st.session_state.df[col] = "대기 중..."

    table_placeholder = st.empty()
    table_placeholder.dataframe(st.session_state.df)

    if start_btn:
        progress_bar = st.progress(0)
        for i, row in st.session_state.df.iterrows():
            if row['아이용 줄거리'] != "대기 중...": continue
            
            title = str(row.get('도서명', ''))
            author = str(row.get('저자', ''))
            
            info = get_book_info_aladin(title, author)
            if info:
                st.session_state.df.at[i, 'ISBN13'] = info['isbn13']
                refined = refine_with_gemini(info, title, user_keyword_list)
                if refined:
                    st.session_state.df.at[i, '아이용 줄거리'] = refined['summary']
                    st.session_state.df.at[i, '추천 키워드'] = ", ".join(refined['keywords'])
            
            table_placeholder.dataframe(st.session_state.df)
            progress_bar.progress((i + 1) / len(st.session_state.df))
        
        st.success("분석 완료!")
        
        # 다운로드 버튼
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            st.session_state.df.to_excel(writer, index=False)
        st.download_button("📥 결과 다운로드", data=output.getvalue(), file_name="result.xlsx")
