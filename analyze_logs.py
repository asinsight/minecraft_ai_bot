"""
Bot Log Analyzer — Parses bot logs and generates structured reports.

Usage:
    python analyze_logs.py                    # Analyze most recent log
    python analyze_logs.py logs/bot_xxx.log   # Analyze specific log
    python analyze_logs.py --last 500         # Only last 500 ticks
"""

import re
import sys
import os
import glob
from collections import Counter, defaultdict


def find_latest_log():
    """Find the most recently modified log file."""
    logs = glob.glob("logs/bot_*.log")
    if not logs:
        print("No log files found in logs/ directory.")
        sys.exit(1)
    return max(logs, key=os.path.getmtime)


def parse_log(filepath, last_n_ticks=None):
    """Parse a bot log file into structured data."""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    ticks = []
    current_tick = None

    for line in lines:
        # Tick header: 🔄 #123 | 🏆 goal_name progress | Chain: ...
        tick_match = re.search(r'🔄 #(\d+)\s*\|\s*(?:🏆\s*(\S+)\s*([^|]*)\|)?\s*(.*)', line)
        if tick_match:
            if current_tick:
                ticks.append(current_tick)
            current_tick = {
                "num": int(tick_match.group(1)),
                "goal": tick_match.group(2) or "",
                "progress": (tick_match.group(3) or "").strip(),
                "chain_status": (tick_match.group(4) or "").strip(),
                "events": [],
                "raw_lines": [line],
            }
            continue

        if current_tick:
            current_tick["raw_lines"].append(line)

            # Chain started
            if "Started chain:" in line:
                m = re.search(r'Started chain:\s*(\S+)', line)
                if m:
                    current_tick["events"].append(("chain_start", m.group(1)))

            # Chain done
            if "→ done" in line and "Chain:" in line:
                m = re.search(r'Chain:\s*(\S+)', line)
                if m:
                    current_tick["events"].append(("chain_done", m.group(1)))

            # Tool call
            tool_match = re.search(r'🔧\s*\[(\w+)\]\s*(\w+)\(', line)
            if tool_match:
                current_tick["events"].append(("tool_call", tool_match.group(2), tool_match.group(1)))

            # Success
            if "✅" in line:
                current_tick["events"].append(("success", line.strip()))

            # Error/failure
            if "❌" in line:
                current_tick["events"].append(("error", line.strip()))

            # Warning
            if "⚠️" in line:
                current_tick["events"].append(("warning", line.strip()))

            # LLM call
            if "🧠 LLM CALL" in line or "Escalating to LLM" in line:
                current_tick["events"].append(("llm_call", line.strip()))

            # Death
            if "💀" in line:
                current_tick["events"].append(("death", line.strip()))

            # Auto-equip
            if "Auto-equipped" in line or "Best gear equipped" in line:
                current_tick["events"].append(("equip", line.strip()))

    if current_tick:
        ticks.append(current_tick)

    if last_n_ticks and len(ticks) > last_n_ticks:
        ticks = ticks[-last_n_ticks:]

    return ticks


