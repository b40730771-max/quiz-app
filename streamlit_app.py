import streamlit as st
import anthropic
import base64
import json
import datetime
import hashlib
import requests

# ── 페이지 설정
st.set_page_config(page_title="AI 퀴즈 생성기", page_icon="📝", layout="centered")

# ── Supabase REST API 헬퍼
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def sb_select(table, filters=None, order=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}?select=*"
    if filters:
        for k, v in filters.items():
            url += f"&{k}=eq.{v}"
    if order:
        url += f"&order={order}.desc"
    res = requests.get(url, headers=sb_headers())
    return res.json()

def sb_insert(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    res = requests.post(url, headers=sb_headers(), json=data)
    return res.json()

# ── Anthropic 클라이언트
client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

# ── 세션 초기화
defaults = {"user": None, "quiz": None, "answers": {}, "result": None,
            "page": "login", "hist_detail": None, "file_name": "",
            "difficulty": "개념", "types": ["단답형"]}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── 유틸
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def encode_file(f):
    f.seek(0)
    return base64.b64encode(f.read()).decode("utf-8")

def score_color(s):
    if s >= 80: return "🟢"
    if s >= 50: return "🟡"
    return "🔴"

def generate_quiz(file_data, file_type, difficulty, types, count):
    type_desc = ", ".join(types)
    diff_desc = "기본 개념과 정의를 묻는" if difficulty == "개념" else "개념을 응용하고 분석하는"
    
    system_prompt = '당신은 교육용 퀴즈 생성 전문가입니다. 반드시 JSON만 출력하세요. {"title":"제목","keywords":["키워드"],"questions":[{"id":1,"type":"단답형 또는 서술형","question":"문제","answer":"모범답안","keywords":["채점키워드"],"explanation":"해설"}]}'
    
    if file_type == "application/pdf":
        source_item = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": file_data}
        }
    else:
        mime_type = file_type if file_type != "image/jpg" else "image/jpeg"
        source_item = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": file_data}
        }

    messages = [
        {
            "role": "user",
            "content": [
                source_item,
                {"type": "text", "text": f"위 문서를 분석하여 {diff_desc} {type_desc} 문제를 정확히 {count}개 만들어주세요. 난이도: {difficulty}"}
            ]
        }
    ]

    try:
        response = client.beta.messages.create(
            model="claude-3-5-sonnet-20241022",
            betas=["pdfs-2024-09-25"], 
            max_tokens=4000,
            system=system_prompt,
            messages=messages
        )
        # 중요: response.content.text로 수정
        raw_text = response.content.text
        json_string = raw_text.replace("```json", "").replace("```", "").strip()
        return json.loads(json_string)
    except Exception as e:
        st.error(f"퀴즈 생성 중 오류가 발생했습니다: {str(e)}")
        return None

def grade_quiz(quiz, answers):
    grading_data = [{"id": q["id"], "question": q["question"], "type": q["type"], "answer": q["answer"], "keywords": q["keywords"], "userAnswer": answers.get(q["id"], "")} for q in quiz["questions"]]
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022", max_tokens=2000,
        system='채점 전문가입니다. JSON만 출력하세요. {"scores":[{"id":1,"score":0~100,"matched_keywords":["키워드"],"feedback":"피드백"}],"total":0~100}',
        messages=[{"role": "user", "content": f"채점:\n{json.dumps(grading_data, ensure_ascii=False)}"}]
    )
    return json.loads(response.content.text.replace("```json", "").replace("```", "").strip())

# ... (save_result, load_history 함수는 동일하므로 생략 가능하나 구조상 유지)
def save_result(result):
    sb_insert("quiz_history", {
        "user_id": st.session_state.user["id"],
        "title": result["quiz"]["title"],
        "file_name": result["file_name"],
        "difficulty": result["difficulty"],
        "types": result["types"],
        "quiz": result["quiz"],
        "answers": result["answers"],
        "grading": result["grading"],
        "total_score": round(result["grading"]["total"]),
        "created_at": result["date"]
    })

def load_history():
    return sb_select("quiz_history", filters={"user_id": st.session_state.user["id"]}, order="created_at")

# ── 페이지 함수들
def page_login():
    st.title("📝 AI 퀴즈 생성기")
    st.caption("로그인하고 나만의 퀴즈 기록을 관리하세요")
    st.divider()
    mode = st.radio("", ["로그인", "회원가입"], horizontal=True, label_visibility="collapsed")

    if mode == "로그인":
        with st.form("login_form"):
            email = st.text_input("이메일")
            pw = st.text_input("비밀번호", type="password")
            if st.form_submit_button("로그인", use_container_width=True):
                rows = sb_select("users", filters={"email": email, "password": hash_pw(pw)})
                if rows:
                    st.session_state.user = rows
                    st.session_state.page = "generate"
                    st.rerun()
                else:
                    st.error("이메일 또는 비밀번호가 올바르지 않아요.")
    else:
        with st.form("signup_form"):
            name = st.text_input("이름")
            email = st.text_input("이메일")
            pw = st.text_input("비밀번호", type="password")
            pw2 = st.text_input("비밀번호 확인", type="password")
            if st.form_submit_button("회원가입", use_container_width=True):
                if pw != pw2: st.error("비밀번호가 일치하지 않아요.")
                elif not name or not email: st.error("이름과 이메일을 모두 입력해 주세요.")
                else:
                    existing = sb_select("users", filters={"email": email})
                    if existing: st.error("이미 사용 중인 이메일이에요.")
                    else:
                        sb_insert("users", {"name": name, "email": email, "password": hash_pw(pw)})
                        st.success("가입 완료! 로그인해 주세요.")

