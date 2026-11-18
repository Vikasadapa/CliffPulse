
import streamlit as st
import re
from pathlib import Path

st.set_page_config(
    page_title="CliffPulse | Editor's Room",
    page_icon="🎬",
    layout="wide"
)

st.markdown("""
<style>
.stApp { background-color: #f3f5ed; }
h1, h2, h3 { color: #18382b; }
</style>
""", unsafe_allow_html=True)

st.title("🎬 CLIFFPULSE / EDITOR'S ROOM")
st.header("Give every ending a closer read.")
st.write("Upload episode scripts and review ending signals.")

uploaded_files = st.file_uploader(
    "Upload episode scripts (.txt)",
    type=["txt"],
    accept_multiple_files=True
)

paywall_start = st.number_input(
    "Paywall starts at episode",
    min_value=1,
    value=4
)

paywall_end = st.number_input(
    "Paywall ends at episode",
    min_value=1,
    value=10
)

def inspect_script(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    endings = lines[-12:] if lines else []
    ending_text = " ".join(endings)

    signals = []

    patterns = {
        "Question / uncertainty": r"\?|who|why|what|where|how",
        "Threat / danger": r"danger|kill|dead|gun|attack|threat|blood",
        "Reveal / discovery": r"reveals?|discovers?|secret|truth|hidden|letter",
        "Arrival / interruption": r"arrives?|interrupts?|enters?|door|phone rings",
        "Emotional confrontation": r"love|hate|betray|cry|marry|divorce"
    }

    for label, pattern in patterns.items():
        if re.search(pattern, ending_text, re.IGNORECASE):
            signals.append(label)

    return endings, signals


if uploaded_files:
    st.success(f"{len(uploaded_files)} episode file(s) uploaded.")

    results = []

    for file in uploaded_files:
        content = file.getvalue().decode("utf-8", errors="replace")
        endings, signals = inspect_script(content)

        results.append({
            "Episode": file.name,
            "Ending": " ".join(endings) if endings else "No ending text found",
            "Signals": ", ".join(signals) if signals else "No signals detected"
        })

    if st.button("Run script audit", type="primary"):
        st.subheader("Editorial review")

        for result in results:
            with st.expander(result["Episode"], expanded=True):
                st.markdown("**Ending excerpt**")
                st.write(result["Ending"])

                st.markdown("**Detected ending signals**")
                st.write(result["Signals"])

        st.subheader("Episode summary")

        for result in results:
            st.write(f"**{result['Episode']}** — {result['Signals']}")

        report = "\n\n".join(
            f"EPISODE: {r['Episode']}\n"
            f"ENDING: {r['Ending']}\n"
            f"SIGNALS: {r['Signals']}"
            for r in results
        )

        st.download_button(
            "Download audit report",
            data=report,
            file_name="CliffPulse_audit.txt",
            mime="text/plain"
        )

else:
    st.info("Upload one or more .txt episode scripts to begin.")

st.caption(
    "CliffPulse is an editorial-assistance prototype. "
    "Detected signals are heuristic indicators, not editorial conclusions."
)
