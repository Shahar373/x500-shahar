# קטלוג הבדיקות האוטומטיות

נוצר אוטומטית מ-`tools/gen_checks_doc.py` (אל תערוך ידנית). הספים נמצאים בקבצי הפרופיל `bench.json` / `flight.json` ואפשר לשנות אותם. ערך `null` = לא מוגדר (הבדיקה תהיה UNKNOWN, ואם היא קריטית - התוצאה הכוללת לא תהיה "עברו"). ערך `"fc:PARAM"` = הסף נלקח מפרמטר של בקר הטיסה.

## Raspberry Pi

| מזהה | בדיקה | ספסל (bench) | טיסה (flight) | הצדקה |
|---|---|---|---|---|
| `pi.reachable` | חיבור ל-Pi (SSH) | **קריטי** | **קריטי** |  |
| `pi.identity` | זהות ה-Pi ומערכת ההפעלה | **קריטי**<br>`expected_model_contains`="Raspberry Pi 5" | **קריטי**<br>`expected_model_contains`="Raspberry Pi 5" |  |
| `pi.uptime` | זמן פעילות ואתחולים | לא קריטי | לא קריטי |  |
| `pi.temperature` | טמפרטורת מעבד ה-Pi | לא קריטי<br>`warn_c`=75<br>`fail_c`=82 | לא קריטי<br>`warn_c`=75<br>`fail_c`=82 | 75/82°C הם הספים של x500-health הקיים. לפי Raspberry Pi, ליבות ה-ARM מואטות בהדרגה מ-80°C (גבול קשיח 85°C). מקור: הסקריפט הקיים x500-health / x500-preflight ב-Pi (snapshot 14.10.2026). מדיניות - ניתן לשנות. |
| `pi.cpu` | עומס מעבד ה-Pi | לא קריטי<br>`warn_pct`=85<br>`fail_pct`=97 | לא קריטי<br>`warn_pct`=85<br>`fail_pct`=97 | מדיניות ברירת מחדל: עומס CPU גבוה עלול לעכב את mavlink-router ואת הווידאו. אין זה גבול חומרה. |
| `pi.memory` | זיכרון RAM פנוי | לא קריטי<br>`warn_available_pct`=10<br>`fail_available_pct`=5 | לא קריטי<br>`warn_available_pct`=10<br>`fail_available_pct`=5 | x500-health מזהיר כש-RAM בשימוש ≥ 90% (כלומר ≤ 10% פנוי). מקור: הסקריפט הקיים x500-health / x500-preflight ב-Pi (snapshot 14.10.2026). מדיניות - ניתן לשנות. |
| `pi.storage` | מקום פנוי באחסון | לא קריטי<br>`warn_free_mb`=2048<br>`fail_free_mb`=512<br>`warn_used_pct`=85<br>`fail_used_pct`=95 | לא קריטי<br>`warn_free_mb`=2048<br>`fail_free_mb`=512<br>`warn_used_pct`=85<br>`fail_used_pct`=95 | x500-health: דיסק בשימוש ≥ 85% אזהרה, ≥ 95% קריטי. בנוסף, סף מקום פנוי במגה-בייט עבור הלוגים. מקור: הסקריפט הקיים x500-health / x500-preflight ב-Pi (snapshot 14.10.2026). מדיניות - ניתן לשנות. |
| `pi.power_now` | מתח ה-Pi כעת (undervoltage/throttling) | לא קריטי | **קריטי** |  |
| `pi.power_history` | אירועי מתח מאז האתחול | לא קריטי | לא קריטי |  |
| `pi.watchdog` | Watchdog: מוגדר מול פעיל | לא קריטי | לא קריטי | 60-x500-watchdog.conf מגדיר RuntimeWatchdogSec=15s. 'מוגדר' אינו 'פעיל': נדרשת הוכחת ריצה (ping אחרון של systemd או /sys state=active). |
| `pi.blackbox` | קופסה שחורה: שירות ודגימות עדכניות | לא קריטי<br>`max_age_s`=30 | לא קריטי<br>`max_age_s`=30 | x500-preflight הקיים דרש קובץ שעודכן ב-30 השניות האחרונות. כאן נדרשת דגימה תקינה (לא רק mtime). הקצב בפועל = זמן הדגימה + sleep 10. |
| `pi.monitors` | שירותי ניטור (health / net) | לא קריטי | לא קריטי |  |
| `pi.fc_usb` | Pixhawk ב-USB (by-id) | לא קריטי | **קריטי** | x500-preflight הקיים: Pixhawk לא מזוהה ב-USB = FAIL בטיסה, WARN בספסל. |
| `pi.mavlink_router` | שירות mavlink-router | לא קריטי | **קריטי** |  |
| `pi.camera` | זיהוי מצלמה | לא קריטי | לא קריטי |  |
| `pi.tools` | סקריפטים קיימים ב-Pi | לא קריטי | לא קריטי |  |

