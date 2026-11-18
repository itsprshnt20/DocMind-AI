"""DocMind AI's local Streamlit interface."""
from pathlib import Path

import streamlit as st

from docmind import MAX_FILES, NO_ANSWER, Workspace, citation_text

SAMPLES = Path(__file__).resolve().parent / "samples"
QUESTIONS = (
    ("Handbook question", "How many annual volunteer hours are available?"),
    ("Onboarding question", "Where is laptop pickup?"),
    ("Unsupported question", "What is the lunar travel budget?"),
)
STYLE = """
<style>
.stApp, [data-testid="stHeader"] { background: #000; }
.stMainBlockContainer { max-width: 72rem; padding-top: 3.6rem; padding-bottom: 4rem; }
.st-key-hero, .st-key-library, .st-key-ask, .st-key-evidence, .st-key-history_panel {
    border: 1px solid #353047; border-radius: 1.2rem;
    padding: clamp(1rem, 2.6vw, 1.7rem); background: #0d0b13;
}
.st-key-hero {
    padding: clamp(1.4rem, 4vw, 2.9rem);
    background: radial-gradient(ellipse at 94% 0%, #2c1f4d 0%, #151025 36%, #08070d 74%);
}
.st-key-hero h1 {
    font-size: clamp(2.75rem, 7vw, 5rem); line-height: 1; letter-spacing: -.07em;
}
.st-key-library [data-testid="stForm"], .st-key-ask [data-testid="stForm"] { border: 0; padding: 0; }
[data-testid="stBaseButton-primary"] {
    color: #1d1038; background: #c7aaff; border-color: #c7aaff; font-weight: 700;
}
[data-testid="stBaseButton-primary"]:hover { color: #1d1038; background: #e3d0ff; }
[data-testid="stBaseButton-primary"]:not(:disabled) p { color: #1d1038; }
.st-key-evidence { border-top: 2px solid #a983f0; }
.st-key-history_panel [data-testid="stText"] { margin-top: .2rem; }
[data-testid="stDownloadButton"] button { border-color: #70569e; border-radius: .75rem; }
[data-testid="stDownloadButton"] button:hover { color: #e3d0ff; border-color: #c7aaff; }
button:focus-visible, input:focus-visible { outline: 2px solid #c7aaff; outline-offset: 3px; }
[data-testid="stCode"] pre { background: #161121; border: 1px solid #3b2c54; }
[data-testid="stText"] { overflow-wrap: anywhere; white-space: pre-wrap; }
@media (max-width: 600px) {
    .stMainBlockContainer { padding-top: 3rem; }
}
</style>
"""


def clear_workspace():
    st.session_state.workspace.clear()
    st.session_state.upload_errors = []
    st.session_state.upload_epoch += 1
    st.session_state.question_epoch += 1
    st.session_state.demo = False


def ask(workspace, question):
    try:
        with st.spinner("Searching local passages…"):
            workspace.ask(question)
    except ValueError as exc:
        st.error(str(exc))


st.set_page_config(page_title="DocMind AI", layout="wide")
st.html(STYLE)
if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
    st.session_state.upload_errors = []
    st.session_state.upload_epoch = 0
    st.session_state.question_epoch = 0
    st.session_state.demo = False
workspace = st.session_state.workspace

with st.container(key="hero"):
    st.title("DocMind AI")
    st.write("Ask a question about your PDFs and text files, then open the exact source passage.")
    st.caption("Local document Q&A · Maintained by Prashant Chauhan")

library, question_panel = st.columns([1, 1.2])
with library, st.container(key="library"):
    st.subheader("Documents")
    actions = st.columns(2)
    if actions[0].button("Load demo", use_container_width=True, disabled=st.session_state.demo):
        with st.spinner("Indexing synthetic documents…"):
            files = [(path.name, path.read_bytes()) for path in sorted(SAMPLES.iterdir())
                     if path.name in {"synthetic-handbook.pdf", "synthetic-onboarding.txt"}]
            st.session_state.upload_errors = workspace.add(files)
            st.session_state.demo = not st.session_state.upload_errors
        st.rerun()
    actions[1].button("Clear workspace", on_click=clear_workspace, use_container_width=True)
    with st.form("upload"):
        files = st.file_uploader("Upload documents", type=["pdf", "txt"], accept_multiple_files=True,
                                 key=f"upload_{st.session_state.upload_epoch}")
        if st.form_submit_button("Index files"):
            if not files:
                st.warning("Choose at least one PDF or UTF-8 text file.")
            elif len(files) > MAX_FILES:
                st.error("Select at most 10 files at a time.")
            else:
                with st.spinner("Extracting and indexing documents…"):
                    st.session_state.upload_errors = workspace.add((file.name, file.getvalue()) for file in files)
                st.session_state.upload_epoch += 1
                st.rerun()
    for filename, error in st.session_state.upload_errors:
        st.text(filename)
        st.error(error)
    if workspace.documents:
        if st.session_state.demo:
            st.caption("Synthetic demo documents loaded")
        for document in workspace.documents:
            st.text(f"{document.filename} · {document.id[:8]}")
            st.caption(f"Indexed · {len(document.passages)} passages")
    else:
        st.info("Load the synthetic demo or upload PDF and TXT files to begin.")

with question_panel, st.container(key="ask"):
    st.subheader("Ask a question")
    st.caption("Answer mode · Extractive passages ranked by local TF-IDF and cosine similarity")
    with st.form("question"):
        question = st.text_input("Question", placeholder="What would you like to find?", max_chars=400,
                                 key=f"question_{st.session_state.question_epoch}")
        if st.form_submit_button("Ask question", type="primary", disabled=not workspace.documents):
            ask(workspace, question)
    if st.session_state.demo:
        st.caption("Try a question about the synthetic documents")
        for label, example in QUESTIONS:
            if st.button(label, use_container_width=True, help=example):
                ask(workspace, example)
    elif not workspace.documents:
        st.info("Index at least one document to ask a question.")

if workspace.answer:
    with st.container(key="evidence"):
        st.subheader("Answer")
        st.text(workspace.answer.question)
        passage = workspace.answer.passage
        if passage is None:
            st.info(NO_ANSWER)
        else:
            st.code(passage.text, language=None)
            st.text(f"Source [1] · {passage.filename} · {passage.location} · File {passage.document_id[:8]}")
            with st.expander(f"Open citation [1] · {passage.location}"):
                st.caption("Exact indexed passage")
                st.text(f"{passage.filename} · File {passage.document_id[:8]}")
                st.code(passage.text, language=None)

            st.download_button("Download citation", citation_text(workspace.answer), "docmind-citation.txt",
                               "text/plain; charset=utf-8", on_click="ignore")

if workspace.history:
    with st.container(key="history_panel"):
        st.subheader("Recent questions")
        st.caption("Up to five questions from the current document set.")
        for number, entry in enumerate(workspace.history, start=1):
            question_column, action_column = st.columns([3, 1], vertical_alignment="center")
            question_column.text(entry.question)
            action_column.button(f"Reopen answer {number}", key=f"history_{number}",
                                 on_click=workspace.restore, args=(entry,), use_container_width=True)
