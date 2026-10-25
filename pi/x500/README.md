# הסקריפטים המתוקנים ב-Pi

הגרסאות האלה שומרות על **שמות הפקודות, הדגלים וקודי היציאה** של הקוד הקיים (`claude-handoff/pi-reference/current/`),
ומתקנות את מה שנמצא בסקירה (`claude-handoff/PI_CODE_REVIEW.md`). הן לא נבדקו על ה-Pi האמיתי. הן נבדקו רק מול פקודות
מדומות, autopilot מדומה ושורש קבצים זמני (`tests/test_pi_scripts.py`).

**כלל:** שום דבר כאן לא מותקן מהאפליקציה. מתקינים ידנית עם `deploy_x500.sh`, אחרי שקראת את ה-diff.

## הגדרות: `/etc/x500/x500.conf`

קובץ אחד במקום ערכים שהיו כתובים בתוך הסקריפטים. דוגמה: [`etc/x500/x500.conf.example`](etc/x500/x500.conf.example).

| מפתח | מה | היה |
|---|---|---|
| `GROUND_IP` | כתובת תחנת הקרקע (ל-ping בלבד) | סמן `GROUND_STATION_IP_NOT_CONFIGURED` בתוך 4 סקריפטים |
| `ZTE_GATEWAY` | שער מודם ה-ZTE | סמן בתוך 3 סקריפטים |
| `WIFI_IF` / `LTE_IF` / `TAILSCALE_IF` | ממשקים | `wlan0` / `eth1` / `tailscale0` קבועים |
| `X500_VENV_PYTHON` | Python של ה-venv עם pymavlink | נתיב בתוך `/home/<user>/...` |
| `FC_BYID_GLOB` / `EXPECTED_FC_BYID` | זהות ה-Pixhawk ב-`/dev/serial/by-id` | מספר סידורי קבוע |
| `MAVLINK_SYSID` / `MAVLINK_COMPID` | ‏sysid של כלי ה-Pi (‏246/26) | ‏pymavlink ברירת מחדל |
| `BLACKBOX_RETENTION_DAYS` | מחיקת CSV ישנים (רק אם מוגדר) | הודפס "14 days" בלי שום מחיקה |

ערך ריק = "לא מוגדר", והבדיקה מדווחת זאת (לא "עבר"). ערכים שעדיין מכילים `NOT_CONFIGURED` / `PI_USER` מתעלמים מהם,
ו-`deploy_x500.sh` מסרב להתקין כל עוד הם בקובץ. הקובץ חייב להיות של root ולא ניתן לכתיבה לאחרים (שירותי root קוראים אותו).

## מה השתנה בכל פקודה