## תקשורת

| מזהה | בדיקה | ספסל (bench) | טיסה (flight) | הצדקה |
|---|---|---|---|---|
| `link.path` | נתיב התקשורת בפועל | לא קריטי | **קריטי** |  |
| `link.rtt` | זמן תגובה (RTT) לאפליקציה | לא קריטי<br>`warn_ms`={"wifi": 100, "tailscale": 250, "lte": 400}<br>`fail_ms`={"wifi": 500, "tailscale": 1000, "lte": 1500} | לא קריטי<br>`warn_ms`={"wifi": 100, "tailscale": 250, "lte": 400}<br>`fail_ms`={"wifi": 500, "tailscale": 1000, "lte": 1500} | זמן תגובה ברמת האפליקציה (בקשת SSH). מדיניות לפי נתיב. זה לא מדד לבריאות MAVLink, שנבדק בנפרד. |
| `link.interruptions` | ניתוקים בסשן | לא קריטי<br>`window_s`=600<br>`warn_count`=1<br>`fail_count`=3 | לא קריטי<br>`window_s`=600<br>`warn_count`=1<br>`fail_count`=3 |  |
| `link.interfaces` | ממשקי רשת ונתיבים ב-Pi | לא קריטי | לא קריטי |  |
| `link.lte` | מודם LTE (ZTE F30): ממשק, שער, אינטרנט | **קריטי**<br>`required`="auto" | **קריטי**<br>`required`="auto" |  |
| `link.internet` | נתיב לאינטרנט ו-ICMP מה-Pi | לא קריטי | לא קריטי | x500-preflight הקיים מזהיר כשאין אינטרנט. אינטרנט אינו דרישה לבדיקת MAVLink. |
| `link.tailscale` | Tailscale ב-Pi | לא קריטי | לא קריטי | מקשיב ה-SRT של ה-Pi נקשר לכתובת ה-Tailscale; Tailscale נדרש לווידאו ולנתיבי Tailscale/LTE. |
| `link.ground` | הגעה מה-Pi לתחנת הקרקע (ICMP) | לא קריטי | לא קריטי |  |
| `link.mavlink_heartbeat` | HEARTBEAT מבקר הטיסה | **קריטי**<br>`max_age_s`=3.0 | **קריטי**<br>`max_age_s`=3.0 | ArduPilot שולח HEARTBEAT בקצב 1Hz. שלוש הודעות שלא הגיעו נחשבות לנתון ישן. |
| `link.telemetry` | טלמטריה נדרשת | **קריטי**<br>`max_age_s`={"SYS_STATUS": 5, "GPS_RAW_INT": 5, "EKF_STATUS_REPORT": 5} | **קריטי**<br>`max_age_s`={"SYS_STATUS": 5, "GPS_RAW_INT": 5, "EKF_STATUS_REPORT": 5, "GLOBAL_POSITION_INT": 5} |  |
| `link.gcs_peer` | תחנת קרקע (Mission Planner) בקישור | מושבת | לא קריטי | Mission Planner אחראי על השליטה בטיסה. אם לא נראה HEARTBEAT של תחנת קרקע בקישור הזה, ייתכן שהוא מחובר בנתיב אחר - לכן רק אזהרה. |

