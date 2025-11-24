# app.py
import streamlit as st
import requests
from icalendar import Calendar
from datetime import datetime, time, timedelta
import pytz
import recurring_ical_events
import pandas as pd

st.set_page_config(page_title="Free Time Finder", layout="wide")

# -----------------------
# Helper functions
# -----------------------
@st.cache_data(show_spinner=False)
def fetch_calendar_bytes(ics_url: str):
    """Fetch ICS bytes (cached)."""
    resp = requests.get(ics_url, timeout=20)
    resp.raise_for_status()
    return resp.content


def compute_free_slots(
    start_date,
    end_date,
    ics_bytes,
    start_hour=9,
    end_hour=17,
    timezone_str="US/Pacific",
    min_free_minutes=30
):
    cal = Calendar.from_ical(ics_bytes)
    tz = pytz.timezone(timezone_str)

    free_slots_by_day = {}
    current_day = start_date

    while current_day <= end_date:
        work_start = tz.localize(datetime.combine(current_day, time(start_hour, 0)))
        work_end = tz.localize(datetime.combine(current_day, time(end_hour, 0)))

        events_today = recurring_ical_events.of(cal).between(work_start, work_end)
        busy_events = []

        for event in events_today:
            status = str(event.get("status")).upper() if event.get("status") else "BUSY"
            if status in ["TENTATIVE", "CANCELLED"]:
                continue

            e_start = event.get("dtstart").dt
            e_end = event.get("dtend").dt
            if not isinstance(e_start, datetime) or not isinstance(e_end, datetime):
                continue

            if e_start.tzinfo is None:
                e_start = pytz.UTC.localize(e_start)
            if e_end.tzinfo is None:
                e_end = pytz.UTC.localize(e_end)

            e_start = e_start.astimezone(tz)
            e_end = e_end.astimezone(tz)

            if e_end.date() < current_day or e_start.date() > current_day:
                continue

            e_start_clamped = max(e_start, work_start)
            e_end_clamped = min(e_end, work_end)

            if e_start_clamped < e_end_clamped:
                busy_events.append((e_start_clamped, e_end_clamped))

        busy_events.sort(key=lambda x: x[0])
        merged = []
        for ev in busy_events:
            if not merged:
                merged.append(ev)
            else:
                last_start, last_end = merged[-1]
                if ev[0] <= last_end:
                    merged[-1] = (last_start, max(last_end, ev[1]))
                else:
                    merged.append(ev)

        free_slots = []
        current = work_start
        min_duration = timedelta(minutes=min_free_minutes)

        for start, end in merged:
            if current < start and (start - current) >= min_duration:
                free_slots.append((current, start))
            current = max(current, end)

        if current < work_end and (work_end - current) >= min_duration:
            free_slots.append((current, work_end))

        free_slots_by_day[current_day] = free_slots
        current_day += timedelta(days=1)

    return free_slots_by_day


def slots_to_dataframe(slots_by_day, timezone_str):
    rows = []
    tz = pytz.timezone(timezone_str)
    for day, slots in slots_by_day.items():
        for s, e in slots:
            duration = int((e - s).total_seconds() // 60)
            rows.append({
                "date": day.isoformat(),
                "start": s.strftime("%Y-%m-%d %H:%M"),
                "end": e.strftime("%Y-%m-%d %H:%M"),
                "duration_minutes": duration
            })
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=["date", "start", "end", "duration_minutes"])


# -----------------------
# Markdown & plaintext tables
# -----------------------
def to_markdown_table(slots_by_day):
    md = ""
    for day, slots in slots_by_day.items():
        md += f"### {day.isoformat()}\n"
        if not slots:
            md += "_No free slots_\n\n"
            continue
        md += "| Start | End | Duration (min) |\n"
        md += "|-------|------|----------------|\n"
        for s, e in slots:
            duration = int((e - s).total_seconds() // 60)
            md += f"| {s.strftime('%I:%M %p')} | {e.strftime('%I:%M %p')} | {duration} |\n"
        md += "\n"
    return md


def to_plaintext_table(slots_by_day):
    txt = ""
    for day, slots in slots_by_day.items():
        txt += f"{day.isoformat()}\n"
        txt += "-" * 40 + "\n"
        if not slots:
            txt += "No free slots\n\n"
            continue
        txt += f"{'Start':<12}{'End':<12}{'Duration(min)'}\n"
        txt += f"{'-'*12}{'-'*12}{'-'*14}\n"
        for s, e in slots:
            duration = int((e - s).total_seconds() // 60)
            txt += f"{s.strftime('%I:%M %p'):<12}{e.strftime('%I:%M %p'):<12}{duration:<14}\n"
        txt += "\n"
    return txt


# -----------------------
# Copy-to-clipboard component
# -----------------------
def copy_button(text_to_copy, label="Copy to clipboard"):
    encoded = text_to_copy.replace("'", "\\'")
    html = f"""
        <button onclick="navigator.clipboard.writeText('{encoded}')"
        style="padding:8px 16px;border:none;background:#4CAF50;color:white;border-radius:6px;cursor:pointer;">
            {label}
        </button>
    """
    st.markdown(html, unsafe_allow_html=True)


# -----------------------
# UI
# -----------------------
st.title("🕒 Free Time Finder — Streamlit")

with st.sidebar:
    st.header("Inputs")
    ics_url = st.text_input("ICS URL", value="")
    start_date = st.date_input("Start date", value=datetime.now().date())
    end_date = st.date_input("End date", value=datetime.now().date() + timedelta(days=7))

    timezone_str = st.selectbox(
        "Timezone", options=pytz.all_timezones,
        index=pytz.all_timezones.index("US/Pacific")
    )

    start_hour = st.number_input("Workday start hour", min_value=0, max_value=23, value=9)
    end_hour = st.number_input("Workday end hour", min_value=1, max_value=24, value=17)
    min_free_minutes = st.slider("Min free slot (minutes)", 5, 240, 30, 5)

    submit = st.button("Fetch & Compute")


if submit:
    if not ics_url.startswith(("http://", "https://")):
        st.error("Invalid ICS URL.")
        st.stop()

    with st.spinner("Fetching calendar..."):
        ics_bytes = fetch_calendar_bytes(ics_url)

    with st.spinner("Calculating free slots..."):
        slots_by_day = compute_free_slots(
            start_date, end_date, ics_bytes,
            start_hour, end_hour, timezone_str, min_free_minutes
        )

    df = slots_to_dataframe(slots_by_day, timezone_str)
    st.success("Done!")

    st.subheader("Free Slots Summary")
    st.dataframe(df, use_container_width=True)

    # -----------------------------------------
    # Copy/paste friendly tables
    # -----------------------------------------
    st.subheader("📋 Markdown Table (for Slack/Notion/GitHub)")
    markdown_tables = to_markdown_table(slots_by_day)
    st.code(markdown_tables, language="markdown")
    copy_button(markdown_tables, "Copy Markdown")

    st.subheader("📋 Plain Text Table (for Email/SMS)")
    plaintext_tables = to_plaintext_table(slots_by_day)
    st.code(plaintext_tables, language="text")
    copy_button(plaintext_tables, "Copy Plain Text")

else:
    st.info("Fill out the sidebar and click *Fetch & Compute*.")
