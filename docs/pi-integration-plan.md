# תוכנית שילוב קוד ה-Pi הקיים (claude-handoff, 14.10.2026)

מקור: `claude-handoff/pi-reference/current/` (קוד המקור של ה-Pi) ו-`claude-handoff/PI_CODE_REVIEW.md`.
זה snapshot של קוד בלבד - לא ראיה למה שרץ בפועל ב-Pi.

## עקרונות

1. **ממשק מובנה, לא גירוד טקסט.** האפליקציה לא מריצה את `x500-preflight` / `x500-status` ולא מפרסרת את הפלט שלהם.
   ה-collector (קריאה בלבד) מחזיר תצפיות גולמיות כ-JSON בגרסה `x500fs.collector/1`, ושכבת ההערכה של האפליקציה (`x500fs.core.checks`)
   היא היחידה שמחליטה PASS/WARN/FAIL/UNKNOWN.
2. **אותה שכבת הערכה גם ב-Pi.** גרסה מתוקנת של `x500-preflight [bench|flight]` משתמשת באותו collector ובאותה שכבת הערכה.
   היא שומרת על קודי היציאה הישנים 0/1/2. INCOMPLETE (בדיקה קריטית לא ידועה) מחזיר 2 - לעולם לא "עבר".
3. **פריסה מפורשת בלבד.** הסקריפטים המתוקנים נמצאים ב-`pi/x500/` ([פירוט](../pi/x500/README.md)).
   `pi/deploy/deploy_x500.sh` מציג diff, שומר גיבוי ולא מחליף קבצים בלי `--apply`.
   חיבור או בדיקה מהאפליקציה לא משנים כלום ב-Pi.

## מיפוי הקוד הישן ליכולות החדשות

| קוד ישן | מה נלקח | מה תוקן (מספר בסקירה) |
|---|---|---|
| `x500-health` | ספי טמפרטורה 75/82, ‏RAM ‏90%, דיסק 85/95%, פירוק דגלי throttle | ‏`vcgencmd` שנכשל = "לא זמין", לא 0 / ‏`0x0` (1); דגלים נוכחיים מול היסטוריים בנפרד |
| `x500-preflight` | רשימת הבדיקות, מצבי bench/flight, קודי יציאה | ACK של `MAV_CMD_RUN_PREARM_CHECKS` נבדק לפי `result` ומקור; שקט ≠ עבר (2); סינון לפי הרכב (3); סוללה/GPS/EKF (4,5) |
| `x500-status` | שדות הטלמטריה (roll/pitch/yaw, מצב, GPS, סוללה, EKF) | מוצגים מחיבור MAVLink קבוע אחד, עם גיל לכל שדה |
| `x500-net`, `x500-net-monitor` | ממשקים `wlan0`/`eth1`/`tailscale0`, שער ה-ZTE, ‏ping לאינטרנט, Tailscale, תחנת קרקע | הכול ניתן להגדרה (10); ICMP ≠ איכות MAVLink (6) |
| `x500-blackbox`, `x500-blackbox-status` | מיקום ה-CSV והעמודות | בדיקת שירות **וגם** דגימה תקינה ועדכנית; קצב בפועל נמדד; אין טענת שמירה ל-14 יום (7) |
| `60-x500-watchdog.conf` | ‏`RuntimeWatchdogSec=15s` | "מוגדר" מול "פעיל" - הוכחת ריצה נפרדת (8) |
| `x500-fc-detect` | ‏by-id ‏`usb-Holybro_Pixhawk6C_*-if00` | זהות FC ניתנת להגדרה ומאומתת (10) |
| `x500-video-run`, `x500-video@.service` | צינור SRT כמו שהוא, פרופילים high/normal/degraded | הצופה ב-Windows מתחבר כ-caller; "progress מתעדכן" ≠ "תמונה חיה" (6) |
| `x500-diag` | רשימת המקורות | ייצוא אבחון מפורש מהאפליקציה, ל-stdout בלבד, עם הסרת פרטים פרטיים (9) |

## קצב איסוף (תיקון 9)

- **מהיר** (ברירת מחדל: כל 10 שניות): טמפרטורה, CPU, זיכרון, אחסון, מתח, שירותים, watchdog, קופסה שחורה, מצב וידאו.
- **איטי** (כל 60 שניות, ובכל "הרץ בדיקה"): pings, ‏Tailscale, מצלמה (`rpicam-hello --list-cameras`), ‏USB.
- **MAVLink**: חיבור אחד קבוע דרך mavlink-router, עם בקשות חד-פעמיות מוגבלות בקצב.
- לכל סעיף יש זמן קבלה משלו, והגיל מחושב לפיו.

## וידאו

- **SRT קודם.** PyAV לא נבנה עם libsrt (נבדק: `Protocol not found`). לכן הנגן הוא FFmpeg מתוך החבילה `imageio-ffmpeg`
  (גרסה נעולה ב-hash; ב-Windows זה build של gyan.dev 7.1 עם `--enable-libsrt`). FFmpeg רץ כתהליך נפרד שמפענח לפריימים גולמיים.
- **הפעלת הזרם ב-Pi:** רק `sudo -n /usr/bin/systemctl start|stop x500-video@<profile>.service`, ורק לשלושת הפרופילים.
- **אומת ב-CI של Windows:** ה-FFmpeg המצורף (‏gyan.dev 7.1) כולל `srt`, והאפליקציה המותקנת פענחה זרם SRT מקומי מקצה לקצה.
  אם אין הרשאה בלי סיסמה, האפליקציה מסבירה. אופציה מפורשת: קובץ sudoers מצומצם (`pi/deploy/install_video_sudoers.sh`).
- **MediaMTX/RTSP** נשאר כחלופה מתועדת, לא כברירת מחדל.