## Pixhawk וניווט

| מזהה | בדיקה | ספסל (bench) | טיסה (flight) | הצדקה |
|---|---|---|---|---|
| `fc.identity` | זיהוי בקר הטיסה וקושחה | **קריטי**<br>`expected_autopilot`="ARDUPILOTMEGA"<br>`expected_vehicle_types`=["QUADROTOR"]<br>`min_firmware`=null | **קריטי**<br>`expected_autopilot`="ARDUPILOTMEGA"<br>`expected_vehicle_types`=["QUADROTOR"]<br>`min_firmware`=null |  |
| `fc.armed` | מצב חימוש | **קריטי** | **קריטי** |  |
| `fc.mode` | מצב טיסה | לא קריטי<br>`expected_modes`=null | לא קריטי<br>`expected_modes`=null |  |
| `fc.arming_checks` | בדיקות החימוש של ArduPilot מופעלות | **קריטי**<br>`allowed_skipped`=[] | **קריטי**<br>`allowed_skipped`=[] | כל בדיקות החימוש של ArduPilot נדרשות, אלא אם רשמת במפורש כאן בדיקה שמותר לדלג עליה (למשל RC, אם אין מקלט). |
| `fc.prearm` | מצב Pre-arm של ArduPilot | לא קריטי<br>`max_age_s`=5<br>`require_diagnostic`=false<br>`diagnostic_max_age_s`=300 | **קריטי**<br>`max_age_s`=5<br>`require_diagnostic`=true<br>`diagnostic_max_age_s`=300 |  |
| `fc.sensors` | חיישנים: קיום, הפעלה ותקינות | **קריטי**<br>`required`=["gyro", "accel", "mag", "baro", "ahrs"]<br>`max_age_s`=5 | **קריטי**<br>`required`=["gyro", "accel", "mag", "baro", "ahrs", "gps"]<br>`max_age_s`=5 |  |
| `fc.logging` | רישום לוגים בבקר הטיסה | לא קריטי<br>`max_age_s`=5 | לא קריטי<br>`max_age_s`=5 |  |
| `fc.load` | עומס מעבד ושגיאות תקשורת בבקר הטיסה | לא קריטי<br>`warn_pct`=70<br>`fail_pct`=85<br>`max_age_s`=5 | **קריטי**<br>`warn_pct`=70<br>`fail_pct`=85<br>`max_age_s`=5 | x500-preflight הקיים: עומס Pixhawk < 70% תקין, < 85% אזהרה, אחרת קריטי. מקור: הסקריפט הקיים x500-health / x500-preflight ב-Pi (snapshot 14.10.2026). מדיניות - ניתן לשנות. |
| `gps.fix` | GPS: סוג קיבוע | לא קריטי<br>`min_fix_type`=3<br>`max_age_s`=5 | **קריטי**<br>`min_fix_type`=3<br>`max_age_s`=5 | ArduPilot דורש קיבוע תלת-ממדי (3D) לחימוש במצבים שתלויים ב-GPS. |
| `gps.hdop` | GPS: HDOP | לא קריטי<br>`max_hdop`="fc:GPS_HDOP_GOOD"<br>`max_age_s`=5 | **קריטי**<br>`max_hdop`="fc:GPS_HDOP_GOOD"<br>`max_age_s`=5 | הסף נלקח מהפרמטר GPS_HDOP_GOOD של בקר הטיסה (יחידות: HDOP×100, ברירת מחדל 140 = 1.4) - אותו סף ש-ArduPilot משתמש בו בבדיקת החימוש. |
| `gps.satellites` | GPS: מספר לוויינים | מושבת | מושבת | אין פרמטר חימוש של ArduPilot שקובע מספר לוויינים מינימלי. (ל-EKF3 יש סף פנימי קבוע של 6 לוויינים בבדיקת ה-GPS שלו, דרך EK3_GPS_CHECK.) הגדר סף רק אם יש לך הצדקה. |
| `gps.accuracy` | GPS: דיוק אופקי מדווח | מושבת | מושבת |  |
| `ekf.status` | EKF: מצב ושונויות | **קריטי**<br>`required_flags`=["attitude"]<br>`variance_warn`=0.5<br>`variance_fail`="fc:FS_EKF_THRESH"<br>`max_age_s`=5 | **קריטי**<br>`required_flags`=["attitude", "velocity_horiz", "velocity_vert", "pos_horiz_abs", "pos_vert_abs"]<br>`variance_warn`=0.5<br>`variance_fail`="fc:FS_EKF_THRESH"<br>`max_age_s`=5<br>`forbidden_flags`=["const_pos_mode", "gps_glitching", "uninitialized"] | 0.5 היא ההתחלה של הצבע הכתום בתצוגת EKF של Mission Planner. סף הכישלון הוא FS_EKF_THRESH של בקר הטיסה. |
| `nav.home` | נקודת בית (Home) | מושבת | **קריטי**<br>`max_age_s`=600 | נקודת הבית נדרשת עבור RTL. HOME_POSITION נבקשת בבקשה חד-פעמית (MAV_CMD_REQUEST_MESSAGE). |

