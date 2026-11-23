import os
import datetime
import pytz
import requests
import recurring_ical_events
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont
from bidi.algorithm import get_display

# הגדרות תצוגה למסך 7.5 אינץ' (TRMNL OG)
WIDTH, HEIGHT = 800, 480
TIMEZONE = pytz.timezone("Asia/Jerusalem")
OUTPUT_PATH = "docs/calendar.bmp"

def fix_hebrew(text):
    if not text:
        return ""
    return get_display(text)

def fetch_events(urls):
    now = datetime.datetime.now(TIMEZONE)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_range = start_of_day + datetime.timedelta(days=3)
    events = []

    for url in urls:
        url = url.strip().replace("webcal://", "https://")
        if not url:
            continue
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        cal = Calendar.from_ical(resp.content)
        occurrences = recurring_ical_events.of(cal).between(start_of_day, end_range)

        for event in occurrences:
            summary = str(event.get("SUMMARY", "ללא כותרת"))
            dtstart = event.get("DTSTART").dt
            if isinstance(dtstart, datetime.date) and not isinstance(dtstart, datetime.datetime):
                dtstart = TIMEZONE.localize(datetime.datetime.combine(dtstart, datetime.time.min))
                all_day = True
            else:
                dtstart = dtstart.astimezone(TIMEZONE)
                all_day = False
            events.append((dtstart, all_day, summary))

    events.sort(key=lambda x: x[0])
    return events

def render_image(events):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    img = Image.new("1", (WIDTH, HEIGHT), 1)  # 1 = לבן, 0 = שחור
    draw = ImageDraw.Draw(img)

    # טעינת פונט מערכת תומך עברית (מותקן ב-Ubuntu של GitHub Actions)
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    font_item = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)

    now = datetime.datetime.now(TIMEZONE)
    header = fix_hebrew(f"לו״ז מעודכן - {now.strftime('%d/%m/%Y %H:%M')}")
    draw.text((WIDTH - 30, 20), header, font=font_title, fill=0, anchor="ra")
    draw.line([(30, 65), (WIDTH - 30, 65)], fill=0, width=2)

    y = 85
    if not events:
        draw.text((WIDTH - 30, y), fix_hebrew("אין אירועים מתוכננים לימים הקרובים."), font=font_item, fill=0, anchor="ra")
    else:
        for dt, all_day, summary in events[:9]:
            time_str = "כל היום" if all_day else dt.strftime("%H:%M")
            day_str = dt.strftime("%d/%m")
            line_text = fix_hebrew(f"{day_str} | {time_str} - {summary}")
            draw.text((WIDTH - 30, y), line_text, font=font_item, fill=0, anchor="ra")
            y += 40

    img.save(OUTPUT_PATH, format="BMP")

if __name__ == "__main__":
    raw_urls = os.environ.get("ICAL_URLS", "")
    urls = [u for u in raw_urls.replace("\n", ",").split(",") if u.strip()]
    events = fetch_events(urls)
    render_image(events)