| פקודה | נשמר | תוקן |
|---|---|---|
| `x500-health` (+`--key`, `--machine`, חדש: `--json`) | ספים 75/82°C, ‏RAM ‏90%, דיסק 85/95%, קודי יציאה 0/1/2 | `vcgencmd` שנכשל = `unavailable` (לא `temp=0` / `throttled=0x0`) והמצב UNKNOWN (יציאה 2, לא 0). דגלי מתח **כעת** (`power=`) ו**מאז האתחול** (`power_hist=`) בנפרד: כעת = CRITICAL, היסטוריה = WARN גלוי |
| `x500-health-monitor` | ללא שינוי (קורא את `x500-health --key`) | — |
| `x500-preflight [bench\|flight]` | הפלט `[PASS]/[WARN]/[FAIL]`, ‏SUMMARY, קודי יציאה 0/1/2 | רץ על **אותן בדיקות כמו האפליקציה** (`x500fs.pi_tools.preflight`): חיבור MAVLink אחד, בחירת רחפן וסינון לפי sysid/compid, ‏ACK של Pre-arm נבדק לפי `result` ומקור, שקט ≠ עבר, סוללה/GPS/EKF לפי הפרופיל. בדיקה קריטית לא ידועה (INCOMPLETE) = יציאה 2. בדיקות שרק המחשב רואה (SSH, ‏RTT, תמונת וידאו) = N/A. `--json` לפלט מכונה |
| `x500-status` | Pi, רשת, שירותים, טלמטריה | ערך לא זמין = `unavailable`; טלמטריה מהרחפן הנבחר בלבד, עם גיל לכל הודעה; "מקודד מתקדם" לא נקרא "CONNECTED" |
| `x500-net` / `x500-net-monitor` | אותם נושאים, יומן ב-journal | ממשקים וכתובות מהקובץ; כל שורה אומרת מה נמדד (ממשק / נתיב / ICMP לשער / ICMP לאינטרנט / Tailscale / ICMP לתחנת הקרקע); כתובת לא מוגדרת = `not_configured` |
| `x500-blackbox` | אותו CSV (14 עמודות, אותו header) | `NA` במקום ערך ריק; ממשקים/כתובות מהקובץ; `video_state=encoding` במקום `connected`; ההמתנה מקוזזת בזמן הדגימה; מחיקה רק עם `BLACKBOX_RETENTION_DAYS` |
| `x500-blackbox-status` | שירות, קבצים, ערכים אחרונים | "תקין" דורש **גם** שירות פעיל **וגם** דגימה תקינה עדכנית; הקצב **נמדד** מהדגימות; בלי "Retention: 14 days" |
| `x500-fc-detect` | by-id, מועמדים, הרשאות, mavlink-router | השוואה לתבנית/שם שהוגדרו; מזהיר על סמן בתצורת ה-router; לא פותח את הפורט |
| `x500-diag` | אותם מקורות | **stdout בלבד** (JSON), לא כותב קבצים ב-Pi. האפליקציה מציעה ייצוא מוסתר (כפתור "ייצוא אבחון") |
| `x500-video-run` | אותם פרופילים, פורט, אפשרויות SRT וקובץ progress | בוחר מצלמה לפי `CAMERA` ב-`/etc/x500/video.conf`: ‏`auto` (‏USB אם מחוברת, אחרת מצלמת ה-Pi), ‏`csi`, ‏`usb`. כותב את הבחירה ל-`/run/x500-video-<profile>.source`. פירוט: [docs/video.md](../../docs/video.md#מצלמת-usb-במקום-מצלמת-ה-pi) |
| `x500-video`, ‏`x500-video@.service`, ‏`/etc/x500/video.conf` | **ללא שינוי** (‏`deploy_x500.sh` לא נוגע בהם) | — |

לא נוגעים בהם בכלל: תצורת mavlink-router, תצורת ה-watchdog וה-journald, קבצי ה-service.

## פריסה

```bash
# במחשב:
python tools/make_pi_bundle.py                       # dist/x500-pi-bundle-<גרסה>.tar.gz (+SHA256SUMS בפנים)
# ב-Pi:
tar xzf x500-pi-bundle-*.tar.gz && cd x500-pi-bundle-*/
sudo bash deploy_x500.sh                 # הרצה יבשה: מה ישתנה + diff. לא משנה כלום
sudo bash deploy_x500.sh --apply         # פעם ראשונה: מעתיק את הדוגמה ל-/etc/x500/x500.conf ועוצר
sudo nano /etc/x500/x500.conf            # למלא GROUND_IP, ZTE_GATEWAY, X500_VENV_PYTHON
sudo bash deploy_x500.sh                 # שוב הרצה יבשה
sudo bash deploy_x500.sh --apply         # גיבוי ל-/var/backups/x500fs/<זמן>/ ואז התקנה
```

מה מותקן: הפקודות ב-`/usr/local/bin` ו-`/opt/x500/bin` (כמו היום, כולל `x500-video-run`), והספרייה המשותפת ב-`/opt/x500fs/lib`.
**שום שירות לא מופעל מחדש.** הקופסה השחורה ומנטר הרשת ממשיכים להריץ את הקוד הישן עד שתריץ על הספסל:
`sudo systemctl restart x500-blackbox x500-net-monitor`.

חזרה לאחור: `sudo cp -a /var/backups/x500fs/<זמן>/. /` ואז אותה הפעלה מחדש.

## הרשאת וידאו מצומצמת (אופציונלי)

```bash
sudo bash install_video_sudoers.sh <user>            # מציג את הכלל ובודק אותו עם visudo
sudo bash install_video_sudoers.sh <user> --apply    # /etc/sudoers.d/x500-video
```

הכלל מתיר בדיוק שש פקודות: `/usr/bin/systemctl start|stop x500-video@high|normal|degraded.service`. אין restart, אין
יחידות אחרות ואין תווים כלליים.
