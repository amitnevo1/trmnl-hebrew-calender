import os
import datetime
import pytz
import requests
import recurring_ical_events
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont
from bidi.algorithm import get_display  # pip install python-bidi

WIDTH, HEIGHT = 800, 480
TIMEZONE = pytz.timezone("Asia/Jerusalem")
OUTPUT_PATH = "docs/calendar.bmp"

HEBREW_MONTHS = {
    1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל",
    5: "מאי", 6: "יוני", 7: "יולי", 8: "אוגוסט",
    9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר"
}
HEBREW_DAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]


def reverse_hebrew_visually(text):
    """
    ממיר טקסט לוגי לסדר חזותי (bidi) עם כיוון בסיס RTL.
    חשוב: הפונטים נטענים עם Layout.BASIC כדי ש-Pillow/raqm
    לא יעשה bidi נוסף ויהפוך שוב (היפוך כפול).
    """
    if not text:
        return ""
    return get_display(str(text), base_dir="R")


def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
    print("WARNING: no Hebrew-capable font found, install fonts-freefont-ttf or fonts-dejavu")
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    """
    שובר טקסט לוגי לשורות (לפני bidi!) כדי שלא יגלוש מחוץ לקובייה.
    את reverse_hebrew_visually מפעילים אחר כך על כל שורה בנפרד.
    """
    words = str(text).split()
    lines = []
    current_line = []
    for word in words:
        test_line = " ".join(current_line + [word])
        if draw.textlength(reverse_hebrew_visually(test_line), font=font) <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
    if current_line:
        lines.append(" ".join(current_line))
    return lines


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
            dtstart_prop = event.get("DTSTART")
            dtend_prop = event.get("DTEND")

            if not dtstart_prop:
                continue
            dtstart = dtstart_prop.dt

            if isinstance(dtstart, datetime.date) and not isinstance(dtstart, datetime.datetime):
                all_day = True
                dt_local_start = TIMEZONE.localize(datetime.datetime.combine(dtstart, datetime.time.min))
                if dtend_prop:
                    dt_local_end = TIMEZONE.localize(datetime.datetime.combine(dtend_prop.dt, datetime.time.min))
                else:
                    dt_local_end = dt_local_start + datetime.timedelta(days=1)
            else:
                all_day = False
                dt_local_start = dtstart.astimezone(TIMEZONE)
                if dtend_prop:
                    dt_local_end = dtend_prop.dt.astimezone(TIMEZONE)
                else:
                    dt_local_end = dt_local_start + datetime.timedelta(hours=1)

            # שיוך לימים הרלוונטיים בשבוע (אירוע יכול לגלוש למספר ימים)
            start_idx = (dt_local_start.date() - sunday_start.date()).days
            end_idx = (dt_local_end.date() - sunday_start.date()).days

            for day_idx in range(max(0, start_idx), min(7, end_idx + 1)):
                # אירוע שנגמר ב-00:00 בדיוק לא שייך ליום הבא.
                # זה נכון גם לאירועי כל-היום (DTEND בהם הוא בלעדי),
                # אחרת אירוע כל-היום היה מופיע ביום אחד יותר מדי.
                if day_idx == end_idx and dt_local_end.time() == datetime.time.min:
                    continue
                events_by_day[day_idx].append((all_day, dt_local_start, dt_local_end, summary))

    # מיון: כל-היום קודם, ואז לפי שעת התחלה
    for i in range(7):
        events_by_day[i].sort(key=lambda x: (not x[0], x[1]))
    return events_by_day


