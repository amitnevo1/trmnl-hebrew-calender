import os
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
    1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל",
    5: "מאי", 6: "יוני", 7: "יולי", 8: "אוגוסט",
    9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר"
}
HEBREW_DAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]

def fix_hebrew(text):
    if not text:
        return ""
    return get_display(str(text))

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

def truncate_text(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return text
    while len(text) > 1 and draw.textlength(text + "..", font=font) > max_width:
        text = text[:-1]
    return text + ".."

def fetch_week_events(urls, sunday_start):
    saturday_end = sunday_start + datetime.timedelta(days=7)
    events_by_day = {i: [] for i in range(7)}

    for url in urls:
        url = url.strip().replace("webcal://", "https://")
        if not url:
            continue
        resp = requests.get(url, timeout=15)
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
    font_day_num = get_font(18, bold=True)
    font_event = get_font(13, bold=False)
    font_time = get_font(12, bold=True)

    # סרגל עליון (כותרת חודש ועדכון אחרון)
    margin_x = 12
    header_h = 46
    month_title = fix_hebrew(f"{HEBREW_MONTHS[now.month]} {now.year}")
    draw.text((WIDTH - margin_x - 4, 10), month_title, font=font_header, fill=0, anchor="ra")

    updated_str = f"עודכן {now.strftime('%H:%M')}"
    draw.text((margin_x + 4, 15), fix_hebrew(updated_str), font=font_sub, fill=0, anchor="la")

    # גבולות הטבלה
    grid_top = header_h
    grid_bottom = HEIGHT - 10
    grid_left = margin_x
    grid_right = WIDTH - margin_x
    col_w = (grid_right - grid_left) / 7.0
    day_header_h = 48

    # מסגרת חיצונית
    draw.rectangle([grid_left, grid_top, grid_right, grid_bottom], outline=0, width=2)
    draw.line([(grid_left, grid_top + day_header_h), (grid_right, grid_top + day_header_h)], fill=0, width=2)

    # ציור 7 עמודות מימין לשמאל (יום ראשון בימין, שבת בשמאל)
    for i in range(7):
        col_right = grid_right - i * col_w
        col_left = col_right - col_w
        day_date = sunday_start + datetime.timedelta(days=i)
        is_today = (day_date.date() == now.date())

        if i > 0:
            draw.line([(col_right, grid_top), (col_right, grid_bottom)], fill=0, width=1)

        # הדגשת כותרת היום הנוכחי בריבוע שחור עם טקסט לבן
        if is_today:
            draw.rectangle([col_left + 1, grid_top + 1, col_right - 1, grid_top + day_header_h - 1], fill=0)
            text_color = 1
        else:
            text_color = 0

        cx = (col_left + col_right) / 2
        draw.text((cx, grid_top + 6), fix_hebrew(HEBREW_DAYS[i]), font=font_day_name, fill=text_color, anchor="ma")
        draw.text((cx, grid_top + 25), str(day_date.day), font=font_day_num, fill=text_color, anchor="ma")

        # הצגת אירועים בתוך הקובייה של אותו יום
        y = grid_top + day_header_h + 6
        max_text_w = col_w - 10
        day_events = events_by_day[i]

        for idx, (all_day, dt_local, summary) in enumerate(day_events):
            if y + 32 > grid_bottom - 4:
                remaining = len(day_events) - idx
                more_txt = fix_hebrew(f"+ עוד {remaining}")
                draw.text((col_right - 5, grid_bottom - 18), more_txt, font=font_time, fill=0, anchor="ra")
                break

            if all_day:
                # אירוע יום שלם בתוך מסגרת מודגשת
                draw.rectangle([col_left + 4, y, col_right - 4, y + 20], fill=0)
                clean_sum = truncate_text(draw, summary, font_event, max_text_w - 4)
                draw.text((col_right - 7, y + 2), fix_hebrew(clean_sum), font=font_event, fill=1, anchor="ra")
                y += 24
            else:
                # שורה עליונה: שעה, שורה תחתונה: שם האירוע (מונע שיבושי BiDi)
                time_label = dt_local.strftime("%H:%M")
                draw.text((col_right - 5, y), time_label, font=font_time, fill=0, anchor="ra")
                y += 14
                clean_sum = truncate_text(draw, summary, font_event, max_text_w)
                draw.text((col_right - 5, y), fix_hebrew(clean_sum), font=font_event, fill=0, anchor="ra")
                y += 18
                draw.line([(col_left + 6, y), (col_right - 6, y)], fill=0, width=1)
                y += 4

    img.save(OUTPUT_PATH, format="BMP")

if __name__ == "__main__":
    now = datetime.datetime.now(TIMEZONE)
    # חישוב יום ראשון של השבוע הנוכחי (בישראל השבוע מתחיל בראשון)
    days_since_sunday = (now.weekday() + 1) % 7
    sunday_start = (now - datetime.timedelta(days=days_since_sunday)).replace(hour=0, minute=0, second=0, microsecond=0)

    raw_urls = os.environ.get("ICAL_URLS", "")
    urls = [u for u in raw_urls.replace("\n", ",").split(",") if u.strip()]
    events_by_day = fetch_week_events(urls, sunday_start)
    render_calendar(events_by_day, now, sunday_start)