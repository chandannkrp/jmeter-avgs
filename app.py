import asyncio
import hashlib
import hmac
import os
import threading
from pathlib import Path

import pandas as pd
import streamlit as st
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

import storage
from fury_fleet.agent import root_agent  # importing the package loads .env and picks the backend
from fury_fleet.tools.jmx_tools import analyze_jmx_file

APP_NAME = "fury_fleet"
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES_PER_SESSION", "30"))  # per conversation
MAX_VIEW_CHARS = 200_000
LABELS = {"spec": "API spec", "jmx": "JMX plan", "jtl": "JTL results"}
KIND_BY_SUFFIX = {".jmx": "jmx", ".jtl": "jtl", ".csv": "jtl",
                  ".json": "spec", ".yaml": "spec", ".yml": "spec"}

st.set_page_config(page_title="Fury Fleet", page_icon="⚡", layout="wide")


# ---------- runtime: one background event loop + persistent ADK sessions ----------
@st.cache_resource
def get_runtime():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="adk-loop").start()
    storage.DATA_DIR.mkdir(exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{(storage.DATA_DIR / 'adk_sessions.db').as_posix()}"
    service = DatabaseSessionService(db_url=db_url)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=service)
    return loop, service, runner


async def _ask(runner, service, user_slug, conv_id, text):
    existing = await service.get_session(app_name=APP_NAME, user_id=user_slug, session_id=conv_id)
    if existing is None:
        await service.create_session(app_name=APP_NAME, user_id=user_slug, session_id=conv_id)
    message = types.Content(role="user", parts=[types.Part(text=text)])
    answer, activity = [], []
    async for event in runner.run_async(user_id=user_slug, session_id=conv_id, new_message=message):
        for call in event.get_function_calls() or []:
            activity.append(f"{event.author} → {call.name}")
        if event.is_final_response() and event.content and event.content.parts:
            part_text = "".join(p.text or "" for p in event.content.parts)
            if part_text:
                answer.append(part_text)
    return "\n\n".join(answer) or "(no response)", activity


def workspace_block(user_slug, conv):
    out_dir = (storage.conv_dir(user_slug, conv["id"]) / "output").as_posix()
    lines = ["[Workspace]"] + [f"- {LABELS[k]}: {v}" for k, v in conv["inputs"].items() if k in LABELS]
    lines.append(f"- Output directory: {out_dir}")
    return "\n".join(lines)


def ask(prompt, user, conv):
    loop, service, runner = get_runtime()
    text = workspace_block(user["slug"], conv) + "\n\n" + prompt
    future = asyncio.run_coroutine_threadsafe(_ask(runner, service, user["slug"], conv["id"], text), loop)
    return future.result(timeout=600)


# ---------- callbacks (run before the next rerun, so they may set widget state) ----------
def select_conversation(conv_id):
    st.session_state.conv_id = conv_id
    st.session_state.view = "Chat"


def new_chat(user):
    select_conversation(storage.new_or_empty_conversation(user)["id"])


def logout():
    for key in ("user", "conv_id"):
        st.session_state.pop(key, None)
    st.query_params.clear()


def queue_prompt(text):
    st.session_state.pending_prompt = text
    st.session_state.view = "Chat"


def open_file(conv_id, label):
    st.session_state[f"file_choice_{conv_id}"] = label
    st.session_state.view = "Files"


def set_active(conv_id, kind, path):
    storage.set_input(conv_id, kind, path)