def page_generate():
    # 1. 로그인 확인 안전장치 (함수 바로 아래에 들여쓰기 필수)
    if "user" not in st.session_state or st.session_state.user is None:
        st.session_state.page = "login"
        st.rerun()
        return

    user = st.session_state.user
    
    # 2. st.columns에 반드시 인자()를 넣으세요! (비워두면 에러 발생)
    col1, col2 = st.columns(2) 
    
    with col1:
        st.title("📝 AI 퀴즈 생성기")
        st.caption(f"안녕하세요, {user['name']}님!")
    
    with col2:
        if st.button("로그아웃"):
            for k in ["user", "quiz", "answers", "result", "hist_detail"]:
                st.session_state[k] = None
            st.session_state.page = "login"
            st.rerun()

    # 탭 생성
    tab1, tab2 = st.tabs(["퀴즈 생성", "히스토리"])

    with tab1:
        uploaded = st.file_uploader("PDF 또는 이미지 업로드", type=["pdf", "jpg", "jpeg", "png"])
        
        col_d, col_t = st.columns(2)
        with col_d:
            difficulty = st.radio("난이도", ["개념", "응용"], horizontal=True)
        with col_t:
            types = st.multiselect("문제 유형", ["단답형", "서술형"], default=["단답형"])
            
        count = st.slider("문제 수", 3, 20, 5)

        if st.button("🚀 퀴즈 생성하기", disabled=not uploaded or not types):
            with st.spinner("AI가 문제를 생성하고 있어요..."):
                file_data = encode_file(uploaded)
                quiz = generate_quiz(file_data, uploaded.type, difficulty, types, count)
                
                if quiz:
                    st.session_state.quiz = quiz
                    st.session_state.answers = {}
                    st.session_state.file_name = uploaded.name
                    st.session_state.difficulty = difficulty
                    st.session_state.types = types
                    st.session_state.page = "taking"
                    st.rerun()
                else:
                    st.error("퀴즈 생성에 실패했습니다.")

    with tab2:
        history = load_history()
        if not history:
            st.info("아직 저장된 퀴즈 기록이 없어요.")
        else:
            for i, h in enumerate(history):
                with st.container(border=True):
                    # 여기도 인자를로 주면 더 예쁘게 나옵니다.
                    c1, c2 = st.columns() 
                    with c1:
                        st.markdown(f"**{h['title']}**")
                        st.caption(f"{h['created_at']} · {h['file_name']}")
                    with c2:
                        total = h['total_score']
                        st.markdown(f"### {score_color(total)} {total}")
                    
                    if st.button("다시 보기", key=f"hist_{i}"):
                        st.session_state.hist_detail = h
                        st.session_state.page = "hist_detail"
                        st.rerun()
def page_taking():
    if not st.session_state.quiz:
        st.session_state.page = "generate"
        st.rerun()
    quiz = st.session_state.quiz
    st.title(f"📄 {quiz['title']}")
    if st.button("← 다시 생성"):
        st.session_state.page = "generate"
        st.rerun()
    
    answers = {}
    for q in quiz["questions"]:
        with st.container(border=True):
            st.markdown(f"**Q. {q['question']}**")
            if q["type"] == "단답형":
                answers[q["id"]] = st.text_input("답", key=f"ans_{q['id']}", label_visibility="collapsed")
            else:
                answers[q["id"]] = st.text_area("답", key=f"ans_{q['id']}", label_visibility="collapsed")
    
    if st.button("✅ 제출 및 채점하기"):
        with st.spinner("채점 중..."):
            grading = grade_quiz(quiz, answers)
            result = {
                "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "file_name": st.session_state.file_name,
                "difficulty": st.session_state.difficulty,
                "types": st.session_state.types,
                "quiz": quiz,
                "answers": answers,
                "grading": grading
            }
            save_result(result)
            st.session_state.result = result
            st.session_state.page = "result"
            st.rerun()

def page_result(res):
    if st.button("← 뒤로"):
        st.session_state.page = "generate"
        st.rerun()
    g = res["grading"]
    total = round(g["total"])
    st.metric("총점", f"{score_color(total)} {total}점")
    
    for i, q in enumerate(res["quiz"]["questions"]):
        sc = next((s for s in g["scores"] if s["id"] == q["id"]), {})
        with st.container(border=True):
            st.markdown(f"**Q{i+1}. {q['question']}** ({sc.get('score', 0)}점)")
            st.info(f"내 답변: {res['answers'].get(q['id']) or '미작성'}")
            with st.expander("해설 보기"):
                st.write(f"정답: {q['answer']}")
                st.write(f"설명: {q['explanation']}")
                if sc.get("feedback"): st.caption(f"피드백: {sc['feedback']}")

# ── 라우터
p = st.session_state.page
if p == "login": page_login()
elif p == "generate": page_generate()
elif p == "taking": page_taking()
elif p == "result": page_result(st.session_state.result)
elif p == "hist_detail": page_result(st.session_state.hist_detail)
