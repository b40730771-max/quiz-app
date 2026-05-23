import streamlit as st
import base64
import json
import datetime
import hashlib
import requests

# ── 페이지 설정
st.set_page_config(page_title="AI 퀴즈 생성기", page_icon="📝", layout="centered")

# ── Supabase 헬퍼
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
GROQ_KEY     = st.secrets["GROQ_API_KEY"]

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
    return res.json() if res.ok else []

def sb_insert(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    res = requests.post(url, headers=sb_headers(), json=data)
    return res.json()

# ── Gemini API 호출
def groq_text(prompt):
    """텍스트 전용 Groq 호출"""
    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 4000}
    )
    data = res.json()
    if "choices" not in data:
        st.error(f"Groq 오류: {data.get('error', {}).get('message', str(data))}")
        st.stop()
    return data["choices"][0]["message"]["content"]

def extract_pdf_text(file_data):
    """PDF를 텍스트로 추출"""
    import base64, io
    try:
        import pdfplumber
        raw = base64.b64decode(file_data)
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        st.error(f"PDF 텍스트 추출 실패: {e}")
        st.stop()

def extract_image_text(file_data, file_type):
    """이미지에서 Groq vision으로 텍스트 추출"""
    res = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{file_type};base64,{file_data}"}},
                {"type": "text", "text": "이 이미지의 모든 텍스트와 내용을 상세히 추출해주세요."}
            ]}],
            "max_tokens": 4000
        }
    )
    data = res.json()
    if "choices" not in data:
        st.error(f"이미지 분석 오류: {data.get('error', {}).get('message', str(data))}")
        st.stop()
    return data["choices"][0]["message"]["content"]

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
    if file_type == "application/pdf":
        content_text = extract_pdf_text(file_data)
    else:
        content_text = extract_image_text(file_data, file_type)

    # 추출된 텍스트 확인
    if not content_text or len(content_text.strip()) < 50:
        st.error(f"PDF에서 텍스트를 추출하지 못했어요. 추출된 내용: {content_text[:200]}")
        st.stop()

    st.info(f"📄 추출된 텍스트 미리보기 (앞 300자):\n{content_text[:300]}")

    prompt = f"""아래 문서 내용만을 바탕으로 {diff_desc} {type_desc} 문제를 정확히 {count}개 만들어주세요. 난이도: {difficulty}
절대 문서 내용과 무관한 문제를 만들지 마세요. 반드시 JSON만 출력하세요.
{{"title":"퀴즈 제목","keywords":["핵심키워드"],"questions":[{{"id":1,"type":"단답형 또는 서술형","question":"문제","answer":"모범답안","keywords":["채점키워드"],"explanation":"해설"}}]}}

===문서 내용===
{content_text[:8000]}
===끝==="""
    text = groq_text(prompt)
    return json.loads(text.replace("```json", "").replace("```", "").strip())

def grade_quiz(quiz, answers):
    grading_data = [{"id": q["id"], "question": q["question"], "type": q["type"],
                     "answer": q["answer"], "keywords": q["keywords"],
                     "userAnswer": answers.get(q["id"], "")} for q in quiz["questions"]]
    prompt = f"""다음 답안을 채점해주세요. 키워드 포함 여부로 부분 점수를 부여하세요.
반드시 JSON만 출력하세요.
{{"scores":[{{"id":1,"score":0~100,"matched_keywords":["키워드"],"feedback":"피드백"}}],"total":0~100}}

{json.dumps(grading_data, ensure_ascii=False)}"""
    text = groq_text(prompt)
    return json.loads(text.replace("```json", "").replace("```", "").strip())

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

# ══════════════════════════════════════════════════
# 페이지 함수들
# ══════════════════════════════════════════════════

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
                    st.session_state.user = rows[0]
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
                if pw != pw2:
                    st.error("비밀번호가 일치하지 않아요.")
                elif not name or not email:
                    st.error("이름과 이메일을 모두 입력해 주세요.")
                else:
                    existing = sb_select("users", filters={"email": email})
                    if existing:
                        st.error("이미 사용 중인 이메일이에요.")
                    else:
                        sb_insert("users", {"name": name, "email": email, "password": hash_pw(pw)})
                        st.success("가입 완료! 로그인해 주세요.")