def analyze(ticks):
    """Analyze parsed ticks and return structured report data."""
    report = {}

    # --- Summary ---
    total_ticks = len(ticks)
    first_tick = ticks[0]["num"] if ticks else 0
    last_tick = ticks[-1]["num"] if ticks else 0
    report["total_ticks"] = total_ticks
    report["tick_range"] = (first_tick, last_tick)

    # --- Chain Performance ---
    chain_starts = Counter()
    chain_completions = Counter()
    chain_start_tick = {}  # chain_name -> tick when last started
    chain_durations = defaultdict(list)

    for tick in ticks:
        for event in tick["events"]:
            if event[0] == "chain_start":
                chain_name = event[1]
                chain_starts[chain_name] += 1
                chain_start_tick[chain_name] = tick["num"]
            elif event[0] == "chain_done":
                chain_name = event[1]
                chain_completions[chain_name] += 1
                if chain_name in chain_start_tick:
                    duration = tick["num"] - chain_start_tick[chain_name]
                    chain_durations[chain_name].append(duration)

    report["chain_starts"] = chain_starts
    report["chain_completions"] = chain_completions
    report["chain_durations"] = chain_durations

    # --- Error Analysis ---
    errors = []
    warnings = []
    for tick in ticks:
        for event in tick["events"]:
            if event[0] == "error":
                errors.append((tick["num"], event[1]))
            elif event[0] == "warning":
                warnings.append((tick["num"], event[1]))

    # Normalize error messages for grouping
    def normalize_msg(msg):
        # Remove emoji and leading whitespace
        msg = re.sub(r'^\s*[❌⚠️🔧✅]\s*', '', msg)
        # Truncate long messages
        return msg[:120].strip()

    error_counter = Counter(normalize_msg(e[1]) for e in errors)
    warning_counter = Counter(normalize_msg(w[1]) for w in warnings)
    report["top_errors"] = error_counter.most_common(15)
    report["top_warnings"] = warning_counter.most_common(15)
    report["total_errors"] = len(errors)
    report["total_warnings"] = len(warnings)

    # --- LLM Calls ---
    llm_calls = [(t["num"], e[1]) for t in ticks for e in t["events"] if e[0] == "llm_call"]
    report["llm_calls"] = len(llm_calls)
    report["llm_details"] = llm_calls

    # --- Deaths ---
    deaths = [(t["num"], e[1]) for t in ticks for e in t["events"] if e[0] == "death"]
    report["deaths"] = deaths

    # --- Stuck Loop Detection ---
    # Find sequences where the same chain+step repeats N+ times consecutively
    stuck_loops = []
    prev_status = None
    repeat_start = None
    repeat_count = 0

    for tick in ticks:
        status = tick["chain_status"]
        if status == prev_status and status and "No active" not in status:
            repeat_count += 1
        else:
            if repeat_count >= 3:
                stuck_loops.append({
                    "start_tick": repeat_start,
                    "end_tick": tick["num"] - 1,
                    "count": repeat_count + 1,
                    "status": prev_status,
                })
            repeat_count = 0
            repeat_start = tick["num"]
        prev_status = status

    if repeat_count >= 3:
        stuck_loops.append({
            "start_tick": repeat_start,
            "end_tick": ticks[-1]["num"],
            "count": repeat_count + 1,
            "status": prev_status,
        })

    report["stuck_loops"] = stuck_loops

    # --- Goal Progress ---
    if ticks:
        report["goal_start"] = ticks[0].get("progress", "")
        report["goal_end"] = ticks[-1].get("progress", "")
        report["goal_name"] = ticks[-1].get("goal", "")

    return report


