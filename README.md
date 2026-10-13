# X500 Flight Station

אפליקציית Windows בעברית לבדיקות לפני טיסה, ניטור ווידאו חי עבור רחפן **Holybro X500 V2 PNP**
(Pixhawk 6C עם ArduPilot Copter, Raspberry Pi 5, מצלמת IMX219).

> **האפליקציה רק בודקת, מנטרת ומציגה.** היא לא מחמשת, לא ממריאה, לא משנה מצב טיסה,
> לא מפעילה מנועים, לא משנה פרמטרים ולא מכבה בדיקות Pre-arm. **Mission Planner נשאר האחראי על השליטה בטיסה.**
> תוצאה "בדיקות אוטומטיות עברו" **אינה** הבטחה לטיסה בטוחה.

![מסך ראשי (סימולציה)](docs/screenshots/main-bench-healthy.png)

---

## מה מוכן ומה נבדק

| תחום | מומש | נבדק אוטומטית (בלי חומרה) | נבדק על הרחפן האמיתי |
|---|---|---|---|
| ממשק בעברית (RTL), סימולציה, רשימה ידנית, דוחות JSON/HTML | ✔ | ✔ בדיקות GUI ב-offscreen + צילומי מסך | ✘ |
| מנוע בדיקות ופרופילים (ספסל/טיסה) | ✔ | ✔ ‏~120 בדיקות pytest | ✘ |
| SSH עם אימות מפתח שרת (host key) | ✔ | ✔ מול שרת SSH מדומה | ✘ |
| איסוף נתונים מה-Pi (collector, קריאה בלבד) | ✔ | ✔ על Linux ועל מערכת קבצים מדומה | ✘ (Pi 5 אמיתי לא נבדק) |
| MAVLink לקריאה בלבד דרך mavlink-router / מנהרת SSH | ✔ | ✔ מול autopilot מדומה עם הודעות MAVLink2 אמיתיות | ✘ (Pixhawk 6C לא נבדק) |
| וידאו RTSP (PyAV/FFmpeg) עם זיהוי תמונה ישנה וניסיונות חיבור מוגבלים | ✔ | ✔ פענוח H.264 מקובץ מקומי | ✘ (MediaMTX על ה-Pi לא נבדק) |
| התקנת MediaMTX ב-Pi (סקריפט נפרד) | ✔ | ✘ רק `--dry-run` | ✘ |
| חבילת Windows ‏(PyInstaller) + Setup.exe ‏(Inno Setup) | ✔ | ✔ ב-CI: התקנה שקטה + בדיקה עצמית בלי Python ([פרטים](#מצב-בנייה-ל-windows)) | ✘ מחשב Windows עם מסך |

רשימת הבדיקות המלאה, עם הספים וההצדקות: [docs/checks.md](docs/checks.md).
מה צריך לבדוק על החומרה: [docs/hardware-verification-checklist.md](docs/hardware-verification-checklist.md).

## התקנה (משתמש)

1. ב-GitHub: **Actions → Windows build (app + Setup.exe)** → הריצה האחרונה שהצליחה → הורד את
   `X500FlightStation-Setup-<גרסה>`. (לא פורסם Release - זה בכוונה.)
2. הרץ את `X500FlightStation-Setup-<גרסה>.exe`. ההתקנה היא למשתמש הנוכחי (לא צריך הרשאות מנהל)
   ואין צורך בהתקנת Python.
3. פתח את האפליקציה. כדי לראות איך היא נראית בלי רחפן: **הגדרות → כללי וסימולציה → מצב סימולציה**.
   במצב הזה יש פס כתום קבוע, הכותרת מתחילה ב-"[סימולציה]" וכל מקור נתונים מסומן `SIM`.

ההגדרות, הפרופילים, מפתחות השרת המוכרים והלוגים נשמרים ב-`%APPDATA%\X500FlightStation`.
הדוחות נשמרים ב-`Documents\X500FlightStation\reports`. הסרת ההתקנה לא מוחקת אותם.
סיסמאות ו-passphrase נשמרים רק במנהל האישורים של Windows.

## הפעלה ראשונה מול הרחפן

מדריך צעד-אחר-צעד: **[docs/first-bench-session.md](docs/first-bench-session.md)**.
בקצרה: מריצים את `pi/deploy/probe.sh` על ה-Pi (קריאה בלבד), ממלאים בהגדרות כתובת, משתמש ומפתח SSH,
מאשרים את טביעת האצבע של מפתח השרת (משווים מול ה-Pi), ומתחברים.

## שימוש יומיומי (2-3 לחיצות)

1. **התחבר** (Ctrl+K)
2. **הרץ בדיקה** (F5) - מתחיל סשן חדש: מאפס את הרשימה הידנית, מרענן נתונים מה-Pi ופרמטרים מבקר הטיסה
3. (לא חובה) **הפעל וידאו** - רק כשהרחפן לא חמוש

אחר כך מסמנים את הרשימה הידנית אחרי בדיקה פיזית, ו**ייצא דוח** (Ctrl+S) שומר JSON ו-HTML.
ה"בדיקות האוטומטיות" וה"רשימה הידנית" מוצגות בנפרד. כל בדיקה מציגה סטטוס
(PASS / WARN / FAIL / UNKNOWN / NOT_APPLICABLE), הסבר, ערך נצפה, תנאי צפוי, מקור הנתון וגיל התצפית.

## עקרונות בטיחות (מה האפליקציה לעולם לא עושה)

- **לא שולחת HEARTBEAT**, ‏RC_CHANNELS_OVERRIDE או MANUAL_CONTROL. אלה ההודעות היחידות שמאפסות את טיימר
  ה-GCS failsafe ב-ArduPilot, ולכן היא לא יכולה "להחזיק" את הקישור במקום Mission Planner או להסתיר ניתוק שלו.
- משתמשת ב-sysid/compid משלה (‏247/25, ניתן לשינוי), שונה מתחנת הקרקע.
- שולחת רק `PARAM_REQUEST_READ` ו-`MAV_CMD_REQUEST_MESSAGE` (בקשה חד-פעמית שלא משנה קצבי שידור ולא כותבת פרמטרים).
  כל הודעה אחרת נחסמת בקוד ונבדקת בבדיקות. אפשר לכבות גם את זה (מצב האזנה בלבד).
- לא פותחת את הפורט הטורי של ה-Pixhawk. מתחברת ל-endpoint קיים של mavlink-router (ברירת מחדל: מנהרת SSH ל-`127.0.0.1:5760` על ה-Pi).
- חיבור או הרצת בדיקה לא מתקינים כלום ב-Pi ולא משנים בו דבר: ה-collector נשלח ב-SSH ל-`python3 -` ורץ בקריאה בלבד.
  התקנה (MediaMTX) היא שלב נפרד שאתה מריץ ידנית.
- מפתח שרת SSH לא מוכר לעולם לא מתקבל בשקט. מפתח שהשתנה נחסם.
- נתון חסר, ישן, לא נתמך או פגום לעולם לא מוצג כ-PASS. בדיקה קריטית שלא הוגדרה מונעת תוצאה "עברו".

פירוט: [docs/mavlink-safety.md](docs/mavlink-safety.md).

## פיתוח

דרוש Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux: source .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
set PYTHONPATH=src                # Linux: export PYTHONPATH=src
python -m x500fs                  # הפעלת האפליקציה
python -m pytest -q               # כל הבדיקות
```

- autopilot מדומה (MAVLink2 אמיתי על TCP): `python -m x500fs.sim.mock_mavlink --port 5760`,
  ואז בהגדרות MAVLink: "TCP ישיר" אל `127.0.0.1:5760`.
- בדיקה עצמית (כמו ב-CI): `python -m x500fs --self-test result.json --offscreen`
- צילומי מסך לתיעוד: `python tools/screenshots.py docs/screenshots`
- עדכון קטלוג הבדיקות: `python tools/gen_checks_doc.py`
- עדכון תלויות: ערוך את `requirements.in` ואז
  `uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes -o requirements.txt`

ארכיטקטורה: [docs/architecture.md](docs/architecture.md).

## בנייה ל-Windows

```bat
python -m PyInstaller --noconfirm --clean packaging\x500fs.spec
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\installer.iss
```

התוצאה: `dist\X500FlightStation\` (תיקייה ניידת) ו-`packaging\Output\X500FlightStation-Setup-0.1.0.exe`.

### מצב בנייה ל-Windows

ה-workflow ‏`.github/workflows/windows-build.yml` מריץ על `windows-latest`: בדיקות pytest, בניית PyInstaller,
בדיקה עצמית של החבילה, בניית Setup.exe, **התקנה שקטה והרצת הבדיקה העצמית של האפליקציה המותקנת כשאף Python לא נמצא ב-PATH**,
והסרה שקטה שמוודאת שנתוני המשתמש נשארים. התוצרים: Setup.exe, ‏zip נייד ותוצאות הבדיקה העצמית.
**נבדק בפועל ב-CI** (ריצה ראשונה, Windows Server 2025, ‏`windows-latest`): כל הבדיקות עברו, Setup.exe נבנה (‏~55MB),
הותקן בשקט, והאפליקציה **המותקנת** עברה את כל שלבי הבדיקה העצמית (ממשק בסימולציה + דוח, MAVLink מול autopilot מדומה,
פענוח H.264, קבצי ה-Pi, מנהל האישורים `WinVaultKeyring`) **כש-Python הוסר מה-PATH**. ההסרה מחקה את קבצי האפליקציה
והשאירה את `%APPDATA%\X500FlightStation`.
**לא נבדק:** הפעלה על מחשב Windows אמיתי עם מסך (ב-CI הממשק רץ במצב offscreen), ומראה הגופן העברי ב-Windows.

## צד ה-Pi

ראו [pi/README.md](pi/README.md): ‏`probe.sh` (קריאה בלבד), התקנת MediaMTX למצלמה (שלב נפרד ומפורש),
והתקנה אופציונלית של ה-collector.

## מסמכים

- [docs/first-bench-session.md](docs/first-bench-session.md) - מפגש ספסל ראשון
- [docs/hardware-verification-checklist.md](docs/hardware-verification-checklist.md) - מה עוד לא אומת על החומרה
- [docs/checks.md](docs/checks.md) - כל הבדיקות, הספים וההצדקות
- [docs/video.md](docs/video.md) - נתיב הווידאו, מדידת ביצועים על Pi 5
- [docs/mavlink-safety.md](docs/mavlink-safety.md) - למה האפליקציה לא יכולה להשפיע על הטיסה
- [docs/architecture.md](docs/architecture.md) - מבנה הקוד
- [docs/THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md) - רישיונות של רכיבים מצורפים
