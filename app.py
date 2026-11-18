"""CliffPulse: evidence-led editorial review for timestamped scripts."""
from __future__ import annotations

import csv
import html
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

st.set_page_config(page_title="CliffPulse · Editor's room", page_icon="◒", layout="wide")
SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = SCRIPT_DIR.parent
DEFAULT_INPUT_DIR = PACKAGE_DIR / "sample_scripts" if (PACKAGE_DIR / "sample_scripts").is_dir() else PACKAGE_DIR.parent
REPORT_NAME = "master_retention_and_stalling_report.json"
STATUS_LABELS = {
    "REVIEW_CLOSURE": "Closure cues · review",
    "MIXED_SIGNALS": "Mixed ending cues",
    "TENSION_CUES_PRESENT": "Tension cues present",
    "EDITOR_REVIEW": "Needs editorial judgment",
}
STATUS_COLORS = {
    "REVIEW_CLOSURE": "closure",
    "MIXED_SIGNALS": "mixed",
    "TENSION_CUES_PRESENT": "tension",
    "EDITOR_REVIEW": "neutral",
}
DECISIONS = ["Unreviewed", "Useful flag — revise", "Keep as written", "False positive", "Needs more context"]

st.markdown("""
<style>
:root { --ink:#1b3029; --muted:#62756a; --paper:#f2f3ec; --card:#fffefa; --line:#dfe6d9; --lime:#d6f279; }
.stApp { background:var(--paper); color:var(--ink); }
[data-testid="stHeader"] { background:rgba(242,243,236,.93); }
.block-container { max-width:1440px; padding:2.1rem 1.5rem 3rem; }
h1,h2,h3,h4 { color:var(--ink); letter-spacing:-.035em; }
h2 { font-size:1.65rem!important; } h3 { font-size:1.1rem!important; }
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p, [data-testid="stText"], [data-testid="stExpander"] summary { color:var(--ink); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color:var(--muted); }
[data-testid="stSidebar"] { background:#e9eee2; border-right:1px solid #dce4d4; }
[data-testid="stSidebar"] h2 { font-size:1.15rem!important; color:var(--ink); }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"], [data-testid="stSidebar"] label, [data-testid="stSidebar"] summary, [data-testid="stSidebar"] svg { color:var(--ink); }
[data-testid="stSelectbox"] [data-baseweb="select"] > div, [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input, [data-testid="stTextArea"] textarea { background:#fffefa; border-radius:9px; color:#1b3029; }
[data-testid="stSelectbox"] svg, [data-testid="stNumberInput"] button { color:var(--ink); }
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button { border-radius:9px; font-weight:650; color:var(--ink); background:#fffefa; }
[data-testid="stButton"] button p, [data-testid="stFormSubmitButton"] button p, [data-testid="stDownloadButton"] button p { color:inherit; }
[data-testid="stButton"] button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { background:#244a39; border-color:#244a39; color:#fffefa; }
[data-testid="stButton"] button[kind="primary"] [data-testid="stMarkdownContainer"], [data-testid="stFormSubmitButton"] button[kind="primary"] [data-testid="stMarkdownContainer"] { color:#fffefa; }
[data-testid="stDownloadButton"] button { background:#fffefa; border:1px solid #ccd9c5; border-radius:9px; color:var(--ink); }
.stTabs [data-baseweb="tab-list"] { gap:7px; border-bottom:1px solid #d8e1d1; padding-top:10px; }
.stTabs [data-baseweb="tab"] { height:46px; padding:0 15px; color:#687b6e; font-weight:650; }
.stTabs [aria-selected="true"] { color:#1b3029!important; background:#e4edda; border-radius:10px 10px 0 0; }
.stTabs [data-baseweb="tab-highlight"] { background:#8ba347; }
[data-testid="stDataFrame"] { border:1px solid #dce5d5; border-radius:12px; overflow:hidden; }
[data-testid="stExpander"] { background:#fffefa; border:1px solid #dfe6d9; border-radius:12px; }
.hero { background:radial-gradient(ellipse at 84% 0, #405b37 0, transparent 53%),#1b3029; border-radius:22px; padding:27px 32px; color:#fffefa; }
.hero-top { display:flex; justify-content:space-between; align-items:center; gap:16px; }
.brand { font:700 12px ui-monospace,monospace; letter-spacing:.12em; text-transform:uppercase; }
.brand-icon { display:inline-flex; align-items:center; justify-content:center; width:29px; height:29px; background:var(--lime); color:#1b3029; border-radius:9px; font-size:20px; margin-right:9px; }
.hero-tag { border:1px solid #66805e; border-radius:99px; padding:6px 10px; color:#d9e7c9; font:500 10px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.08em; }
.hero h1 { color:#fffefa; font-size:clamp(2rem,3.7vw,3.6rem); line-height:1.06; margin:24px 0 12px; font-weight:750; letter-spacing:-.06em; }
.hero h1 span { color:#d6f279; }
.hero p { color:#c8d7c8; max-width:770px; margin:0; font-size:15px; line-height:1.65; }
.hero-meta { border-top:1px solid #425a48; margin-top:22px; padding-top:13px; color:#bcd1b8; font:500 11px ui-monospace,monospace; letter-spacing:.04em; }
.kicker { color:#708568; font:600 10px ui-monospace,monospace; letter-spacing:.12em; text-transform:uppercase; margin:22px 0 5px; }
.intro { color:#66796b; font-size:14px; line-height:1.6; margin-bottom:18px; max-width:850px; }
.metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:13px; margin:20px 0 8px; }
.metric { background:#fffefa; border:1px solid #dfe6d9; border-radius:14px; padding:17px 19px; }
.metric-label { color:#70836d; font:600 10px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.06em; }
.metric-value { color:#203d2c; font-size:30px; font-weight:750; letter-spacing:-.06em; line-height:1.45; }
.metric-detail { color:#6c7e6b; font-size:12px; }
.notice { background:#e5eed9; border:1px solid #d4e3c3; border-radius:12px; padding:14px 17px; color:#3f5b35; font-size:13px; line-height:1.6; margin:14px 0; }
.episode-map { display:grid; grid-template-columns:repeat(auto-fit,minmax(43px,1fr)); gap:7px; padding:17px; background:#fffefa; border:1px solid #dfe6d9; border-radius:14px; margin:12px 0; }
.episode-tile { min-height:43px; border-radius:8px; display:flex; align-items:center; justify-content:center; color:#36513c; font:600 12px ui-monospace,monospace; border:1px solid transparent; }
.episode-tile.gate { box-shadow:inset 0 -3px 0 #253c2b; }
.closure { background:#f9dfc6; color:#80532c; } .mixed { background:#f4eac3; color:#705c29; } .tension { background:#e0edce; color:#3e6134; } .neutral { background:#e9ede5; color:#657463; }
.legend { color:#687c63; font-size:12px; line-height:1.9; }
.legend span { margin-right:15px; white-space:nowrap; }
.legend i { width:8px; height:8px; display:inline-block; border-radius:3px; margin-right:5px; }
.evidence { background:#fffefa; border:1px solid #dce5d4; border-left:4px solid #9eb777; border-radius:10px; padding:15px 17px; margin:8px 0 12px; }
.evidence-meta { display:flex; justify-content:space-between; gap:10px; color:#698260; font:600 10px ui-monospace,monospace; letter-spacing:.04em; text-transform:uppercase; margin-bottom:10px; }
.evidence-text { color:#304c35; white-space:pre-wrap; font-size:14px; line-height:1.75; overflow-wrap:anywhere; }
.cue { display:inline-block; border-radius:99px; background:#edf3e4; color:#647a50; font:500 10px ui-monospace,monospace; padding:4px 7px; margin:8px 4px 0 0; }
.brief { background:#fffefa; border:1px solid #dfe6d9; padding:17px 19px; border-radius:13px; margin:8px 0 16px; }
.brief-title { color:#69815f; font:600 10px ui-monospace,monospace; letter-spacing:.08em; text-transform:uppercase; margin-bottom:7px; }
.brief-text { color:#36503b; font-size:14px; line-height:1.65; }
.footer { color:#7d8e75; font:500 10px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.06em; margin:25px 0 0; text-align:center; }
@media(max-width:1100px) { .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media(max-width:800px) { .block-container { padding:1rem; } .hero { padding:22px; } .hero-top { align-items:flex-start; } .hero-tag { display:none; } .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } }
</style>
""", unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def discover_runs():
    candidates = list((PACKAGE_DIR / "runs").glob(f"*/{REPORT_NAME}"))
    configured = os.environ.get("CLIFFPULSE_OUTPUT_DIR")
    if configured:
        candidates.append(Path(configured).expanduser().resolve() / REPORT_NAME)
    candidates.append(PACKAGE_DIR / "raw_outputs" / REPORT_NAME)
    found = {}
    for report in candidates:
        data = read_json(report, {})
        if data.get("schema_version") == 3:
            found[str(report.parent.resolve())] = (data, report.stat().st_mtime_ns)
    return sorted(found.items(), key=lambda item: item[1][1], reverse=True)


@st.cache_data(show_spinner=False)
def load_run(run_dir, report_version):
    folder = Path(run_dir)
    master = json.loads((folder / REPORT_NAME).read_text(encoding="utf-8"))
    frame = pd.read_csv(folder / "episode_retention_summary.csv")
    pairs = json.loads((folder / "multi_episode_stalling_cut_sheets.json").read_text(encoding="utf-8"))
    manifest = read_json(folder / "run_manifest.json", {})
    validation = read_json(folder / "validation_report.json", {})
    quality = read_json(folder / "quality_report.json", {})
    app_checks = read_json(folder / "app_validation_report.json", {})
    return master, frame, pairs, manifest, validation, quality, app_checks


@st.cache_data(show_spinner=False)
def load_episode(run_dir, number, report_version):
    path = Path(run_dir) / "episodes_json" / f"episode_{int(number):03d}_retention_audit.json"
    return json.loads(path.read_text(encoding="utf-8"))


def heading(label, title, description=""):
    st.markdown(f'<div class="kicker">{esc(label)}</div><h2>{esc(title)}</h2><div class="intro">{esc(description)}</div>', unsafe_allow_html=True)


def brief(label, text):
    if text:
        if isinstance(text, list):
            text = " · ".join(str(item) for item in text)
        st.markdown(f'<div class="brief"><div class="brief-title">{esc(label)}</div><div class="brief-text">{esc(text)}</div></div>', unsafe_allow_html=True)


def show_block(block, source_label="Source screenplay"):
    block = block or {}
    cues = []
    for cue in block.get("closure_cues", []):
        cues.append(f"closure · {cue}")
    for cue in block.get("tension_cues", []):
        cues.append(f"tension · {cue}")
    badges = "".join(f'<span class="cue">{esc(cue)}</span>' for cue in cues)
    text = block.get("text", block.get("body", "No source text available."))
    line_label = f" · line {block['source_line']}" if block.get("source_line") else ""
    st.markdown(
        f'<div class="evidence"><div class="evidence-meta"><span>{esc(source_label)} · {esc(block.get("tag", block.get("tag_raw", "BLOCK")))}{esc(line_label)}</span><span>{esc(block.get("start_str", "—"))} → {esc(block.get("end_str", "—"))}</span></div><div class="evidence-text">{esc(text)}</div>{badges}</div>',
        unsafe_allow_html=True,
    )


def review_control(run_key, finding_id, episode_label):
    storage_key = f"{run_key}::{finding_id}"
    saved = st.session_state.setdefault("review_decisions", {}).get(storage_key, {})
    with st.form(f"review_form_{storage_key}"):
        st.markdown("**Your editorial decision**")
        decision = st.selectbox("Decision", DECISIONS, index=DECISIONS.index(saved.get("decision", "Unreviewed")), key=f"decision_{storage_key}")
        notes = st.text_area("Editorial notes", value=saved.get("notes", ""), placeholder="What would you keep or change, and why?", key=f"notes_{storage_key}")
        if st.form_submit_button("Save review", type="primary"):
            st.session_state["review_decisions"][storage_key] = {
                "run": run_key, "finding_id": finding_id, "episodes": episode_label,
                "decision": decision, "notes": notes,
                "reviewed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            st.success("Review saved for this browser session. Export your decisions below to keep them.")


def review_export(run_key):
    rows = [row for row in st.session_state.get("review_decisions", {}).values() if row["run"] == run_key]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["run", "finding_id", "episodes", "decision", "notes", "reviewed_at_utc"])
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue(), rows


with st.sidebar:
    st.markdown("## Your review workspace")
    st.caption("Load scripts, inspect the evidence, and record an editorial decision.")
    with st.expander("Start a new audit", expanded=not bool(discover_runs())):
        with st.form("run_audit"):
            uploaded_scripts = st.file_uploader(
                "Upload timestamped script files",
                type=["txt"],
                accept_multiple_files=True,
                help="Select the .txt screenplay files for this show. Each filename or script must identify its episode number.",
            )
            show_title = st.text_input("Show title (optional)", value="")
            first_col, last_col = st.columns(2)
            gate_start = first_col.number_input("Paywall starts", min_value=1, value=4, step=1)
            gate_end = last_col.number_input("Paywall ends", min_value=1, value=10, step=1)
            st.caption("Use the team's actual gate. The supplied example uses episodes 4–10 as a review assumption.")
            launched = st.form_submit_button("Run script audit", type="primary")
        if launched:
            if gate_end < gate_start:
                st.error("The paywall end must be on or after its start.")
            elif not uploaded_scripts:
                st.error("Please upload one or more timestamped .txt screenplay files.")
            else:
                try:
                    with tempfile.TemporaryDirectory(prefix="cliffpulse_uploaded_scripts_") as upload_dir:
                        input_dir = Path(upload_dir)
                        for uploaded_script in uploaded_scripts:
                            # Keep only the uploaded filename; do not accept client-side paths.
                            source_name = Path(uploaded_script.name).name
                            safe_name = f"{Path(source_name).stem}.txt"  # Normalize extension case for Linux hosting.
                            (input_dir / safe_name).write_bytes(uploaded_script.getvalue())

                        command = [sys.executable, str(SCRIPT_DIR / "run_pipeline.py"), "--input-dir", str(input_dir), "--input-pattern", "*.txt", "--paywall-start", str(gate_start), "--paywall-end", str(gate_end)]
                        if show_title.strip():
                            command.extend(["--show-title", show_title.strip()])
                        with st.spinner("Reading scripts and saving a new audit…"):
                            process = subprocess.run(command, cwd=str(SCRIPT_DIR), capture_output=True, text=True, timeout=120)

                    if process.returncode:
                        st.error("The audit could not finish. Check that the uploaded files use the supported timestamped screenplay format and episode numbers in filenames or script metadata.")
                        st.code((process.stderr or process.stdout)[-4000:], language="text")
                    else:
                        new_runs = discover_runs()
                        if new_runs:
                            st.session_state["active_run"] = new_runs[0][0]
                        st.success("A new audit is ready.")
                except (OSError, subprocess.TimeoutExpired) as error:
                    st.error(f"The audit could not complete: {error}")

    runs = discover_runs()
    options = [item[0] for item in runs]
    run_metadata = {key: value for key, value in runs}
    if options:
        if st.session_state.get("active_run") not in options:
            st.session_state["active_run"] = options[0]
        run_dir = st.selectbox("Saved audit", options, key="active_run", format_func=lambda path: f"{run_metadata[path][0].get('show_title', Path(path).name)} · {Path(path).name}")
        st.caption(f"Run: {Path(run_dir).name}")
    else:
        run_dir = None
    st.markdown("---")
    st.caption("Supported input: upload timestamped UTF-8 screenplay .txt files, one file per episode; episode numbers must appear in filenames or script metadata. Your source scripts stay intact. Each audit creates its own results folder.")

if not run_dir:
    st.markdown('<section class="hero"><div class="brand"><span class="brand-icon">◒</span> CliffPulse / Editor\'s room</div><h1>Give every ending<br><span>a closer read.</span></h1><p>Start an audit from the panel on the left. CliffPulse collects script evidence for editors to review before production.</p></section>', unsafe_allow_html=True)
    st.info("No current-format audit is available yet. Use ‘Start a new audit’ to analyse the supplied scripts.")
    st.caption("Older demonstration outputs are archived and excluded from this workspace.")
    st.stop()

report_version = tuple(
    (name, (Path(run_dir) / name).stat().st_mtime_ns)
    for name in [REPORT_NAME, "episode_retention_summary.csv", "multi_episode_stalling_cut_sheets.json", "run_manifest.json", "quality_report.json", "validation_report.json", "app_validation_report.json"]
    if (Path(run_dir) / name).exists()
)
try:
    master, frame, pairs, manifest, validation, quality, app_checks = load_run(run_dir, report_version)
except (OSError, ValueError, KeyError) as error:
    st.error(f"This audit is incomplete or unreadable: {error}")
    st.info("Choose another saved audit or run the scripts again to create a complete results folder.")
    st.stop()

run_key = Path(run_dir).name
show_name = master.get("show_title", run_key)
episode_count = master.get("episodes_analyzed", len(frame))
ending_count = master.get("ending_review_count", 0)
paywall_count = master.get("paywall_ending_review_count", 0)
repetition_count = master.get("repetition_review_count", len(pairs))
paywall_episodes = master.get("paywall_zone_episodes", [])
gate_start = master.get("paywall_zone_start", 4)
gate_end = master.get("paywall_zone_end", 10)

st.markdown(f'''<section class="hero">
<div class="hero-top"><div class="brand"><span class="brand-icon">◒</span> CliffPulse / Editor's room</div><span class="hero-tag">Evidence → editorial decision</span></div>
<h1>Give every ending<br><span>a closer read.</span></h1>
<p>{esc(show_name)}. An editorial queue built from the scripts: ending cues, possible repeated beats, and the exact source passages behind each flag.</p>
<div class="hero-meta">{episode_count} EPISODES &nbsp; / &nbsp; {master.get('total_runtime_minutes', 0):.1f} MINUTES &nbsp; / &nbsp; PAYWALL REVIEW {gate_start:02d}–{gate_end:02d}</div>
</section>''', unsafe_allow_html=True)

metrics = [
    ("Ending review queue", str(ending_count), "closure cues without registered tension"),
    ("Inside the paywall window", f"{paywall_count} / {len(paywall_episodes)}", "flagged / available episodes in the window"),
    ("Possible repetition", str(repetition_count), "adjacent pairs with source matches"),
    ("Source coverage", str(episode_count), "episodes parsed for this audit"),
]
st.markdown('<div class="metrics">' + ''.join(f'<div class="metric"><div class="metric-label">{esc(label)}</div><div class="metric-value">{esc(value)}</div><div class="metric-detail">{esc(detail)}</div></div>' for label, value, detail in metrics) + '</div>', unsafe_allow_html=True)
st.caption("These are script-review signals. Viewer retention, conversion, and the benefit of an edit require a separate team evaluation.")

overview_tab, ending_tab, repetition_tab, method_tab = st.tabs(["01  Season overview", "02  Ending review", "03  Repeated beats", "04  Method & checks"])

with overview_tab:
    heading("01 / season overview", "Find the episodes worth a closer look", "The map summarizes detected ending cues. A dark underline marks episodes in your configured paywall window.")
    tiles = []
    for row in frame.to_dict(orient="records"):
        status = row.get("Ending_Status", "EDITOR_REVIEW")
        gate = " gate" if bool(row.get("Paywall_Conversion_Zone", False)) else ""
        tiles.append(f'<div class="episode-tile {STATUS_COLORS.get(status, "neutral")}{gate}" title="{esc(STATUS_LABELS.get(status, status))}">{int(row["Episode"]):02d}</div>')
    st.markdown('<div class="episode-map">' + ''.join(tiles) + '</div>', unsafe_allow_html=True)
    st.markdown('<div class="legend"><span><i class="closure"></i>Closure cues</span><span><i class="mixed"></i>Mixed cues</span><span><i class="tension"></i>Tension cues</span><span><i class="neutral"></i>Editorial judgment</span></div>', unsafe_allow_html=True)
    visible = frame.copy()
    if "Ending_Status" in visible:
        visible["Ending_Status"] = visible["Ending_Status"].map(lambda value: STATUS_LABELS.get(value, value))
    columns = [name for name in ["Episode", "Duration_Sec", "Scenes", "Paywall_Conversion_Zone", "Ending_Status", "Needs_Ending_Review", "Closure_Cue_Count", "Tension_Cue_Count"] if name in visible]
    visible = visible[columns].rename(columns={"Duration_Sec": "Runtime (sec)", "Paywall_Conversion_Zone": "Paywall window", "Ending_Status": "Ending cues", "Needs_Ending_Review": "Queued", "Closure_Cue_Count": "Closure cues", "Tension_Cue_Count": "Tension cues"})
    st.dataframe(visible, hide_index=True, use_container_width=True, height=370)
    export_cols = st.columns(2)
    export_cols[0].download_button("Download episode register", (Path(run_dir) / "episode_retention_summary.csv").read_bytes(), file_name=f"{run_key}-episode-register.csv", mime="text/csv")
    queue_path = Path(run_dir) / "review_queue.md"
    if queue_path.exists():
        export_cols[1].download_button("Download editorial review brief", queue_path.read_bytes(), file_name=f"{run_key}-review-brief.md", mime="text/markdown")

with ending_tab:
    heading("02 / ending review", "Read the source before deciding", "A closure cue can be intentional. Use the flagged passage, the wider story context, and your editorial judgment to decide what needs work.")
    scope = st.selectbox("Review scope", ["Flagged endings", "Mixed signals", "Paywall window", "All episodes"])
    if scope == "Flagged endings":
        subset = frame[frame["Needs_Ending_Review"].astype(bool)]
    elif scope == "Mixed signals":
        subset = frame[frame["Ending_Status"] == "MIXED_SIGNALS"]
    elif scope == "Paywall window":
        subset = frame[frame["Paywall_Conversion_Zone"].astype(bool)]
    else:
        subset = frame
    if subset.empty:
        st.info("No episodes match this scope. Choose ‘All episodes’ to inspect the scripts.")
    else:
        choices = subset["Episode"].astype(int).tolist()
        episode_number = st.selectbox("Episode", choices, format_func=lambda number: f"Episode {number:02d}" + (" · paywall window" if number in paywall_episodes else ""))
        audit = load_episode(run_dir, episode_number, report_version)
        st.caption(f"{audit.get('file_name', '')} · {audit.get('duration_formatted', '')} · {audit.get('scene_count', 0)} scenes")
        brief("Story context", audit.get("logline") or "No logline was supplied in this script.")
        editor_brief = audit.get("editor_brief", {})
        brief("What triggered the review", editor_brief.get("finding", STATUS_LABELS.get(audit.get("ending_status"), "Editorial review")))
        left, right = st.columns([1.15, 1], gap="large")
        with left:
            st.markdown("### The ending on the page")
            for block in audit.get("ending_evidence", []):
                show_block(block, f"EP {episode_number:02d}")
        with right:
            st.markdown("### A next-episode moment to inspect")
            candidate = audit.get("next_episode_candidate")
            if candidate:
                st.caption(f"Episode {candidate['episode_index']} · {candidate.get('scene_heading', '')}")
                show_block(candidate.get("evidence", {}), f"EP {candidate['episode_index']:02d}")
                st.caption(candidate.get("selection_reason", ""))
                st.markdown('<div class="notice">This is an existing source passage. A writer must check continuity and pacing before moving any part of it.</div>', unsafe_allow_html=True)
            else:
                st.info("No suitable next-episode candidate is available. If this is a finale, a resolved ending may be appropriate.")
            brief("Suggested review action", editor_brief.get("review_action", "Review the ending against its intended story purpose."))
            review_control(run_key, f"ending-{episode_number:03d}", f"Episode {episode_number}")
        with st.expander("Audit details and source basis"):
            st.write(editor_brief.get("source_basis", ""))
            st.json(audit)
        st.download_button("Download this episode's evidence", json.dumps(audit, indent=2, ensure_ascii=False), file_name=f"episode-{episode_number:03d}-evidence.json", mime="application/json")

with repetition_tab:
    heading("03 / repeated beats", "Check whether the story is repeating itself", "Similar wording can be a deliberate callback. These matches direct your attention to exact passages; they do not prescribe a cut or a time saving.")
    if not pairs:
        st.info("No adjacent pairs crossed the current repetition thresholds. An editor should still sample unflagged episodes for missed issues.")
    else:
        pair_indices = list(range(len(pairs)))
        pair_index = st.selectbox("Adjacent episode pair", pair_indices, format_func=lambda index: pairs[index]["episodes"])
        pair = pairs[pair_index]
        label = pair.get("finding_type", "Possible repetition").replace("_", " ").lower()
        brief("Why this pair is queued", f"{label.capitalize()} · strongest text similarity {pair.get('max_text_similarity', 0):.0%}. Review the matched passages below.")
        st.caption("Locations: " + " / ".join(pair.get("locations", [])))
        matches = pair.get("evidence_matches", [])
        for index, match in enumerate(matches):
            st.markdown(f"**Match {index + 1} · {str(match.get('kind', 'source match')).replace('_', ' ').lower()}**")
            st.caption(f"Text similarity: {match.get('text_similarity', 0):.0%}")
            left, right = st.columns(2, gap="large")
            with left:
                show_block(match.get("episode_a_block", {}), f"EP {pair['episode_a']:02d}")
            with right:
                show_block(match.get("episode_b_block", {}), f"EP {pair['episode_b']:02d}")
        brief("Editorial action", pair.get("review_action", "Compare both scenes in context and retain only changes that preserve the story."))
        review_control(run_key, pair.get("pair_id", f"pair-{pair_index}"), pair["episodes"])
        st.download_button("Download this pair's evidence", json.dumps(pair, indent=2, ensure_ascii=False), file_name=f"{pair.get('pair_id', 'repetition')}-evidence.json", mime="application/json")

with method_tab:
    heading("04 / method & checks", "Make the review easy to challenge", "The audit collects explainable cues from text. Editors decide whether a flagged moment is a problem and whether any change helps the story.")
    left, right = st.columns(2, gap="large")
    with left:
        brief("What the software does", "Reads timestamped screenplay blocks, checks the closing passage for closure and tension cues, and compares adjacent episodes for repeated text. Each finding points back to source excerpts.")
        brief("Where editorial judgment is needed", "Intentional closure, quiet menace, callbacks, genre conventions, and season finales need context. A cue match alone cannot establish that viewers will leave or pay.")
        methodology = master.get("methodology", {})
        with st.expander("Exact detection settings"):
            st.json(methodology)
        for limitation in master.get("limitations", []):
            st.caption(f"• {limitation}")
    with right:
        if quality:
            brief("Input integrity", f"{quality.get('files_parsed', 0)} files · {quality.get('tagged_blocks_parsed', 0):,} timestamped blocks · {quality.get('scenes_parsed', 0)} scenes parsed. These checks establish readable input; editorial accuracy is evaluated separately.")
        if validation:
            st.markdown("### Recorded checks")
            st.caption(validation.get("scope", "Automated checks recorded with this audit."))
            checks = validation.get("checks", [])
            if checks:
                passed_checks = sum(bool(check.get("passed")) for check in checks)
                status_text = f"{passed_checks} of {len(checks)} automated checks passed"
                if passed_checks == len(checks):
                    st.success(status_text)
                else:
                    st.error(status_text)
                check_table = pd.DataFrame([{"Check": check.get("name", "Check"), "Result": "Passed" if check.get("passed") else "Failed", "Detail": check.get("detail", "")} for check in checks])
                st.dataframe(check_table, hide_index=True, use_container_width=True, height=270)
            else:
                st.json(validation)
            for limit in validation.get("not_measured", []):
                st.caption(str(limit))
            st.download_button("Download automated check results", json.dumps(validation, indent=2, ensure_ascii=False), file_name=f"{run_key}-validation.json", mime="application/json")
            if app_checks:
                st.caption(f"App workflow checks: {app_checks.get('passed', 0)} passed, {app_checks.get('failed', 0)} failed. See the exported validation report for scope.")
                st.download_button("Download app workflow results", json.dumps(app_checks, indent=2, ensure_ascii=False), file_name=f"{run_key}-app-validation.json", mime="application/json")
        else:
            brief("Quality evidence", "No automated validation report is bundled with this run. Do not treat the absence of an error as proof of editorial accuracy.")
        brief("A real-team pilot", "Have two editors independently review a sample of flagged and unflagged episodes. Track useful flags, false positives, missed issues, and review time. Evaluate viewer continuation and paywall conversion only after an approved edit reaches a controlled audience test.")
    with st.expander("Run provenance"):
        st.json(manifest)
    if (Path(run_dir) / "run_manifest.json").exists():
        st.download_button("Download run manifest", (Path(run_dir) / "run_manifest.json").read_bytes(), file_name=f"{run_key}-manifest.json", mime="application/json")

st.markdown("---")
review_csv, review_rows = review_export(run_key)
review_col, export_col = st.columns([3, 1])
review_col.markdown(f"**{len(review_rows)} editorial decisions saved in this session**")
review_col.caption("Export decisions to retain them. Closing or resetting the session can clear this review log.")
export_col.download_button("Export review decisions", review_csv, file_name=f"{run_key}-editor-decisions.csv", mime="text/csv", disabled=not bool(review_rows))
st.markdown('<div class="footer">CliffPulse · source evidence, editor judgment, measurable team outcomes</div>', unsafe_allow_html=True)