def render_calendar(events_by_day, now, sunday_start):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    img = Image.new("1", (WIDTH, HEIGHT), 1)  # 1 is white
    draw = ImageDraw.Draw(img)

    font_header = get_font(20, bold=True)
    font_sub = get_font(12, bold=False)
    font_day_name = get_font(14, bold=True)
    font_day_num = get_font(16, bold=True)
    font_event = get_font(11, bold=False)
    font_time_label = get_font(10, bold=False)

    # אזורי פריסה
    margin_left = 10
    margin_right = 35  # שוליים לשעות בצד ימין
    header_top = 5
    header_h = 25
    day_header_top = header_top + header_h
    day_header_h = 35
    allday_top = day_header_top + day_header_h
    allday_h = 22      # אזור לאירועי כל-היום בראש הטור

    grid_top = allday_top + allday_h
    grid_bottom = HEIGHT - 5
    grid_left = margin_left
    grid_right = WIDTH - margin_right
    col_w = (grid_right - grid_left) / 7.0
    px_per_hour = (grid_bottom - grid_top) / 24.0

    # סרגל עליון מימין ומשמאל
    month_title = f"{HEBREW_MONTHS[now.month]} {now.year}"
    draw.text((WIDTH - 15, header_top), reverse_hebrew_visually(month_title), font=font_header, fill=0, anchor="ra")
    updated_str = f"עודכן {now.strftime('%H:%M')}"
    draw.text((15, header_top + 5), reverse_hebrew_visually(updated_str), font=font_sub, fill=0, anchor="la")

    # קו תחתון תחת הכותרות
    draw.line([(grid_left, day_header_top + day_header_h), (grid_right, day_header_top + day_header_h)], fill=0, width=2)
    # קו מפריד בין כל-היום לגריד השעות
    draw.line([(grid_left, grid_top), (grid_right, grid_top)], fill=0, width=2)
    # מסגרת מלאה לטבלה
    draw.rectangle([grid_left, day_header_top, grid_right, grid_bottom], outline=0, width=2)

    # שנתות וקווים לשעות בגריד
    for h in range(2, 24, 2):
        y = grid_top + h * px_per_hour
        draw.text((grid_right + 4, y), f"{h:02d}:00", font=font_time_label, fill=0, anchor="lm")
        for x in range(int(grid_left), int(grid_right), 4):
            draw.point((x, int(y)), fill=0)

    # ציור עמודות הימים (מימין לשמאל)
    for i in range(7):
        col_right = grid_right - i * col_w
        col_left = col_right - col_w
        current_day_date = sunday_start.date() + datetime.timedelta(days=i)
        is_today = current_day_date == now.date()

        if i > 0:
            draw.line([(col_right, day_header_top), (col_right, grid_bottom)], fill=0, width=1)

        if is_today:
            draw.rectangle([col_left + 1, day_header_top + 1, col_right - 1, day_header_top + day_header_h - 1], fill=0)
            fg = 1
        else:
            fg = 0

        # כותרת היום (שם ומספר)
        cx = (col_left + col_right) / 2.0
        draw.text((cx, day_header_top + 4), reverse_hebrew_visually(HEBREW_DAYS[i]), font=font_day_name, fill=fg, anchor="ma")
        draw.text((cx, day_header_top + 20), str(current_day_date.day), font=font_day_num, fill=fg, anchor="ma")

        day_events = events_by_day[i]
        allday_y_cursor = allday_top + 1

        for all_day, dt_start, dt_end, summary in day_events:
            if all_day:
                # אירוע "כל היום": פס שחור, טקסט לבן בראש העמודה
                if allday_y_cursor + 10 < grid_top:
                    draw.rectangle([col_left + 1, allday_y_cursor, col_right - 1, allday_y_cursor + 11], fill=0)
                    draw.text((col_right - 3, allday_y_cursor), reverse_hebrew_visually(summary[:12]), font=font_event, fill=1, anchor="ra")
                    allday_y_cursor += 12
            else:
                # התחלה וסיום יחסיים ליום הנוכחי (00:00 עד 24:00)
                if dt_start.date() < current_day_date:
                    h_start = 0.0
                else:
                    h_start = dt_start.hour + dt_start.minute / 60.0

                if dt_end.date() > current_day_date:
                    h_end = 24.0
                else:
                    h_end = dt_end.hour + dt_end.minute / 60.0

                y0 = grid_top + h_start * px_per_hour
                y1 = grid_top + h_end * px_per_hour

                # גובה מינימלי לבלוק כך שלפחות שורת טקסט אחת תיכנס
                y1_visual = max(y1, y0 + 14)

                draw.rectangle([col_left + 1, y0, col_right - 1, y1_visual], outline=0, fill=1)

                lines = wrap_text(draw, summary, font_event, col_w - 6)
                text_y = y0 + 1
                for line in lines:
                    if text_y + 11 > y1_visual:
                        break
                    draw.text((col_right - 3, text_y), reverse_hebrew_visually(line), font=font_event, fill=0, anchor="ra")
                    text_y += 12

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
