# MAVLink - למה האפליקציה לא יכולה להשפיע על הטיסה

## מה האפליקציה שולחת

| הודעה | מתי | השפעה על הרחפן |
|---|---|---|
| `PARAM_REQUEST_READ` (לפי שם) | בתחילת סשן בדיקה ("הרץ בדיקה") | קריאה בלבד. הבקר עונה ב-`PARAM_VALUE`, ש-mavlink-router מעביר גם ל-Mission Planner (ערכים אמיתיים, ללא שינוי) |
| `COMMAND_LONG` עם `MAV_CMD_REQUEST_MESSAGE` (512) | כל 2 שניות (ניתן לשינוי): SYS_STATUS, ‏GPS_RAW_INT, ‏EKF_STATUS_REPORT, ‏GLOBAL_POSITION_INT, ‏BATTERY_STATUS, ‏ATTITUDE, ‏VFR_HUD. פעם אחת: AUTOPILOT_VERSION, ‏HOME_POSITION | בקשה חד-פעמית לשליחת הודעה. לא משנה קצבי שידור ולא כותבת פרמטרים. נתמך ב-ArduPilot 4.6.3 וב-4.7.x |
| `COMMAND_LONG` עם `MAV_CMD_RUN_PREARM_CHECKS` (401) | **רק** בלחיצה על "הרץ בדיקה", ורק כש-HEARTBEAT טרי מאשר שהרחפן לא חמוש (אפשר לכבות בהגדרות) | ArduPilot מריץ את בדיקות ה-Pre-arm ושולח `PreArm:` על כל כשל. לא מחמש, לא משנה מצב ולא כותב פרמטרים |

**זה הכול.** כל שליחה עוברת דרך פונקציה אחת (`MavlinkClient._send`) עם רשימה סגורה של סוגים מותרים.
כל דבר אחר (HEARTBEAT, ‏ARM/DISARM, ‏SET_MODE, ‏PARAM_SET, ‏REQUEST_DATA_STREAM ועוד) זורק חריגה. זה נבדק ב-`tests/test_mavlink_safety.py`.
אפשר לכבות גם את הבקשות (הגדרות → MAVLink), ואז האפליקציה רק מאזינה.

בכוונה **לא** בשימוש:
- `REQUEST_DATA_STREAM` ו-`MAV_CMD_SET_MESSAGE_INTERVAL` - שניהם משנים קצב שידור לכל הערוץ הטורי, כלומר גם ל-Mission Planner.
  ב-Copter הם לא נשמרים ל-EEPROM, אבל הם משנים את מה ש-Mission Planner מקבל.
- `PARAM_REQUEST_LIST` - מציף את הקישור בכל הפרמטרים.

## אבחון Pre-arm (תיקון 2 מהסקירה)

הסקריפט הישן סימן "PREARM_CLEAR" לכל ACK, ואם לא הגיעה הודעת PreArm תוך 6 שניות. עכשיו:
1. הפקודה נשלחת רק אם HEARTBEAT טרי מהרחפן הנבחר מראה **לא חמוש**. אם הוא חמוש, אין HEARTBEAT, או מצב פסיבי - היא לא נשלחת (`not_sent` + סיבה).
2. רק `COMMAND_ACK` שמגיע **מהרחפן הנבחר** (sysid של הרחפן, compid 1), וממוען לאפליקציה, נחשב. ACK ממערכת אחרת מתעלמים ממנו.
3. `ACCEPTED` פירושו רק שהבדיקות **הורצו** (לפי `GCS_MAVLINK::handle_command_run_prearm_checks`), לא שעברו.
   `TEMPORARILY_REJECTED` (חמוש/עסוק), `UNSUPPORTED`, ‏`DENIED`, ‏`FAILED`, או היעדר ACK תוך 3 שניות - כולם UNKNOWN עם הסבר.
4. אחרי ACCEPTED נדרש SYS_STATUS **שהגיע אחרי ה-ACK** (לפחות שנייה אחריו). התוצאה נקבעת מביט ה-PREARM_CHECK בו
   (present/enabled/healthy) ומהודעות `PreArm:` שהגיעו מהרחפן הנבחר (כולל STATUSTEXT מפוצל שהורכב מחדש לפי `chunk_seq`).
   בלי SYS_STATUS טרי - UNKNOWN. שקט אינו הוכחה.
