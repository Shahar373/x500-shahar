# ארכיטקטורה

```
src/x500fs/
  core/                 ← ללא Qt, ללא רשת (קל לבדיקה)
    status.py           PASS/WARN/FAIL/UNKNOWN/NOT_APPLICABLE, CheckResult, aggregate() → תוצאה כוללת
    observations.py     מאגר תצפיות thread-safe; מקור "live" או "simulation" בלבד (OriginMismatch)
    profile.py          פרופילי JSON (bench/flight), ולידציה, ספים "fc:PARAM", כללי expected_params
    checks/             46 בדיקות: pi / link / fc / battery / video, + מנוע ההערכה (base.py)
    mav/                קבועי MAVLink ופענוח טהור (sentinels → None)
    manual.py           רשימה ידנית (מתאפסת בכל סשן)
    report.py, redact.py  דוח JSON/HTML, הסרת סודות
    session.py          יומן סשן: ניתוקים, אירועים, RTT
    params.py           אילו פרמטרים נקראים (כולל שמות של 4.7)
  transport/            ← רשת, בלי Qt
    ssh.py              paramiko: אימות מפתח שרת, exec עם stdin וזמן קצוב, ping ‏(keepalive@openssh.com), מנהרה מקומית
    pi_link.py          SshManager (חיבור/התחברות מחדש/RTT), PiPoller (מריץ את ה-collector מעל SSH)
    streams.py          TCP / UDP / ערוץ SSH ‏(direct-tcpip)
    mavlink_client.py   לקוח MAVLink לקריאה בלבד, עם רשימה סגורה של הודעות מותרות
    mav_ingest.py       משותף ללקוח ולסימולציה: סינון sysid/compid, PARAM_VALUE, STATUSTEXT, COMMAND_ACK
  pi_side/              ← רץ על ה-Pi (stdlib בלבד)
    x500fs_collector.py תצפיות גולמיות כ-JSON בגרסה ‏x500fs.collector/1
    x500fs_camera.py    מצב ושינוי זמני של נתיב ה-MediaMTX (רק מ-localhost)
  sim/                  סימולציה (SimDrone) ו-autopilot מדומה על TCP (mock_mavlink)
  video/worker.py       PyAV worker, ‏FPS, ניסיונות חיבור מחדש מוגבלים; SimVideoWorker
  controller.py         תזמור (בלי Qt): חיבור, בדיקה, וידאו, דוח
  ui/                   PySide6, ‏RTL: main_window, checks_view, panels, video_view, dialogs
  settings.py           הגדרות JSON ב-%APPDATA%
  credentials.py        Windows Credential Manager דרך keyring (בלי גיבוי לטקסט גלוי)
  selftest.py           בדיקה עצמית לאפליקציה הארוזה
```

## זרימת נתונים

1. **Workers** (threads נפרדים: SSH, ‏Pi poller, ‏MAVLink, וידאו) כותבים תצפיות גולמיות ל-`ObservationStore`,
   עם זמן קבלה מונוטוני במחשב.
2. **מנוע הבדיקות** רץ פעם בשנייה על `Snapshot` (צילום של המאגר עם `now` אחד), מול הפרופיל, ומחשב לכל תצפית את הגיל שלה.
3. כל בדיקה מחזירה סטטוס, הסבר, ערך נצפה, תנאי צפוי, מקור, גיל, מגבלות וראיות חסרות.
   חריגה בתוך בדיקה (למשל נתון מסוג שגוי) הופכת ל-UNKNOWN.
4. **התוצאה הכוללת**:
   - FAIL - בדיקה קריטית נכשלה.
   - INCOMPLETE - בדיקה קריטית UNKNOWN, או בדיקה קריטית שלא הוגדרה.
   - PASS_WITH_WARNINGS - אין בעיה קריטית, אבל יש אזהרות.
   - PASS - הכול עבר.
5. **ממשק המשתמש** מקבל אירועים מה-workers דרך `UiBridge` (signals בתור, ‏queued) ולא חוסם אף פעם על רשת.

## הפרדת סימולציה

- `ObservationStore(simulated=True/False)` דוחה תצפית מהמקור השני.
- `SimDrone` דורש מאגר סימולציה.
- מקור כל בדיקה מתחיל ב-`SIM ·`, הדוח מסומן `"simulation": true`, שם הקובץ מסתיים ב-`-SIMULATION`, ובממשק מוצג פס כתום קבוע.
- הסימולציה עוברת דרך אותו `MavIngest` כמו החיבור האמיתי.

## Threads וזמנים קצובים

| Worker | זמנים קצובים | ביטול |
|---|---|---|
| SSH | connect/banner/auth: 10 שניות, ‏ping: 3 שניות, ‏keepalive: 5 שניות | `stop()` סוגר את ה-transport |
| Pi collector | ‏exec: 45 שניות; כל פקודה ב-Pi: 3-12 שניות | `Event` |
| MAVLink | קריאה: 0.2 שניות; התחברות מחדש בהמתנה גדלה עד 10 שניות | סגירת ה-stream |
| וידאו | פתיחה: 8 שניות, קריאה: 3 שניות; עד 5 ניסיונות רצופים | `Event` (בדיקה בין פריימים) |