def page_generate():
    user = st.session_state.user
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("📝 AI 퀴즈 생성기")
        st.caption(f"안녕하세요, {user['name']}님!")
    with col2:
        if st.button("로그아웃"):
            for k in ["user", "quiz", "answers", "result", "hist_detail"]:
                st.session_state[k] = None
            st.session_state.page = "login"
            st.rerun()

    tab1, tab2 = st.tabs(["퀴즈 생성", "히스토리"])
    with tab1:
        uploaded = st.file_uploader("PDF 또는 이미지 업로드", type=["pdf", "jpg", "jpeg", "png"])
        col1, col2 = st.columns(2)
        with col1:
            difficulty = st.radio("난이도", ["개념", "응용"], horizontal=True)
        with col2:
            types = st.multiselect("문제 유형", ["단답형", "서술형"], default=["단답형"])
        count = st.slider("문제 수", 3, 20, 5)
        if st.button("🚀 퀴즈 생성하기", disabled=not uploaded or not types):
            with st.spinner("AI가 문제를 생성하고 있어요..."):
                file_data = encode_file(uploaded)
                quiz = generate_quiz(file_data, uploaded.type, difficulty, types, count)
                st.session_state.quiz = quiz
                st.session_state.answers = {}
                st.session_state.file_name = uploaded.name
                st.session_state.difficulty = difficulty
                st.session_state.types = types
                st.session_state.page = "taking"
                st.rerun()

    with tab2:
        history = load_history()
        if not history:
            st.info("아직 저장된 퀴즈 기록이 없어요.")
        else:
            st.caption(f"총 {len(history)}개의 기록")
            for i, h in enumerate(history):
                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{h['title']}**")
                        st.caption(f"{h['created_at']} · {h['file_name']} · {len(h['quiz']['questions'])}문제")
                        st.markdown(f"`{h['difficulty']}` " + " ".join([f"`{t}`" for t in h['types']]))
                    with col2:
                        st.markdown(f"### {score_color(h['total_score'])} {h['total_score']}점")
                    if st.button("다시 보기", key=f"hist_{i}"):
                        st.session_state.hist_detail = h
                        st.session_state.page = "hist_detail"
                        st.rerun()


def page_taking():
    quiz = st.session_state.quiz
    st.title(f"📄 {quiz['title']}")
    st.caption(f"난이도: {st.session_state.difficulty} · {', '.join(st.session_state.types)} · {len(quiz['questions'])}문제")
    if st.button("← 다시 생성"):
        st.session_state.page = "generate"
        st.rerun()
    answers = {}
    for i, q in enumerate(quiz["questions"]):
        with st.container(border=True):
            st.caption("📌 단답형" if q["type"] == "단답형" else "📝 서술형")
            st.markdown(f"**Q{i+1}. {q['question']}**")
            if q["type"] == "단답형":
                answers[q["id"]] = st.text_input("답", key=f"ans_{q['id']}", label_visibility="collapsed")
            else:
                answers[q["id"]] = st.text_area("답", key=f"ans_{q['id']}", height=120, label_visibility="collapsed")
    st.session_state.answers = answers
    if st.button("✅ 제출 및 채점하기"):
        with st.spinner("채점 중..."):
            grading = grade_quiz(quiz, answers)
            result = {
                "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "file_name": st.session_state.file_name,
                "difficulty": st.session_state.difficulty,
                "types": st.session_state.types,
                "quiz": quiz,
                "answers": dict(answers),
                "grading": grading
            }
            save_result(result)
            st.session_state.result = result
            st.session_state.page = "result"
            st.rerun()


def page_result(res):
    g = res["grading"]
    if st.button("← 뒤로"):
        st.session_state.page = "generate"
        st.rerun()
    col1, col2 = st.columns([3, 1])
    with col1:
        st.title(res["quiz"]["title"])
        st.caption(f"{res.get('date') or res.get('created_at')} · {res['file_name']}")
    with col2:
        total = round(g["total"])
        st.metric("총점", f"{score_color(total)} {total}점")
    st.divider()
    for i, q in enumerate(res["quiz"]["questions"]):
        sc = next((s for s in g["scores"] if s["id"] == q["id"]), {})
        score = round(sc.get("score", 0))
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"**Q{i+1}. {q['question']}**")
            with col2:
                st.markdown(f"**{score_color(score)} {score}점**")
            st.markdown("**내 답변**")
            st.info(res["answers"].get(q["id"]) or res["answers"].get(str(q["id"])) or "_(미작성)_")
            if sc.get("matched_keywords"):
                st.success("포함된 키워드: " + " · ".join(sc["matched_keywords"]))
            with st.expander("해설 보기"):
                st.write(q["explanation"])
                if sc.get("feedback"):
                    st.caption(sc["feedback"])


# ══════════════════════════════════════════════════
# 라우터
# ══════════════════════════════════════════════════
p = st.session_state.page
if p == "login":
    page_login()
elif p == "generate":
    page_generate()
elif p == "taking":
    page_taking()
elif p == "result":
    page_result(st.session_state.result)
elif p == "hist_detail":
    page_result(st.session_state.hist_detail)