5. בפרופיל flight ‏`require_diagnostic: true`: בלי אבחון שהושלם בחמש הדקות האחרונות, `fc.prearm` נשאר UNKNOWN ולכן התוצאה הכוללת לא "עברה".
6. כשהרחפן חמוש `fc.prearm` הוא UNKNOWN (ArduPilot מדווח את הביט כתקין תמיד במצב חמוש).

## בחירת הרחפן וסינון מקורות (תיקון 3)

- הרחפן נבחר לפי `sysid של בקר הטיסה` בהגדרות, או (0) לפי ה-HEARTBEAT הראשון של autopilot עם compid 1.
- כל ההודעות (טלמטריה, פרמטרים, STATUSTEXT, ‏ACK) מסוננות לפי sysid+compid של הרחפן הנבחר. HEARTBEAT של Mission Planner
  (type GCS, למשל 255/190) נרשם כ"עמית" ואינו נחשב לרחפן.
- אם נראה עוד autopilot באותו קישור ולא נקבע sysid, `fc.identity` מזהירה.

## GCS failsafe ו-sysid

לפי קוד המקור של ArduPilot (‏`GCS_Common.cpp`, ‏Copter 4.6.3 / 4.7.2), טיימר ה-GCS failsafe מתאפס **רק** מאחת משלוש הודעות,
וגם זה רק כשהן נשלחות מ-sysid של תחנת קרקע:
- `HEARTBEAT`
- `RC_CHANNELS_OVERRIDE`
- `MANUAL_CONTROL`

ה-sysid של תחנת הקרקע מוגדר ב-`SYSID_MYGCS`, שנקרא ב-4.7 ‏`MAV_GCS_SYSID` (עם טווח אופציונלי `MAV_GCS_SYSID_HI`).

האפליקציה לא שולחת אף אחת מההודעות האלה, ומשתמשת ב-sysid ‏247 / compid ‏25 (כלי ה-Pi, `x500-preflight` ו-`x500-status`,
משתמשים ב-246 / 26 מתוך `/etc/x500/x500.conf`). לכן:
- היא לא יכולה "להחזיק" את ה-failsafe ולהסתיר ניתוק של Mission Planner.
- היא לא נעשית "תחנת הקרקע" של הרחפן.
- הבדיקה `cfg.gcs_identity` מזהירה אם ה-sysid שלה נופל בטווח של תחנת הקרקע.
- אם `SYSID_ENFORCE=1` (ב-4.7: ‏`MAV_OPTIONS` bit 0), הבקר מתעלם מבקשות שלא מגיעות מתחנת הקרקע. הנתונים יופיעו אז כחסרים (UNKNOWN), עם הסבר.

## חיבור

- האפליקציה לא פותחת את הפורט הטורי של ה-Pixhawk. היא מתחברת ל-endpoint קיים של **mavlink-router**,
  שמשכפל את הקישור לכל הלקוחות (כולל Mission Planner).
- ברירת המחדל: ערוץ `direct-tcpip` בתוך SSH אל `127.0.0.1:5760` על ה-Pi. לא צריך לחשוף את הפורט ברשת.
- אפשרויות נוספות: TCP ישיר (אם mavlink-router מאזין ברשת), או האזנה ל-UDP (אם הוגדר endpoint שמכוון למחשב).

## פירוש הנתונים

- היעדר הודעת `PreArm:` **אינו** הוכחה שהבדיקות עברו. מצב ה-Pre-arm נקבע מהביט `MAV_SYS_STATUS_PREARM_CHECK` ב-SYS_STATUS
  (ArduPilot 4.1 ומעלה). כשהרחפן חמוש הביט תמיד "תקין". אם enabled=0 (בדיקות מושבתות), הוא לא מעיד על דבר.
- `ARMING_CHECK` (עד 4.6, "בדיקות לביצוע", ‏1=הכול) הוחלף ב-4.7 ב-`ARMING_SKIPCHK` ("בדיקות לדילוג", ‏0=כלום, ‏‎-1‎=הכול).
  האפליקציה מטפלת בשניהם. כל בדיקה מושבתת נכשלת, אלא אם היא הותרה במפורש בפרופיל (`allowed_skipped`).
- ערכי sentinel של MAVLink (‏`UINT16_MAX`, ‏`-1`, ‏`255`) מתפרשים כ"לא ידוע", ולעולם לא כמספר.
