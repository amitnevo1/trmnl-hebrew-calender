import os
import re
import datetime
import pytz
import requests
import recurring_ical_events
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont
from bidi.algorithm import get_display

WIDTH, HEIGHT = 800, 480
TIMEZONE = pytz.timezone("Asia/Jerusalem")
OUTPUT_PATH = "docs/calendar.bmp"

HEBREW_MONTHS = {
    1: "ינואר",
    2: "פברואר",
    3: "מרץ",
    4: "אפריל",
    5: "מאי",
    6: "יוני",
    7: "יולי",
    8: "אוגוסט",
    9: "ספטמבר",
    10: "אוקטובר",
    11: "נובמבר",
    12: "דצמבר",
}
HEBREW_DAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]
HEBREW_TOKEN_RE = re.compile(r"[\u0590-\u05FF]+")


def shape_bidi_text(text):
    if not text:
        return ""
    s = str(text)
    # מעבד כל רצף אותיות עבריות בנפרד כדי שספרות ושעות לא ישבשו את הכיוון
    def _fix_hebrew_word(match):
        return get_display(match.group(0), base_dir="R")

    fixed = HEBREW_TOKEN_RE.sub(_fix_hebrew_word, s)
    return get_display(fixed, base_dir="R")


def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_rtl_sequence(draw, x_right, y, segments, fill=0):
    """
    segments: רשימה של טאפלים (טקסט_ללא_עיבוד, פונט, is_hebrew) בסדר ימין-לשמאל.
    כל מקטע מצויר בנפרד מימין לשמאל כך שאין ערבוב BiDi בין עברית למספרים/אנגלית.
    """
    cursor_x = x_right
    for raw_text, font, is_hebrew in segments:
        if not raw_text:
            continue
        rendered = shape_bidi_text(raw_text) if is_hebrew else str(raw_text)
        draw.text((cursor_x, y), rendered, font=font, fill=fill, anchor="ra")
        cursor_x -= draw.textlength(rendered, font=font) + 5


def truncate_text(draw, text, font, max_width):
    s = str(text).strip()
    if draw.textlength(shape_bidi_text(s), font=font) <= max_width:
        return s
    while len(s) > 1 and draw.textlength(shape_bidi_text(s + ".."), font=font) > max_width:
        s = s[:-1]
    return s + ".."


def fetch_week_events(urls, sunday_start):
    saturday_end = sunday_start + datetime.timedelta(days=7)
    events_by_day = {i: [] for i in range(7)}

    for url in urls:
        clean_url = url.strip().replace("webcal://", "https://")
        if not clean_url:
            continue
        resp = requests.get(clean_url, timeout=15)
        resp.raise_for_status()
        cal = Calendar.from_ical(resp.content)
        occurrences = recurring_ical_events.of(cal).between(sunday_start, saturday_end)

        for event in occurrences:
            summary = str(event.get("SUMMARY", "אירוע")).strip()
            dtstart = event.get("DTSTART").dt
            if isinstance(dtstart, datetime.date) and not isinstance(dtstart, datetime.datetime):
                dt_local = TIMEZONE.localize(datetime.datetime.combine(dtstart, datetime.time.min))
                all_day = True
            else:
                dt_local = dtstart.astimezone(TIMEZONE)
                all_day = False

            day_idx = (dt_local.date() - sunday_start.date()).days
            if 0 <= day_idx < 7:
                events_by_day[day_idx].append((all_day, dt_local, summary))

    for i in range(7):
        events_by_day[i].sort(key=lambda x: (not x[0], x[1]))
    return events_by_day


