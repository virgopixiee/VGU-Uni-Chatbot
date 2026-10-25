"""
    pip install streamlit requests
    streamlit run frontend/src/app.py
"""
import time

import requests
import streamlit as st

API_URL = "http://localhost:8000/chat"  # endpoint nhận câu hỏi (POST)

SUGGESTIONS = [
    "Làm sao để đăng ký học phần?",
    "Học phí mỗi kỳ là bao nhiêu?",
    "Thư viện mở cửa lúc mấy giờ?",
    "Điều kiện để xét học bổng?",
]

DEMO_ANSWERS = {
    "đăng ký": "Bạn đăng ký học phần trên cổng thông tin sinh viên trong thời gian phòng đào tạo thông báo. (Nội dung demo)",
    "học phí": "Học phí tính theo chương trình đào tạo và số tín chỉ đăng ký. (Nội dung demo)",
    "thư viện": "Giờ mở cửa thư viện bạn xem trên website của trường. (Nội dung demo)",
    "học bổng": "Điều kiện học bổng thường dựa vào điểm trung bình và kết quả rèn luyện. (Nội dung demo)",
}

st.set_page_config(page_title="VGU Assistant", page_icon="🎓", layout="centered")

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] { background-color: #082f5f; }
    [data-testid="stSidebar"] * { color: #ffffff; }
    [data-testid="stSidebar"] .stButton button {
        text-align: left; width: 100%;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 10px;
    }
    [data-testid="stSidebar"] .stButton button:hover {
        background: rgba(255,255,255,0.2);
        border-color: #f2b705;
    }
    .block-container { padding-top: 2rem; max-width: 760px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ===== Các hàm xử lý =====
def get_demo_answer(question):
    """Get ans from keyword"""
    q = question.lower()
    for keyword, answer in DEMO_ANSWERS.items():
        if keyword in q:
            return answer
    return "Mình chưa có câu trả lời mẫu cho câu này. Hãy thử các câu hỏi gợi ý bên trái nhé."


def ask_backend(question):
    """Send Q to backend and get the ans"""
    response = requests.post(API_URL, json={"message": question}, timeout=30)
    response.raise_for_status()
    data = response.json()
    # Backend field name
    return data.get("answer") or data.get("response") or data.get("reply") or str(data)


def get_answer(question, demo_mode):
    """Get ans from demo or backend"""
    if demo_mode:
        time.sleep(0.8)  # fake-time to prevent spam
        return get_demo_answer(question)
    return ask_backend(question)


# ===== Chat Session History =====
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.title("🎓 VGU Assistant")
    st.caption("Trợ lý dành cho sinh viên VGU")

    demo_mode = st.toggle("Chế độ demo (không cần backend)", value=False)

    st.subheader("Thử hỏi nhanh")
    clicked_question = None
    for suggestion in SUGGESTIONS:
        if st.button(suggestion, key=suggestion):
            clicked_question = suggestion

    if st.button("🗑️ Cuộc trò chuyện mới"):
        st.session_state.messages = []
        st.rerun()

    st.caption("Bản demo frontend. Câu trả lời có thể chưa chính xác, hãy kiểm tra lại với phòng đào tạo.")

# ===== Chat Session =====
st.title("Xin chào, mình là VGU Assistant")

if not st.session_state.messages:
    st.write("Bạn có thể hỏi về đăng ký học phần, học phí, học bổng, thư viện và các thủ tục khác của trường.")

# Show back old chat
for msg in st.session_state.messages:
    avatar = "🎓" if msg["role"] == "assistant" else None
    with st.chat_message(msg["role"], avatar=avatar):
        st.write(msg["content"])

# Get Q from input or suggestion
typed_question = st.chat_input("Nhập câu hỏi của bạn...")
question = clicked_question or typed_question

if question:
    # Show user's Q
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    # Call bot and show ans
    with st.chat_message("assistant", avatar="🎓"):
        with st.spinner("Đang suy nghĩ..."):
            try:
                answer = get_answer(question, demo_mode)
            except Exception as error:
                answer = f"Không gọi được backend ({error}). Hãy kiểm tra server đã chạy chưa, hoặc bật chế độ demo."
        st.write(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
