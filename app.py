import asyncio
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

import os
from pathlib import Path
import hmac

ROOT = Path(__file__).resolve().parent
for env_file in (ROOT / ".env", ROOT / "fury_fleet" / ".env"):
    load_dotenv(env_file, override=True) # must run before the ADK imports

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from fury_fleet.agent import root_agent

APP_NAME, USER_ID, WORKSPACE = "fury_fleet", "demo_user", Path("workspace")
LABELS = {"spec": "API spec", "jmx": "JMX plan", "jtl": "JTL results"}
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES_PER_SESSION", "30"))

st.set_page_config(page_title="JMeter Fury Fleet", page_icon="🕶️", layout="wide")

def check_password() -> bool:
    expected = os.getenv("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return True  # no password configured (local dev) or already signed in
    code = st.text_input("Access code", type="password")
    if code and hmac.compare_digest(code, expected):
        st.session_state.authed = True
        st.rerun()
    elif code:
        st.error("Wrong access code.")
    return False


if not check_password():
    st.stop()

@st.cache_resource
def get_runtime():
    service = InMemorySessionService()
    return service, Runner(agent=root_agent, app_name=APP_NAME, session_service=service)


def init_state():
    if "sid" in st.session_state:
        return
    sid = uuid.uuid4().hex[:12]
    service, _ = get_runtime()
    asyncio.run(service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=sid))
    (WORKSPACE / sid / "uploads").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / sid / "output").mkdir(parents=True, exist_ok=True)
    st.session_state.sid, st.session_state.messages = sid, []


def save_upload(uploaded, sid) -> str:
    dest = WORKSPACE / sid / "uploads" / Path(uploaded.name).name
    dest.write_bytes(uploaded.getvalue())
    return dest.as_posix()


def ask(prompt, files, sid):
    _, runner = get_runtime()
    out_dir = (WORKSPACE / sid / "output").as_posix()
    block = ["[Workspace]"] + [f"- {LABELS[k]}: {v}" for k, v in files.items()]
    block.append(f"- Output directory: {out_dir}")
    text = "\n".join(block) + "\n\n" + prompt
    message = types.Content(role="user", parts=[types.Part(text=text)])

    answer, activity = [], []
    for event in runner.run(user_id=USER_ID, session_id=sid, new_message=message):
        for call in event.get_function_calls() or []:
            activity.append(f"{event.author} → {call.name}")
        if event.is_final_response() and event.content and event.content.parts:
            part_text = "".join(p.text or "" for p in event.content.parts)
            if part_text:
                answer.append(part_text)
    return "\n\n".join(answer) or "(no response)", activity


init_state()
sid = st.session_state.sid

# ---------- Sidebar: inputs, quick actions, downloads ----------
with st.sidebar:
    st.header("Inputs")
    spec_file = st.file_uploader("API spec (OpenAPI/Swagger)", type=["json", "yaml", "yml"])
    spec_url = st.text_input("...or spec URL")
    jmx_file = st.file_uploader("JMeter plan", type=["jmx"])
    jtl_file = st.file_uploader("JMeter results (CSV)", type=["jtl", "csv"])

    files = {}
    if spec_url.strip():
        files["spec"] = spec_url.strip()
    elif spec_file:
        files["spec"] = save_upload(spec_file, sid)
    if jmx_file:
        files["jmx"] = save_upload(jmx_file, sid)
    if jtl_file:
        files["jtl"] = save_upload(jtl_file, sid)

    st.header("Quick actions")
    quick = None
    if st.button("Generate JMX from spec", disabled="spec" not in files, use_container_width=True):
        quick = "Generate a JMeter script from the API spec."
    if st.button("Analyze my JMX", disabled="jmx" not in files, use_container_width=True):
        quick = "Analyze the JMX plan and tell me what to fix."
    if st.button("Find coverage gaps", disabled=not {"spec", "jmx"} <= files.keys(),
                 use_container_width=True):
        quick = "Compare the API spec with the JMX plan and show untested endpoints."
    if st.button("Interpret results", disabled="jtl" not in files, use_container_width=True):
        quick = "Interpret the load test results."

    st.header("Generated files")
    for f in sorted((WORKSPACE / sid / "output").glob("*")):
        st.download_button(f"⬇ {f.name}", f.read_bytes(), file_name=f.name, key=f"dl-{sid}-{f.name}")

    if st.button("New session", use_container_width=True):
        st.session_state.clear()
        st.rerun()

# ---------- Main: chat ----------
st.title("🕶️ JMeter : Fury Fleet")
st.caption("AI agents for JMeter load testing and performance analysis. Inspired by Marvel Characters and loved by the creator of the solution")


for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("activity"):
            with st.expander("Agent activity"):
                st.write("\n".join(f"- {a}" for a in m["activity"]))

prompt = quick or st.chat_input("Ask Fury Fleet... e.g. 'generate a load test from my spec'")
if prompt:
    if sum(m["role"] == "user" for m in st.session_state.messages) >= MAX_MESSAGES:
        st.warning("Message limit reached for this session. Click New session.")
        st.stop()
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.spinner("Assembling Avengers..."):
        try:
            answer, activity = ask(prompt, files, sid)
        except Exception as e:
            answer, activity = f"Something went wrong: {e}", []
    st.session_state.messages.append({"role": "assistant", "content": answer, "activity": activity})
    st.rerun()  # refreshes the sidebar so new generated files appear