def render_calendar(events_by_day, now, sunday_start):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    img = Image.new("1", (WIDTH, HEIGHT), 1)
    draw = ImageDraw.Draw(img)

    font_header = get_font(22, bold=True)
    font_sub = get_font(13, bold=False)
    font_day_name = get_font(15, bold=True)
    font_day_num = get_font(17, bold=True)
    font_event = get_font(13, bold=False)
    font_time = get_font(12, bold=True)

    margin_x = 12
    header_h = 46

    # כותרת חודש ושנה מימין, ושעת עדכון משמאל
    draw_rtl_sequence(
        draw,
        WIDTH - margin_x - 4,
        10,
        [(HEBREW_MONTHS[now.month], font_header, True), (str(now.year), font_header, False)],
        fill=0,
    )
    draw_rtl_sequence(
        draw,
        margin_x + 125,
        15,
        [("עודכן", font_sub, True), (now.strftime("%H:%M"), font_sub, False)],
        fill=0,
    )

    grid_top = header_h
    grid_bottom = HEIGHT - 10
    grid_left = margin_x
    grid_right = WIDTH - margin_x
    col_w = (grid_right - grid_left) / 7.0
    day_header_h = 46

    draw.rectangle([grid_left, grid_top, grid_right, grid_bottom], outline=0, width=2)
    draw.line([(grid_left, grid_top + day_header_h), (grid_right, grid_top + day_header_h)], fill=0, width=2)

    for i in range(7):
        col_right = grid_right - i * col_w
        col_left = col_right - col_w
        day_date = sunday_start + datetime.timedelta(days=i)
        is_today = day_date.date() == now.date()

        if i > 0:
            draw.line([(col_right, grid_top), (col_right, grid_bottom)], fill=0, width=1)

        if is_today:
            draw.rectangle([col_left + 1, grid_top + 1, col_right - 1, grid_top + day_header_h - 1], fill=0)
            fg = 1
        else:
            fg = 0

        # כותרת יום: שם היום בעברית ומספר היום מתחתיו
        day_label = shape_bidi_text(HEBREW_DAYS[i])
        cx = (col_left + col_right) / 2.0
        draw.text((cx, grid_top + 5), day_label, font=font_day_name, fill=fg, anchor="ma")
        draw.text((cx, grid_top + 24), str(day_date.day), font=font_day_num, fill=fg, anchor="ma")

        y = grid_top + day_header_h + 6
        max_text_w = col_w - 12
        day_events = events_by_day[i]

        for idx, (all_day, dt_local, summary) in enumerate(day_events):
            if y + 32 > grid_bottom - 4:
                remaining = len(day_events) - idx
                draw_rtl_sequence(
                    draw,
                    col_right - 6,
                    grid_bottom - 18,
                    [("+", font_time, False), ("עוד", font_time, True), (str(remaining), font_time, False)],
                    fill=0,
                )
                break

            if all_day:
                draw.rectangle([col_left + 4, y, col_right - 4, y + 20], fill=0)
                short_summary = truncate_text(draw, summary, font_event, max_text_w - 4)
                draw.text((col_right - 7, y + 2), shape_bidi_text(short_summary), font=font_event, fill=1, anchor="ra")
                y += 24
            else:
                draw.text((col_right - 6, y), dt_local.strftime("%H:%M"), font=font_time, fill=0, anchor="ra")
                y += 14
                short_summary = truncate_text(draw, summary, font_event, max_text_w)
                draw.text((col_right - 6, y), shape_bidi_text(short_summary), font=font_event, fill=0, anchor="ra")
                y += 18
                draw.line([(col_left + 6, y), (col_right - 6, y)], fill=0, width=1)
                y += 4

    img.save(OUTPUT_PATH, format="BMP")


if __name__ == "__main__":
    now = datetime.datetime.now(TIMEZONE)
    days_since_sunday = (now.weekday() + 1) % 7
    sunday_start = (now - datetime.timedelta(days=days_since_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    raw_urls = os.environ.get("ICAL_URLS", "")
    urls = [u for u in raw_urls.replace("\n", ",").split(",") if u.strip()]
    events_by_day = fetch_week_events(urls, sunday_start)
    render_calendar(events_by_day, now, sunday_start)