# ---------- access, identity, conversation ----------
def check_password() -> bool:
    expected = os.getenv("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return True
    code = st.text_input("Access code", type="password")
    if code and hmac.compare_digest(code, expected):
        st.session_state.authed = True
        st.rerun()
    elif code:
        st.error("Wrong access code.")
    return False


def login_screen():
    st.title("⚡ Fury Fleet")
    st.caption("Enter your name to open your conversations. New names start with a fresh, empty history.")
    with st.form("login"):
        name = st.text_input("Your name")
        submitted = st.form_submit_button("Continue")
    if submitted:
        try:
            st.session_state.user = storage.get_or_create_user(name)
            st.rerun()
        except ValueError as e:
            st.error(str(e))


def resolve_user():
    if "user" not in st.session_state:
        slug = st.query_params.get("user")
        user = storage.get_user_by_slug(slug) if slug else None
        if user:
            st.session_state.user = user
    if "user" in st.session_state:
        st.query_params["user"] = st.session_state.user["slug"]
        return st.session_state.user
    login_screen()
    st.stop()


def resolve_conversation(user):
    conv = None
    cid = st.session_state.get("conv_id") or st.query_params.get("chat")
    if cid:
        conv = storage.get_conversation(user, cid)
    if conv is None:
        existing = storage.list_conversations(user)
        conv = (storage.get_conversation(user, existing[0]["id"]) if existing
                else storage.new_or_empty_conversation(user))
    st.session_state.conv_id = conv["id"]
    st.query_params["chat"] = conv["id"]
    return conv


# ---------- inputs ----------
def save_upload(uploaded, user_slug, conv_id, kind):
    seen = st.session_state.setdefault(f"seen_{conv_id}", set())
    signature = (uploaded.name, hashlib.md5(uploaded.getvalue()).hexdigest())
    if signature in seen:
        return
    dest = storage.conv_dir(user_slug, conv_id) / "uploads" / Path(uploaded.name).name
    dest.write_bytes(uploaded.getvalue())
    storage.set_input(conv_id, kind, dest.as_posix())
    seen.add(signature)
    st.rerun()


def apply_spec_url(conv_id, url):
    url, key = url.strip(), f"last_url_{conv_id}"
    if url != st.session_state.get(key, ""):
        st.session_state[key] = url
        if url:
            storage.set_input(conv_id, "spec", url)
            st.rerun()


def short(value):
    return value if value.startswith("http") else Path(value).name


# ---------- views ----------
def output_snapshot(user_slug, conv_id):
    return {f"{f['group']}/{f['name']}": f["modified"]
            for f in storage.list_files(user_slug, conv_id) if f["group"] == "output"}


def render_chat(conv):
    for m in storage.get_messages(conv["id"]):
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m["meta"].get("activity"):
                with st.expander("Agent activity"):
                    st.write("\n".join(f"- {a}" for a in m["meta"]["activity"]))
            for label in m["meta"].get("files", []):
                st.button(f"👁 View {label}", key=f"open-{m['id']}-{label}",
                          on_click=open_file, args=(conv["id"], label))


def handle_prompt(user, conv):
    typed = st.chat_input("Ask Fury Fleet... e.g. 'generate a load test from my spec'")
    prompt = st.session_state.pop("pending_prompt", None) or typed
    if not prompt:
        return
    cid = conv["id"]
    if storage.count_user_messages(cid) >= MAX_MESSAGES:
        st.warning("Message limit reached for this conversation. Start a new chat.")
        return
    before = output_snapshot(user["slug"], cid)
    storage.add_message(cid, "user", prompt)
    if conv["title"] == "New chat":
        storage.set_title(cid, prompt)
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.spinner("Fleet working..."):
        try:
            answer, activity = ask(prompt, user, conv)
        except Exception as e:
            answer, activity = f"Something went wrong: {e}", []
    after = output_snapshot(user["slug"], cid)
    changed = [label for label, mtime in after.items() if before.get(label) != mtime]
    storage.add_message(cid, "assistant", answer, {"activity": activity, "files": changed})
    st.rerun()


def render_jmx(path: Path):
    xml_tab, plan_tab = st.tabs(["XML", "Plan structure"])
    with xml_tab:
        text = path.read_text(encoding="utf-8", errors="replace")
        with st.container(height=600):
            st.code(text[:MAX_VIEW_CHARS], language="xml")
    with plan_tab:
        result = analyze_jmx_file(path.as_posix())
        if result["status"] != "success":
            st.error(result["error_message"])
            return
        st.markdown("**Thread groups**")
        st.dataframe(pd.DataFrame(result["summary"]["thread_groups"]), hide_index=True)
        st.markdown("**Samplers**")
        st.dataframe(pd.DataFrame(result["summary"]["samplers"]), hide_index=True)
        st.markdown("**Findings**")
        if result["findings"]:
            st.dataframe(pd.DataFrame(result["findings"]), hide_index=True)
        else:
            st.success("No findings.")


def render_table(path: Path):
    try:
        df = pd.read_csv(path, nrows=2000)
    except Exception as e:
        st.error(f"Could not read as CSV: {e}")
        return
    st.caption("Showing the first 2,000 rows.")
    st.dataframe(df)


def render_files_view(user, conv):
    cid = conv["id"]
    files = storage.list_files(user["slug"], cid)
    if not files:
        st.info("No files in this conversation yet. Upload inputs in the sidebar or generate a plan in the chat.")
        return
    labels = [f"{f['group']}/{f['name']}" for f in files]
    key = f"file_choice_{cid}"
    if st.session_state.get(key) not in labels:
        st.session_state.pop(key, None)
    choice = st.selectbox("File", labels, key=key)
    f = files[labels.index(choice)]
    path, suffix = Path(f["path"]), Path(f["path"]).suffix.lower()
    kind = KIND_BY_SUFFIX.get(suffix)

    c1, c2, _ = st.columns([1, 2, 3])
    c1.download_button("⬇ Download", path.read_bytes(), file_name=f["name"], key=f"dl-{cid}-{choice}")
    if kind:
        c2.button(f"Use as active {LABELS[kind]}", key=f"use-{cid}-{choice}",
                  on_click=set_active, args=(cid, kind, f["path"]))
    st.caption(f"{choice} · {f['size']:,} bytes")

    if suffix == ".jmx":
        render_jmx(path)
    elif suffix in (".csv", ".jtl"):
        render_table(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        language = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}.get(suffix, "text")
        with st.container(height=600):
            st.code(text[:MAX_VIEW_CHARS], language=language)


# ---------- main ----------
storage.init_db()
if not check_password():
    st.stop()
user = resolve_user()
conv = resolve_conversation(user)
cid = conv["id"]

with st.sidebar:
    st.markdown(f"### 👤 {user['name']}")
    col1, col2 = st.columns(2)
    col1.button("New chat", on_click=new_chat, args=(user,), use_container_width=True)
    col2.button("Switch user", on_click=logout, use_container_width=True)
    using_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1")
    st.caption("Backend: Vertex AI (ADC)" if using_vertex else "Backend: Gemini API key")

    st.subheader("Conversations")
    for item in storage.list_conversations(user):
        marker = "▶ " if item["id"] == cid else ""
        st.button(f"{marker}{item['title']}", key=f"conv-{item['id']}",
                  on_click=select_conversation, args=(item["id"],), use_container_width=True)

    st.subheader("Inputs")
    spec_file = st.file_uploader("API spec (OpenAPI/Swagger)", type=["json", "yaml", "yml"], key=f"spec_{cid}")
    spec_url = st.text_input("...or spec URL", key=f"url_{cid}")
    jmx_file = st.file_uploader("JMeter plan", type=["jmx"], key=f"jmx_{cid}")
    jtl_file = st.file_uploader("JMeter results (CSV)", type=["jtl", "csv"], key=f"jtl_{cid}")
    for kind, uploaded in (("spec", spec_file), ("jmx", jmx_file), ("jtl", jtl_file)):
        if uploaded is not None:
            save_upload(uploaded, user["slug"], cid, kind)
    apply_spec_url(cid, spec_url)

    if conv["inputs"]:
        st.caption("Active: " + ", ".join(f"{LABELS[k]} = {short(v)}" for k, v in conv["inputs"].items()))

    st.subheader("Quick actions")
    has = set(conv["inputs"])
    st.button("Generate JMX from spec", disabled="spec" not in has, on_click=queue_prompt,
              args=("Generate a JMeter script from the API spec.",), use_container_width=True)
    st.button("Analyze my JMX", disabled="jmx" not in has, on_click=queue_prompt,
              args=("Analyze the JMX plan and tell me what to fix.",), use_container_width=True)
    st.button("Find coverage gaps", disabled=not {"spec", "jmx"} <= has, on_click=queue_prompt,
              args=("Compare the API spec with the JMX plan and show untested endpoints.",),
              use_container_width=True)
    st.button("Interpret results", disabled="jtl" not in has, on_click=queue_prompt,
              args=("Interpret the load test results.",), use_container_width=True)

st.title("⚡ Fury Fleet")
st.caption("AI agents for JMeter load testing and performance analysis. Inspired by Marvel Characters and loved by QA engineers.")
st.session_state.setdefault("view", "Chat")
view = st.radio("View", ["Chat", "Files"], horizontal=True, key="view", label_visibility="collapsed")

if view == "Files":
    render_files_view(user, conv)
else:
    render_chat(conv)
    handle_prompt(user, conv)