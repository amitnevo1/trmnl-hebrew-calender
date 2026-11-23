import os
import sys
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

# אם MASK_TITLES=1 בסביבה, כל כותרות הפגישות מוחלפות ב"תפוס" (ברירת מחדל: כבוי)
MASK_TITLES = os.environ.get("MASK_TITLES") == "1"

BLACK, WHITE = 0, 255
MIN_BLOCK_H = 15   # גובה מינימלי לבלוק פגישה בפיקסלים
LINE_H = 13        # גובה שורת טקסט בתוך בלוק
WEEKEND_DAYS = (5, 6)  # שישי ושבת (אינדקס מראשון)

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
    # אפשר לדרוס נתיב עם FONT_REGULAR / FONT_BOLD בסביבה
    env_path = os.environ.get("FONT_BOLD" if bold else "FONT_REGULAR")
    candidates = [
        env_path,
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
    # נכשלים בקול רם במקום לצייר תמונה עם ריבועים במקום עברית
    raise RuntimeError(
        "No Hebrew-capable font found. Install fonts-freefont-ttf or fonts-dejavu, "
        "or set FONT_REGULAR / FONT_BOLD to a font file path."
    )


def text_width(draw, text, font):
    return draw.textlength(reverse_hebrew_visually(text), font=font)


def fit_text(draw, text, font, max_width):
    """מקצר טקסט לוגי (מהסוף) עד שייכנס ברוחב, ומוסיף …"""
    if text_width(draw, text, font) <= max_width:
        return text
    while text and text_width(draw, text + "…", font) > max_width:
        text = text[:-1]
    return text + "…" if text else ""


def wrap_text(draw, text, font, max_width):
    """
    שובר טקסט לוגי לשורות (לפני bidi!) כדי שלא יגלוש מחוץ לקובייה.
    מילה ארוכה מדי מקוצרת עם …
    את reverse_hebrew_visually מפעילים אחר כך על כל שורה בנפרד.
    """
    lines = []
    current_line = []
    for word in str(text).split():
        test_line = " ".join(current_line + [word])
        if not current_line or text_width(draw, test_line, font) <= max_width:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
    return [fit_text(draw, line, font, max_width) for line in lines]


def to_local(value):
    """
    ממיר DTSTART/DTEND לשעון ירושלים (aware).
    שעה "צפה" (בלי TZ) מפורשת כשעון ירושלים, ולא כשעון המכונה (UTC ב-GitHub Actions).
    """
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return TIMEZONE.localize(datetime.datetime.combine(value, datetime.time.min))
    if value.tzinfo is None:
        return TIMEZONE.localize(value)
    return value.astimezone(TIMEZONE)


def layout_columns(spans):
    """
    מקבל רשימת (y0, y1) ומחזיר לכל אחד (עמודה, מספר_עמודות), כך שאירועים
    חופפים יוצבו זה לצד זה ולא זה על זה. הסדר בפלט זהה לסדר בקלט.
    """
    result = [(0, 1)] * len(spans)
    order = sorted(range(len(spans)), key=lambda i: spans[i])

    cluster = []        # (אינדקס, עמודה) בקבוצה החופפת הנוכחית
    col_ends = []       # y סיום אחרון בכל עמודה בקבוצה
    cluster_end = None

    def flush():
        for idx, col in cluster:
            result[idx] = (col, len(col_ends))

    for i in order:
        y0, y1 = spans[i]
        if cluster_end is None or y0 >= cluster_end:
            flush()
            cluster, col_ends = [], []
            cluster_end = y1
        else:
            cluster_end = max(cluster_end, y1)

        col = next((c for c, end in enumerate(col_ends) if end <= y0), None)
        if col is None:
            col = len(col_ends)
            col_ends.append(y1)
        else:
            col_ends[col] = y1
        cluster.append((i, col))

    flush()
    return result


def fetch_week_events(urls, sunday_start):
    """
    מחזיר (events_by_day, failures). כישלון של יומן אחד לא מפיל את השאר.
    בכוונה לא מדפיסים את ה-URL או את הודעת השגיאה (קישורי iCal הם סודיים).
    """
    saturday_end = sunday_start + datetime.timedelta(days=7)
    events_by_day = {i: [] for i in range(7)}
    failures = 0

    for number, url in enumerate(urls, start=1):
        clean_url = url.strip().replace("webcal://", "https://")
        if not clean_url:
            continue
        try:
            resp = requests.get(clean_url, timeout=15)
            resp.raise_for_status()
            cal = Calendar.from_ical(resp.content)
            occurrences = recurring_ical_events.of(cal).between(sunday_start, saturday_end)

            for event in occurrences:
                if str(event.get("STATUS", "")).upper() == "CANCELLED":
                    continue

                summary = str(event.get("SUMMARY", "")).strip() or "אירוע"
                if MASK_TITLES:
                    summary = "תפוס"
                dtstart_prop = event.get("DTSTART")
                dtend_prop = event.get("DTEND")
                duration_prop = event.get("DURATION")

                if not dtstart_prop:
                    continue
                dtstart = dtstart_prop.dt
                all_day = isinstance(dtstart, datetime.date) and not isinstance(dtstart, datetime.datetime)
                dt_local_start = to_local(dtstart)

                if dtend_prop:
                    dt_local_end = to_local(dtend_prop.dt)
                elif duration_prop:
                    dt_local_end = dt_local_start + duration_prop.dt
                elif all_day:
                    dt_local_end = dt_local_start + datetime.timedelta(days=1)
                else:
                    dt_local_end = dt_local_start + datetime.timedelta(hours=1)

                if dt_local_end <= dt_local_start:
                    dt_local_end = dt_local_start + (
                        datetime.timedelta(days=1) if all_day else datetime.timedelta(minutes=1)
                    )

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
        except Exception as exc:
            failures += 1
            print(f"WARNING: calendar #{number} failed ({type(exc).__name__})", file=sys.stderr)

    # מיון: כל-היום קודם, ואז לפי שעת התחלה
    for i in range(7):
        events_by_day[i].sort(key=lambda x: (not x[0], x[1]))
    return events_by_day, failures


def render_calendar(events_by_day, now, sunday_start, failed_calendars=0):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    # מציירים ב-L (עם החלקה) וממירים ל-1 בסוף בלי dithering
    img = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    font_header = get_font(20, bold=True)
    font_sub = get_font(12, bold=False)
    font_day_name = get_font(14, bold=True)
    font_day_num = get_font(16, bold=True)
    font_event = get_font(12, bold=False)
    font_time_label = get_font(11, bold=False)

    # אזורי פריסה (גריד של 24 שעות)
    margin_left = 10
    margin_right = 35  # שוליים לשעות בצד ימין
    header_top = 5
    header_h = 25
    day_header_top = header_top + header_h
    day_header_h = 35
    allday_top = day_header_top + day_header_h
    allday_h = 22      # אזור לאירוע כל-היום בראש הטור

    grid_top = allday_top + allday_h
    grid_bottom = HEIGHT - 5
    grid_left = margin_left
    grid_right = WIDTH - margin_right
    col_w = (grid_right - grid_left) / 7.0
    px_per_hour = (grid_bottom - grid_top) / 24.0

    def draw_text(xy, text, font, fill, anchor):
        draw.text(xy, reverse_hebrew_visually(text), font=font, fill=fill, anchor=anchor)

    # סרגל עליון מימין ומשמאל
    month_title = f"{HEBREW_MONTHS[now.month]} {now.year}"
    draw_text((WIDTH - 15, header_top), month_title, font_header, BLACK, "ra")
    draw_text((15, header_top + 5), f"עודכן {now.strftime('%H:%M')}", font_sub, BLACK, "la")
    if failed_calendars:
        draw_text((110, header_top + 5), f"שגיאה ב-{failed_calendars} יומנים", font_sub, BLACK, "la")

    # קו תחתון תחת הכותרות
    draw.line([(grid_left, day_header_top + day_header_h), (grid_right, day_header_top + day_header_h)], fill=BLACK, width=2)
    # קו מפריד בין כל-היום לגריד השעות
    draw.line([(grid_left, grid_top), (grid_right, grid_top)], fill=BLACK, width=2)
    # מסגרת מלאה לטבלה
    draw.rectangle([grid_left, day_header_top, grid_right, grid_bottom], outline=BLACK, width=2)

    # רקע מנוקד עדין לשישי ושבת
    for i in WEEKEND_DAYS:
        wk_right = grid_right - i * col_w
        wk_left = wk_right - col_w
        for row, y in enumerate(range(int(grid_top) + 3, int(grid_bottom) - 1, 6)):
            x_start = int(wk_left) + 3 + (3 if row % 2 else 0)
            for x in range(x_start, int(wk_right) - 1, 6):
                draw.point((x, y), fill=BLACK)

    # שנתות וקווים לשעות בגריד
    for h in range(2, 24, 2):
        y = grid_top + h * px_per_hour
        draw.text((grid_right + 4, y), f"{h:02d}:00", font=font_time_label, fill=BLACK, anchor="lm")
        for x in range(int(grid_left), int(grid_right), 4):
            draw.point((x, int(y)), fill=BLACK)

    # ציור עמודות הימים (מימין לשמאל)
    for i in range(7):
        col_right = grid_right - i * col_w
        col_left = col_right - col_w
        current_day_date = sunday_start.date() + datetime.timedelta(days=i)
        is_today = current_day_date == now.date()

        if i > 0:
            draw.line([(col_right, day_header_top), (col_right, grid_bottom)], fill=BLACK, width=1)

        if is_today:
            draw.rectangle([col_left + 1, day_header_top + 1, col_right - 1, day_header_top + day_header_h - 1], fill=BLACK)
            fg = WHITE
        else:
            fg = BLACK

        # כותרת היום (שם ומספר)
        cx = (col_left + col_right) / 2.0
        draw_text((cx, day_header_top + 4), HEBREW_DAYS[i], font_day_name, fg, "ma")
        draw.text((cx, day_header_top + 20), str(current_day_date.day), font=font_day_num, fill=fg, anchor="ma")

        day_events = events_by_day[i]
        all_day_events = [e for e in day_events if e[0]]
        timed_events = [e for e in day_events if not e[0]]

        # אירוע "כל היום": פס שחור בראש העמודה, עם "+N" אם יש עוד
        if all_day_events:
            first_summary = all_day_events[0][3]
            extra = len(all_day_events) - 1
            bar_top = allday_top + 2
            draw.rectangle([col_left + 1, bar_top, col_right - 1, bar_top + 15], fill=BLACK)
            available = col_w - 8
            if extra:
                badge = f"+{extra}"
                draw.text((col_left + 4, bar_top + 1), badge, font=font_event, fill=WHITE, anchor="la")
                available -= draw.textlength(badge, font=font_event) + 4
            label = fit_text(draw, first_summary, font_event, available)
            draw_text((col_right - 4, bar_top + 1), label, font_event, WHITE, "ra")

        # אירועים עם שעה: קודם מחשבים מיקום אנכי, ואז פריסה לעמודות לפי חפיפה
        spans = []
        for _, dt_start, dt_end, _ in timed_events:
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

            # גובה מינימלי כדי שלפחות שורה אחת תיכנס, בלי לצאת מהגריד
            y1_visual = min(max(y1, y0 + MIN_BLOCK_H), grid_bottom - 1)
            y0 = min(y0, y1_visual - MIN_BLOCK_H)
            spans.append((y0, y1_visual))

        for event, (y0, y1_visual), (col, ncols) in zip(timed_events, spans, layout_columns(spans)):
            summary = event[3]
            sub_w = (col_w - 2) / ncols
            block_right = col_right - 1 - col * sub_w
            block_left = block_right - sub_w

            # בלוק הפגישה (רקע לבן, מסגרת שחורה למניעת התנגשות עם קווי הרקע)
            draw.rectangle([block_left, y0, block_right, y1_visual], outline=BLACK, fill=WHITE)

            text_y = y0 + 1
            for line in wrap_text(draw, summary, font_event, sub_w - 6):
                if text_y + LINE_H - 1 > y1_visual:
                    break  # אין מקום לשורות נוספות בתוך הבלוק הזה
                draw_text((block_right - 3, text_y), line, font_event, BLACK, "ra")
                text_y += LINE_H

    img = img.convert("1", dither=Image.Dither.NONE)
    img.save(OUTPUT_PATH, format="BMP")


if __name__ == "__main__":
    now = datetime.datetime.now(TIMEZONE)
    days_since_sunday = (now.weekday() + 1) % 7
    sunday_date = now.date() - datetime.timedelta(days=days_since_sunday)
    sunday_start = TIMEZONE.localize(datetime.datetime.combine(sunday_date, datetime.time.min))

    raw_urls = os.environ.get("ICAL_URLS", "")
    urls = [u for u in raw_urls.replace("\n", ",").split(",") if u.strip()]
    if not urls:
        print("ERROR: ICAL_URLS is empty", file=sys.stderr)
        sys.exit(1)

    events_by_day, failures = fetch_week_events(urls, sunday_start)
    if failures == len(urls):
        # כל היומנים נכשלו: לא דורסים את התמונה האחרונה התקינה, והריצה נכשלת בגלוי
        print("ERROR: all calendars failed, keeping the previous image", file=sys.stderr)
        sys.exit(1)

    render_calendar(events_by_day, now, sunday_start, failed_calendars=failures)
