#!/usr/bin/env python3
"""CliffPulse: an evidence-first editorial review queue for timestamped scripts.

Rules identify review candidates, not audience outcomes or verified story defects.
All quoted evidence comes from input files. Source files are never modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SCHEMA_VERSION = 3
ENGINE_VERSION = "3.0.0"
PACKAGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = PACKAGE_DIR / "sample_scripts" if (PACKAGE_DIR / "sample_scripts").is_dir() else PACKAGE_DIR.parent
DEFAULT_RUNS_DIR = PACKAGE_DIR / "runs"
ENDING_WINDOW_SECONDS = 15.0
OPENING_SCAN_SECONDS = 60.0
# Exact text cues, intentionally small and inspectable. These are not learned weights.
CLOSURE_CUES = (
    "peaceful", "grateful smile", "gentle string", "fading out into silence",
    "slowly fades to black", "fades out", "fade out", "fade-out", "resolving chord",
    "exhausted relief", "quiet resolve", "tender orchestral", "music swells warmly",
    "lingers and fades", "warm, peaceful", "relieved smile", "contented", "peacefully",
)
TENSION_CUES = (
    "cliffhanger", "abrupt cut", "cutting abruptly", "freeze-frame", "dread",
    "terrified", "horrified", "ominous", "sinister", "urgent", "alarm", "threatening",
    "malicious", "predatory", "menacing", "terror", "arrest", "betrayal",
    "weapon", "screaming", "danger", "murder", "unanswered",
)
# Removing camera grammar and common function words reduces production-style matches.
STOP_WORDS = set("""a an the this that these those and or but if then as at by for from in into of on onto
with without to toward towards under over through while before after is are was were be been being
his her their its he she they him them it you your yours my me mine our we us i not no very now
camera action sound fx scene start end shot close up medium wide low high angle extreme macro
framing frame cut black slowly sudden suddenly holds holding hold focus focused camera tracks
tracking pan panning push zoom insert two reverse view profile continuous day night interior exterior
""".split())


def parse_ts(value: str) -> float:
    match = re.fullmatch(r"(\d+):([0-5]?\d(?:\.\d+)?)", value.strip())
    if not match:
        raise ValueError(f"Invalid timestamp {value!r}; expected MM:SS or MM:SS.xx")
    return int(match.group(1)) * 60 + float(match.group(2))


def format_ts(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


def cue_matches(text: str, cues: tuple[str, ...]) -> list[str]:
    return [cue for cue in cues if re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", text, re.I)]


def block_kind(tag: str) -> str:
    upper = tag.upper()
    if upper.startswith("CAMERA"):
        return "CAMERA"
    if upper.startswith("ACTION"):
        return "ACTION"
    if upper.startswith(("SOUND", "MUSIC")):
        return "SOUND"
    return "DIALOGUE"


def parse_episode_file(filepath: str | Path, ep_index: int | None = None) -> dict:
    path = Path(filepath)
    source_bytes = path.read_bytes()
    raw = source_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    number = re.search(r"^EPISODE NUMBER:[ \t]*(?:Episode[ \t]*)?(\d+)\b", raw, re.M | re.I)
    filename_number = re.search(r"(?:episode|ep)[-_ ]*(\d+)", path.name, re.I)
    if number:
        episode_id = int(number.group(1))
    elif filename_number:
        episode_id = int(filename_number.group(1))
    elif ep_index is not None:
        episode_id = int(ep_index)
    else:
        raise ValueError(f"{path.name}: no numeric episode ID in metadata or filename")
    if episode_id < 1:
        raise ValueError(f"{path.name}: episode ID must be positive")

    scenes = []
    scene_pattern = re.compile(
        r"^SCENE[ \t]+(\d+)[ \t]*\nScene heading:[ \t]*(.*?)\n"
        r"Scene start:[ \t]*(.*?)\nScene end:[ \t]*(.*?)(?:\n|$)", re.M | re.I,
    )
    for scene_match in scene_pattern.finditer(raw):
        start, end = parse_ts(scene_match[3]), parse_ts(scene_match[4])
        if end < start:
            raise ValueError(f"{path.name}: scene end precedes start")
        scenes.append({"scene_id": int(scene_match[1]), "heading": scene_match[2].strip(),
                       "start_sec": start, "end_sec": end, "source_offset": scene_match.start()})
    scene_headers = len(re.findall(r"^SCENE[ \t]+\d+\b", raw, re.M | re.I))
    if not scenes or scene_headers != len(scenes):
        raise ValueError(f"{path.name}: missing or malformed scene headers; see input format")
    scene_ids = [scene["scene_id"] for scene in scenes]
    if len(set(scene_ids)) != len(scene_ids):
        raise ValueError(f"{path.name}: duplicate scene IDs; each scene must have a unique number")

    block_pattern = re.compile(
        r"^\*\*([^*]+):\*\*[ \t]*\nStart:[ \t]*\(([^)]+)\)[ \t]*\|[ \t]*End:[ \t]*\(([^)]+)\)[ \t]*\n"
        r"(.*?)(?=^\*\*|^SCENE[ \t]+\d+|^--- SCRIPT END ---|\Z)", re.M | re.S,
    )
    blocks = []
    parsed_tag_starts = set()
    parsed_time_starts = set()
    for match in block_pattern.finditer(raw):
        parsed_tag_starts.add(match.start())
        parsed_time_starts.add(raw.index("\n", match.start()) + 1)
        start, end = parse_ts(match[2]), parse_ts(match[3])
        if end < start:
            raise ValueError(f"{path.name}: block end precedes start at line {raw[:match.start()].count(chr(10)) + 1}")
        body = match[4].strip()
        if not body:
            raise ValueError(f"{path.name}: empty screenplay block")
        preceding = [scene for scene in scenes if scene["source_offset"] < match.start()]
        if not preceding:
            raise ValueError(f"{path.name}: screenplay block appears before any scene header")
        scene = preceding[-1]
        tag = match[1].strip()
        blocks.append({
            "block_id": len(blocks) + 1, "tag": tag, "kind": block_kind(tag),
            "start_str": match[2].strip(), "end_str": match[3].strip(),
            "start_sec": start, "end_sec": end, "text": body,
            "scene_id": scene["scene_id"], "scene_heading": scene["heading"],
            "source_line": raw[:match.start()].count("\n") + 1,
        })
    # Recognize malformed bold tag lines as well, so an omitted colon cannot
    # silently remove a source block from the audit.
    all_tag_starts = {match.start() for match in re.finditer(r"^\*\*", raw, re.M)}
    all_time_starts = {match.start() for match in re.finditer(r"^(?:Start|End)[ \t]*:", raw, re.M | re.I)}
    if not blocks or all_tag_starts != parsed_tag_starts:
        raise ValueError(f"{path.name}: not all tagged blocks have valid Start/End timestamps")
    if all_time_starts != parsed_time_starts:
        raise ValueError(f"{path.name}: unparsed Start/End timestamp line; every timed block needs a valid bold tag")
    logline_match = re.search(r"^EPISODE LOGLINE:[ \t]*(.*)", raw, re.M | re.I)
    duration = max([b["end_sec"] for b in blocks] + [s["end_sec"] for s in scenes])
    return {
        "episode_index": episode_id, "episode_label": f"Episode {episode_id}", "file_name": path.name,
        "logline": logline_match[1].strip() if logline_match else "",
        "duration_sec": round(duration, 3), "duration_formatted": format_ts(duration),
        "scenes": scenes, "blocks": blocks, "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }


def ending_blocks(episode: dict, window_seconds: float = ENDING_WINDOW_SECONDS) -> list[dict]:
    boundary = max(0, episode["duration_sec"] - window_seconds)
    return [block for block in episode["blocks"] if block["end_sec"] > boundary]


def evidence_block(block: dict) -> dict:
    result = dict(block)
    signal_text = block["tag"] + " " + block["text"]
    result["closure_cues"] = cue_matches(signal_text, CLOSURE_CUES)
    result["tension_cues"] = cue_matches(signal_text, TENSION_CUES)
    return result


def next_episode_candidate(episode: dict | None) -> dict | None:
    if episode is None:
        return None
    first_scene = episode["scenes"][0]["scene_id"]
    candidates = []
    for block in episode["blocks"]:
        if block["scene_id"] != first_scene or block["start_sec"] >= OPENING_SCAN_SECONDS:
            continue
        evidence = evidence_block(block)
        if evidence["tension_cues"]:
            candidates.append(evidence)
    if not candidates:
        return None
    selected = max(candidates, key=lambda item: (len(item["tension_cues"]), -item["start_sec"]))
    return {
        "episode_index": episode["episode_index"], "scene_heading": selected["scene_heading"],
        "evidence": selected,
        "selection_reason": "Most registered tension cues in a block within scene 1 and the first 60 seconds; ties prefer earlier blocks.",
        "editor_note": "Source context only. Check continuity and performance before considering any edit; nothing has been moved or rewritten.",
    }


def audit_episode(episode: dict, next_episode: dict | None, paywall_start: int, paywall_end: int) -> dict:
    evidence = [evidence_block(block) for block in ending_blocks(episode)]
    closure = sorted({cue for block in evidence for cue in block["closure_cues"]})
    tension = sorted({cue for block in evidence for cue in block["tension_cues"]})
    if closure and not tension:
        status, finding = "REVIEW_CLOSURE", "Closure or fade cues occur in source blocks overlapping the final 15 seconds without a registered tension cue."
    elif closure and tension:
        status, finding = "MIXED_SIGNALS", "Both closure and tension cues occur in source blocks overlapping the final 15 seconds. Context decides whether the ending works."
    elif tension:
        status, finding = "TENSION_CUES_PRESENT", "Registered tension cues occur in source blocks overlapping the final 15 seconds. This does not establish hook quality."
    else:
        status, finding = "EDITOR_REVIEW", "The cue list is inconclusive. Quiet promises, questions, or subtle threats may still make a valid hook."
    candidate = next_episode_candidate(next_episode)
    review = status == "REVIEW_CLOSURE"
    action = (
        "Read the ending in context and decide whether resolution is intentional. If a stronger transition is needed, compare the quoted next-episode material; record an editor-approved change separately."
        if review else
        "Assess the ending in story context. Keep valid quiet hooks and deliberate closure; a keyword label is not a verdict."
    )
    if next_episode is None:
        action += " No consecutive following episode was supplied; check whether this is a finale or an incomplete pack."
    return {
        "schema_version": SCHEMA_VERSION, "episode_index": episode["episode_index"],
        "episode_label": episode["episode_label"], "file_name": episode["file_name"], "logline": episode["logline"],
        "duration_sec": episode["duration_sec"], "duration_formatted": episode["duration_formatted"],
        "scene_count": len(episode["scenes"]), "scene_headings": [s["heading"] for s in episode["scenes"]],
        "is_paywall_conversion_zone": paywall_start <= episode["episode_index"] <= paywall_end,
        "ending_window_start_sec": max(0, episode["duration_sec"] - ENDING_WINDOW_SECONDS),
        "ending_status": status, "needs_ending_review": review,
        "closure_cues": closure, "tension_cues": tension, "ending_evidence": evidence,
        "next_episode_candidate": candidate,
        "editor_brief": {"finding": finding, "review_action": action,
                         "source_basis": "Exact input excerpts and their original timestamps; no generated dialogue.",
                         "automatic_edit_applied": False},
        "source_sha256": episode["source_sha256"],
    }


def content_tokens(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 2 and word not in STOP_WORDS]


def similarity(a: dict, b: dict) -> float:
    words_a, words_b = content_tokens(a["text"]), content_tokens(b["text"])
    if min(len(words_a), len(words_b)) < 5:
        return 0.0
    # Content-token overlap is evidence of similar wording, not a semantic duplicate verdict.
    set_a, set_b = set(words_a), set(words_b)
    return len(set_a & set_b) / len(set_a | set_b)


def find_repetition(episodes: list[dict]) -> list[dict]:
    reports = []
    for a, b in zip(episodes, episodes[1:]):
        if b["episode_index"] != a["episode_index"] + 1:
            continue
        matches = []
        # Soundtrack instructions often repeat harmlessly; only visual/action endings are compared here.
        left = [block for block in ending_blocks(a) if block["kind"] in {"CAMERA", "ACTION"}]
        right = [block for block in ending_blocks(b) if block["kind"] in {"CAMERA", "ACTION"}]
        for block_a in left:
            for block_b in right:
                value = similarity(block_a, block_b)
                if value >= 0.42:
                    matches.append({"kind": "ENDING_TEXT_OVERLAP", "text_similarity": round(value, 4),
                                    "episode_a_block": block_a, "episode_b_block": block_b})
        # Dialogue candidates need substantial near-identical phrasing, not just shared character names.
        dialogue_a = [block for block in a["blocks"] if block["kind"] == "DIALOGUE"]
        dialogue_b = [block for block in b["blocks"] if block["kind"] == "DIALOGUE"]
        for block_a in dialogue_a:
            words_a = re.findall(r"[a-z]+", block_a["text"].lower())
            if len(words_a) < 8:
                continue
            for block_b in dialogue_b:
                words_b = re.findall(r"[a-z]+", block_b["text"].lower())
                if len(words_b) < 8:
                    continue
                ratio = SequenceMatcher(None, words_a, words_b, autojunk=False).ratio()
                if ratio >= 0.88 and similarity(block_a, block_b) >= 0.6:
                    matches.append({"kind": "NEAR_DUPLICATE_DIALOGUE", "text_similarity": round(ratio, 4),
                                    "episode_a_block": block_a, "episode_b_block": block_b})
        if not matches:
            continue
        matches.sort(key=lambda item: item["text_similarity"], reverse=True)
        matches = matches[:8]
        reports.append({
            "pair_id": f"PAIR_{a['episode_index']:03d}_{b['episode_index']:03d}",
            "episode_a": a["episode_index"], "episode_b": b["episode_index"],
            "episodes": f"Episode {a['episode_index']} → Episode {b['episode_index']}",
            "finding_type": "REPEATED_ENDING_CANDIDATE" if any(m["kind"] == "ENDING_TEXT_OVERLAP" for m in matches) else "REPEATED_DIALOGUE_CANDIDATE",
            "locations": sorted({s["heading"] for s in a["scenes"] + b["scenes"]}),
            "combined_duration_sec": round(a["duration_sec"] + b["duration_sec"], 3),
            "max_text_similarity": max(m["text_similarity"] for m in matches), "evidence_matches": matches,
            "review_action": "Compare the exact matched passages. Confirm whether this is intentional recap, a useful repeated motif, or redundant story material. Select any cut boundaries only after editorial review.",
            "limitation": "Text similarity cannot determine whether the story has advanced. Shared locations alone do not create a flag; no cut length or saved time is inferred.",
        })
    return reports


def review_queue_markdown(summary: dict, audits: list[dict], repetitions: list[dict]) -> str:
    lines = [f"# CliffPulse editorial review · {summary['show_title']}", "",
             "This file contains source evidence and review questions. No screenplay has been rewritten.", "",
             f"Configured paywall window: episodes {summary['paywall_zone_start']}–{summary['paywall_zone_end']} (an analysis assumption).", ""]
    for audit in audits:
        if not audit["needs_ending_review"]:
            continue
        lines += [f"## Episode {audit['episode_index']} · ending review", "", audit["editor_brief"]["finding"], ""]
        for block in audit["ending_evidence"]:
            lines += [f"**{block['tag']} · {block['start_str']}–{block['end_str']} · source line {block['source_line']}**", "", block["text"], ""]
        lines += ["**Editor decision:** [ ] keep [ ] revise [ ] needs context", "", "**Notes / approved edit:**", ""]
    for pair in repetitions:
        lines += [f"## {pair['episodes']} · repetition review", "", pair["review_action"], ""]
        for match in pair["evidence_matches"]:
            lines += [f"**{match['kind']} · text similarity {match['text_similarity']:.0%}**", ""]
            for episode_id, key in [(pair["episode_a"], "episode_a_block"), (pair["episode_b"], "episode_b_block")]:
                block = match[key]
                lines += [f"Episode {episode_id} · {block['start_str']}–{block['end_str']} · {block['tag']}", "", block["text"], ""]
        lines += ["**Editor decision:** [ ] intentional repetition [ ] revise [ ] needs context", "", "**Notes / approved edit:**", ""]
    return "\n".join(lines)


def run_audit(input_dir: str | Path, output_dir: str | Path | None = None, paywall_start: int = 4,
              paywall_end: int = 10, show_title: str | None = None, input_pattern: str = "*.txt") -> Path:
    started = time.perf_counter()
    source = Path(input_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Input folder does not exist: {source}")
    if paywall_start < 1 or paywall_end < paywall_start:
        raise ValueError("Paywall range must be positive and end on or after its start")
    files = sorted(path for path in source.glob(input_pattern) if path.is_file())
    if not files:
        raise ValueError(f"No files match {input_pattern!r} in the input folder")
    episodes = sorted((parse_episode_file(path) for path in files), key=lambda item: item["episode_index"])
    ids = [ep["episode_index"] for ep in episodes]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate episode IDs: use unique numeric episode metadata or filenames")
    title = show_title or source.name
    if output_dir:
        target = Path(output_dir).expanduser().resolve()
        if target == source:
            raise ValueError("Output folder must differ from input folder")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError("Output folder already exists and is not empty; choose a new run folder")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "scripts"
        target = DEFAULT_RUNS_DIR / f"{stamp}-{slug}"
    audits = []
    for index, episode in enumerate(episodes):
        next_episode = episodes[index + 1] if index + 1 < len(episodes) and ids[index + 1] == episode["episode_index"] + 1 else None
        audits.append(audit_episode(episode, next_episode, paywall_start, paywall_end))
    repetitions = find_repetition(episodes)
    paywall_ids = [ep_id for ep_id in ids if paywall_start <= ep_id <= paywall_end]
    gaps = sorted(set(range(ids[0], ids[-1] + 1)) - set(ids))
    statuses = {status: sum(a["ending_status"] == status for a in audits) for status in (
        "REVIEW_CLOSURE", "MIXED_SIGNALS", "TENSION_CUES_PRESENT", "EDITOR_REVIEW")}
    summary = {
        "schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
        "show_title": title, "episodes_analyzed": len(audits),
        "total_runtime_minutes": round(sum(ep["duration_sec"] for ep in episodes) / 60, 2),
        "paywall_zone_start": paywall_start, "paywall_zone_end": paywall_end, "paywall_zone_episodes": paywall_ids,
        "ending_review_count": sum(a["needs_ending_review"] for a in audits),
        "paywall_ending_review_count": sum(a["needs_ending_review"] and a["is_paywall_conversion_zone"] for a in audits),
        "repetition_review_count": len(repetitions), "ending_status_counts": statuses,
        "ending_review_episode_numbers": [a["episode_index"] for a in audits if a["needs_ending_review"]],
        "methodology": {
            "ending_window_seconds": ENDING_WINDOW_SECONDS,
            "ending_rule": "Closure cue present and no tension cue in any block intersecting the final 15 seconds creates an ending-review candidate. No probability or retention score is computed.",
            "opening_scan": "First scene, blocks starting before 60 seconds; only blocks with a registered tension cue can be suggested as context.",
            "ending_repetition": "Adjacent visual/action ending blocks: content-token Jaccard ≥ 0.42, at least five content tokens per block.",
            "dialogue_repetition": "Adjacent episodes: at least eight words per dialogue block, sequence similarity ≥ 0.88 and content-token Jaccard ≥ 0.60.",
            "paywall_note": "The chosen episode range is a configurable assumption, not a verified platform gate.",
            "closure_cues": list(CLOSURE_CUES), "tension_cues": list(TENSION_CUES),
        },
        "limitations": [
            "Keyword cues cannot determine narrative quality, unresolved stakes, or actual viewer retention.",
            "Text similarity can reflect intentional recaps or motifs; an editor decides whether a repetition is useful.",
            "Only the supplied structured UTF-8 text screenplay format is supported; no video or performance is evaluated.",
            "No dialogue is generated, no script is edited, and no time saving or retention uplift is estimated.",
            "No independent editor labels or live audience experiment have been collected in this prototype.",
        ],
        "missing_episode_ids": gaps,
    }
    target.mkdir(parents=True, exist_ok=True)
    (target / "episodes_json").mkdir()
    for audit in audits:
        write_json(target / "episodes_json" / f"episode_{audit['episode_index']:03d}_retention_audit.json", audit)
    write_json(target / "master_retention_and_stalling_report.json", summary)
    write_json(target / "multi_episode_stalling_cut_sheets.json", repetitions)
    with (target / "episode_retention_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Episode", "File", "Duration_Sec", "Scenes", "Paywall_Conversion_Zone", "Ending_Status", "Needs_Ending_Review", "Closure_Cue_Count", "Tension_Cue_Count"])
        for audit in audits:
            writer.writerow([audit["episode_index"], audit["file_name"], audit["duration_sec"], audit["scene_count"],
                             audit["is_paywall_conversion_zone"], audit["ending_status"], audit["needs_ending_review"],
                             len(audit["closure_cues"]), len(audit["tension_cues"])])
    (target / "review_queue.md").write_text(review_queue_markdown(summary, audits, repetitions), encoding="utf-8")
    elapsed = round(time.perf_counter() - started, 4)
    write_json(target / "run_manifest.json", {
        "schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
        "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python_version": sys.version.split()[0],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed, "input_folder_name": source.name, "input_pattern": input_pattern,
        "paywall_start": paywall_start, "paywall_end": paywall_end,
        "source_files": [{"episode_index": ep["episode_index"], "file_name": ep["file_name"], "sha256": ep["source_sha256"]} for ep in episodes],
        "input_scripts_modified": False, "missing_episode_ids": gaps,
    })
    write_json(target / "quality_report.json", {
        "schema_version": SCHEMA_VERSION, "report_kind": "input_integrity_checks",
        "files_parsed": len(episodes), "tagged_blocks_parsed": sum(len(ep["blocks"]) for ep in episodes),
        "scenes_parsed": sum(len(ep["scenes"]) for ep in episodes),
        "unique_episode_ids": True, "all_tagged_blocks_have_valid_timestamps": True,
        "source_hashes_recorded": True, "missing_episode_ids": gaps,
        "editor_accuracy_evaluation": "Not yet performed; automated parser checks do not establish detection accuracy.",
    })
    return target


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create an evidence-based editorial review queue from timestamped scripts.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", help="New or empty run folder; default creates a unique folder under runs/.")
    parser.add_argument("--input-pattern", default="*.txt")
    parser.add_argument("--paywall-start", type=int, default=4)
    parser.add_argument("--paywall-end", type=int, default=10)
    parser.add_argument("--show-title")
    args = parser.parse_args(argv)
    try:
        target = run_audit(args.input_dir, args.output_dir, args.paywall_start, args.paywall_end, args.show_title, args.input_pattern)
    except (ValueError, OSError, UnicodeError) as error:
        parser.error(str(error))
    print(f"Audit complete. Output: {target}")


if __name__ == "__main__":
    main()