## סוללה ותצורה

| מזהה | בדיקה | ספסל (bench) | טיסה (flight) | הצדקה |
|---|---|---|---|---|
| `batt.cells_config` | סוג סוללה ומספר תאים (בפרופיל) | לא קריטי | **קריטי** |  |
| `batt.voltage` | מתח סוללה | לא קריטי<br>`warn_v`="fc:BATT_LOW_VOLT"<br>`fail_v`="fc:BATT_CRT_VOLT"<br>`max_age_s`=5 | **קריטי**<br>`warn_v`="fc:BATT_LOW_VOLT"<br>`fail_v`="fc:BATT_CRT_VOLT"<br>`max_age_s`=5 | הספים נלקחים מפרמטרי הסוללה של בקר הטיסה. ערך 0 בפרמטר = לא מוגדר. |
| `batt.monitor` | הגדרת מד הסוללה (BATT_MONITOR) | לא קריטי<br>`expected_monitor`=null | **קריטי**<br>`expected_monitor`=null |  |
| `batt.current` | זרם סוללה | לא קריטי<br>`max_age_s`=5 | לא קריטי<br>`max_age_s`=5 |  |
| `batt.remaining` | קיבולת נותרת (הערכה) | לא קריטי<br>`warn_pct`=null<br>`max_age_s`=5 | לא קריטי<br>`warn_pct`=null<br>`max_age_s`=5 |  |
| `cfg.battery_monitor` | כיול מד הסוללה מול פרופיל צפוי | לא קריטי | **קריטי** |  |
| `cfg.failsafe` | הגדרות Failsafe מול פרופיל צפוי | לא קריטי | **קריטי** |  |
| `cfg.rtl` | הגדרות RTL מול פרופיל צפוי | לא קריטי | **קריטי** |  |
| `cfg.fence` | הגדרות גדר (Fence) מול פרופיל צפוי | לא קריטי | **קריטי** |  |
| `cfg.gcs_identity` | זהות האפליקציה מול תחנת הקרקע | לא קריטי | לא קריטי |  |

## וידאו

| מזהה | בדיקה | ספסל (bench) | טיסה (flight) | הצדקה |
|---|---|---|---|---|
| `video.stream` | זרם וידאו חי | לא קריטי<br>`min_fps`=null | לא קריטי<br>`min_fps`=null |  |
| `video.pi_service` | שירות הווידאו ב-Pi (x500-video) ומקודד | לא קריטי<br>`progress_max_age_s`=3 | לא קריטי<br>`progress_max_age_s`=3 | x500-video הקיים: progress שעודכן ב-3 שניות האחרונות. כאן זה רק ראיה לצד ה-Pi, לא לתמונה חיה במחשב. |