def format_report(report, filepath):
    """Format analysis into markdown report."""
    lines = []
    lines.append("# Bot Log Analysis Report")
    lines.append("")
    lines.append(f"**Log file**: `{filepath}`")
    lines.append(f"**Tick range**: #{report['tick_range'][0]} — #{report['tick_range'][1]} ({report['total_ticks']} ticks)")
    if report.get("goal_name"):
        lines.append(f"**Goal**: {report['goal_name']} | Start: {report.get('goal_start', '?')} → End: {report.get('goal_end', '?')}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    total_starts = sum(report["chain_starts"].values())
    total_completions = sum(report["chain_completions"].values())
    total_fails = total_starts - total_completions
    lines.append(f"- Chains started: **{total_starts}**, Completed: **{total_completions}**, Failed: **{total_fails}**")
    lines.append(f"- Errors: **{report['total_errors']}**, Warnings: **{report['total_warnings']}**")
    lines.append(f"- LLM calls: **{report['llm_calls']}**")
    lines.append(f"- Deaths: **{len(report['deaths'])}**")
    lines.append(f"- Stuck loops detected: **{len(report['stuck_loops'])}**")
    lines.append("")

    # Chain Performance Table
    lines.append("## Chain Performance")
    lines.append("| Chain | Started | Done | Failed | Avg Ticks | Success Rate |")
    lines.append("|-------|---------|------|--------|-----------|--------------|")
    all_chains = set(list(report["chain_starts"].keys()) + list(report["chain_completions"].keys()))
    for chain in sorted(all_chains, key=lambda c: report["chain_starts"].get(c, 0), reverse=True):
        started = report["chain_starts"].get(chain, 0)
        done = report["chain_completions"].get(chain, 0)
        failed = started - done
        durations = report["chain_durations"].get(chain, [])
        avg_dur = f"{sum(durations)/len(durations):.1f}" if durations else "-"
        rate = f"{done/started*100:.0f}%" if started > 0 else "-"
        lines.append(f"| {chain} | {started} | {done} | {failed} | {avg_dur} | {rate} |")
    lines.append("")

    # Top Errors
    if report["top_errors"]:
        lines.append("## Top Errors (by frequency)")
        for msg, count in report["top_errors"]:
            lines.append(f"- **[{count}x]** {msg}")
        lines.append("")

    # Top Warnings
    if report["top_warnings"]:
        lines.append("## Top Warnings (by frequency)")
        for msg, count in report["top_warnings"][:10]:
            lines.append(f"- **[{count}x]** {msg}")
        lines.append("")

    # Stuck Loops
    if report["stuck_loops"]:
        lines.append("## Stuck Loops")
        for loop in report["stuck_loops"]:
            lines.append(f"- **Tick #{loop['start_tick']}–#{loop['end_tick']}** ({loop['count']} consecutive): `{loop['status']}`")
        lines.append("")

    # LLM Calls
    if report["llm_details"]:
        lines.append("## LLM Escalations")
        for tick_num, detail in report["llm_details"]:
            lines.append(f"- Tick #{tick_num}: {detail[:120]}")
        lines.append("")

    # Deaths
    if report["deaths"]:
        lines.append("## Deaths")
        for tick_num, detail in report["deaths"]:
            lines.append(f"- Tick #{tick_num}: {detail[:120]}")
        lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    recommendations = generate_recommendations(report)
    for rec in recommendations:
        lines.append(f"- {rec}")
    if not recommendations:
        lines.append("- No critical issues detected.")
    lines.append("")

    return "\n".join(lines)


def generate_recommendations(report):
    """Generate actionable recommendations based on analysis."""
    recs = []

    # Check for place_block failures
    for msg, count in report["top_errors"] + report["top_warnings"]:
        if "place_block" in msg.lower() or "no suitable position" in msg.lower():
            recs.append(f"place_block fails {count}x → check server.js /action/place underground support")
            break

    for msg, count in report["top_errors"] + report["top_warnings"]:
        if "no furnace" in msg.lower() or "no crafting" in msg.lower():
            recs.append(f"Missing furnace/crafting_table {count}x → check chain_executor.py _ensure_furnace/_ensure_crafting_table")
            break

    # Check for chains that never succeed
    for chain, started in report["chain_starts"].items():
        done = report["chain_completions"].get(chain, 0)
        if started >= 3 and done == 0:
            recs.append(f"`{chain}` started {started}x but never completed → investigate chain steps and error patterns")

    # Stuck loops
    if report["stuck_loops"]:
        total_stuck_ticks = sum(l["count"] for l in report["stuck_loops"])
        recs.append(f"{len(report['stuck_loops'])} stuck loops wasted {total_stuck_ticks} ticks → check retry/escalation logic")

    # High LLM usage
    if report["total_ticks"] > 0:
        llm_rate = report["llm_calls"] / report["total_ticks"] * 100
        if llm_rate > 5:
            recs.append(f"LLM call rate {llm_rate:.1f}% of ticks → chains may be failing too often, check auto-fix coverage")

    # Deaths
    if len(report["deaths"]) >= 3:
        recs.append(f"{len(report['deaths'])} deaths → review instinct layer combat/flee thresholds")

    return recs


def main():
    last_n = None
    filepath = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--last" and i + 1 < len(args):
            last_n = int(args[i + 1])
            i += 2
        else:
            filepath = args[i]
            i += 1

    if not filepath:
        filepath = find_latest_log()

    print(f"Analyzing: {filepath}")
    ticks = parse_log(filepath, last_n)

    if not ticks:
        print("No ticks found in log file.")
        sys.exit(1)

    print(f"Parsed {len(ticks)} ticks (#{ticks[0]['num']}–#{ticks[-1]['num']})")

    report = analyze(ticks)
    markdown = format_report(report, filepath)

    report_path = "report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"Report saved to: {report_path}")
    print()
    print(markdown)


if __name__ == "__main__":
    main()
