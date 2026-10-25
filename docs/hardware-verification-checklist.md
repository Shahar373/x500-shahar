# רשימת אימות על החומרה האמיתית

כל מה שכתוב כאן **עוד לא נבדק על הרחפן האמיתי**. הקוד נבדק רק מול סימולציה, שרת SSH מדומה ו-autopilot מדומה.
סמן כל שורה אחרי שבדקת אותה על הרחפן, ושמור את הדוח (JSON+HTML) מאותו מפגש.

## Raspberry Pi 5 (x500pi)

- [ ] `probe.sh` רץ ומראה: מערכת הפעלה (Bookworm/Trixie), ‏`rpicam-hello`, ‏`vcgencmd`, ‏`ip -j`
- [ ] טביעת האצבע בחלון האפליקציה זהה לפלט של `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
- [ ] חיבור במפתח SSH עובד; חיבור עם מפתח שגוי נדחה
- [ ] `pi.identity`: שם מארח ודגם "Raspberry Pi 5" מזוהים נכון
- [ ] `pi.temperature`, ‏`pi.cpu`, ‏`pi.memory`, ‏`pi.storage` מציגים ערכים סבירים (להשוות מול `vcgencmd measure_temp`, ‏`top`, ‏`df -h`)
- [ ] `pi.power_now` / `pi.power_history`: הערך זהה ל-`vcgencmd get_throttled`. ‏`EXT5V` מופיע (אם `vcgencmd pmic_read_adc` נתמך)
- [ ] `pi.watchdog`: ‏"מוגדר" (‏RuntimeWatchdogSec=15s) ו"פעיל" (‏`WatchdogLastPingTimestampMonotonic` עדכני / `/sys/class/watchdog/watchdog0/state`) מוצגים בנפרד. לבדוק: גרסת systemd ב-Pi מספקת את השדה?
- [ ] `pi.blackbox`: שירות פעיל **וגם** דגימה תקינה אחרונה. כשעוצרים את הלוגר (ידנית, על הספסל) - הבדיקה נכשלת אחרי כ-3 מחזורים. הקצב שנמדד (בפרטים) מול ה-10 שניות המוגדרות
- [ ] `pi.monitors`: ‏x500-health-monitor ו-x500-net-monitor מזוהים
- [ ] `pi.fc_usb`: ה-Pixhawk מזוהה ב-`/dev/serial/by-id` לפי התבנית (ואם הגדרת שם מדויק - הוא תואם)
- [ ] `pi.mavlink_router`: השירות מזוהה; רשימת ה-endpoints בפרטים תואמת ל-`/etc/mavlink-router/main.conf`
- [ ] `pi.camera`: ‏IMX219 מזוהה. בדוק גם כשהווידאו פועל (האם `--list-cameras` עדיין מראה את המצלמה)
- [ ] `pi.uptime`: אתחול ה-Pi במהלך הסשן מסומן WARN
- [ ] פריסה מפורשת (`deploy_x500.sh`): הרצה יבשה מראה diff, ‏`--apply` שומר גיבוי ב-`/var/backups/x500fs/`
- [ ] `x500-health --key` / `--machine`: כשאין `vcgencmd` (או שנכשל) מוצג `unavailable`, לא `temp=0`
- [ ] `x500-preflight bench` / `flight` ב-Pi: אותן תוצאות כמו באפליקציה (חוץ מבדיקות שרק המחשב רואה, שמסומנות N/A); קוד יציאה 0/1/2
- [ ] `x500-status`: טלמטריה מהרחפן הנבחר עם גיל לכל ערך; `x500-blackbox-status`: בלי טענת "14 ימים"
- [ ] לאחר `sudo systemctl restart x500-blackbox x500-net-monitor` - ה-CSV ממשיך באותן עמודות, עם `NA` כשאין ערך

## תקשורת

- [ ] Wi-Fi: ‏`link.path` = ‏`wlan0 (Wi-Fi)`; ‏`link.interfaces` מציג את הממשקים
- [ ] Tailscale: חיבור דרך כתובת ה-Tailscale → ‏`link.path` מזהה Tailscale, ישיר או DERP, ואת הממשק שמתחתיו
- [ ] LTE: מודם ה-ZTE F30 מזוהה ב-USB (‏vendor ‏`19d2`?) ← לוודא את ה-vendor/product מתוך `probe.sh`; ממשק הרשת שלו (rndis/cdc_ether?) מסווג כ-LTE
- [ ] LTE: עם יעד ping מוגדר - ping דרך הממשק מצליח; כשהמודם מנותק - FAIL (רק בנתיב LTE)
- [ ] Wi-Fi bench בלי מודם: ‏`link.lte` = ‏NOT_APPLICABLE ולא חוסם
- [ ] `link.rtt`: ערכים סבירים בכל נתיב. ניתוק Wi-Fi לכמה שניות → ניתוק נרשם, והתחברות מחדש אוטומטית

## Pixhawk 6C / ArduPilot

- [ ] גרסת הקושחה (`fc.identity`) תואמת למה ש-Mission Planner מציג
- [ ] האפליקציה **לא** מופיעה כתחנת קרקע: ב-Mission Planner → ‏MAVLink Inspector אין HEARTBEAT מ-sysid ‏247
- [ ] כשסוגרים את Mission Planner (לא בטיסה!) - ‏`link.gcs_peer` עובר ל-WARN, ו-ArduPilot לא "רואה" את האפליקציה כ-GCS
- [ ] `fc.armed`: חמוש/לא חמוש מוצג נכון (בדיקה על הספסל **בלי מדחפים**, חימוש מ-Mission Planner בלבד)
- [ ] `fc.prearm`: "הרץ בדיקה" שולח `MAV_CMD_RUN_PREARM_CHECKS` רק כשלא חמוש. ב-MAVLink Inspector: ACK ‏ACCEPTED מ-sysid של הרחפן; עם תקלה ידועה - FAIL והודעות `PreArm:` בפרטים
- [ ] כשחמוש (ספסל, בלי מדחפים): הפקודה לא נשלחת, ו-`fc.prearm` = ‏UNKNOWN
- [ ] `fc.arming_checks`: קריאת ARMING_CHECK (4.6) או ARMING_SKIPCHK (4.7) נכונה
- [ ] `fc.sensors`: התאמה למצב החיישנים ב-Mission Planner; ה-RC מוצג כלא תקין כשאין מקלט
- [ ] `gps.fix`, ‏`gps.hdop`: בחוץ - קיבוע 3D ו-HDOP תואמים ל-Mission Planner. בפנים - FAIL/WARN
- [ ] `ekf.status`: הדגלים והשונויות תואמים ל-EKF Status של Mission Planner
- [ ] `nav.home`: מופיע אחרי קיבוע GPS
- [ ] `fc.logging`: תואם להתנהגות כרטיס ה-SD
- [ ] `cfg.gcs_identity`: ‏SYSID_MYGCS/MAV_GCS_SYSID נקרא; אין התנגשות עם 247
- [ ] אין שינוי פרמטרים: השוואת פרמטרים ב-Mission Planner לפני ואחרי מפגש עם האפליקציה (Compare Params) - אין הבדל

## סוללה (PM02 V3)

- [ ] `batt.voltage` תואם למודד חיצוני (±0.2V) ול-Mission Planner
- [ ] `batt.current` סביר במנוחה; ‏`batt.monitor` = ‏`BATT_MONITOR=4`
- [ ] על USB בלבד (בלי סוללה): המתח מוצג כ-UNKNOWN (לא PASS)

## וידאו (SRT)

- [ ] `x500-video degraded` ידנית ב-Pi, ואז "הפעל וידאו" באפליקציה (כתובת ה-Tailscale של ה-Pi): תמונה חיה, ‏FPS בערך 20
- [ ] "הפעל וידאו" כשהשירות כבוי: אם אין sudo בלי סיסמה - מופיעה ההסבר (לא נשלחת סיסמה). אחרי `install_video_sudoers.sh <user> --apply` - ההפעלה עובדת
- [ ] ארבע השורות בלשונית "מצלמה" תואמות: `systemctl status x500-video@degraded`, קובץ ה-progress, מצב החיבור, ותמונה חיה
- [ ] מדידה: CPU כולל, CPU של `rpicam-vid`/`ffmpeg` וטמפרטורה ב-degraded במשך 10 דקות (מטבלת הביצועים בדוח), לפני מעבר ל-normal
- [ ] ניתוק Wi-Fi/Tailscale באמצע → "תמונה ישנה" תוך כ-2 שניות, ניסיונות חיבור מחדש מוגבלים; אחרי שהשירות מופעל מחדש (‏`RestartSec=3`) התמונה חוזרת
- [ ] הפעלת וידאו או החלפת פרופיל נחסמת כשהרחפן חמוש, וכשאין HEARTBEAT
- [ ] "עצור וידאו" עוצר את השירות ב-Pi רק אם האפליקציה הפעילה אותו
- [ ] מצלמת USB ‏(Logitech C270): `v4l2-ctl --list-devices` מראה אותה; `v4l2-ctl -d /dev/video0 --list-formats-ext` כולל MJPG ‏640×480 ב-30/20 FPS
- [ ] עם C270 מחוברת ו-`CAMERA=auto`: ‏`journalctl -u x500-video@degraded` מראה `Camera: usb`, יש תמונה חיה, ו-`pi.camera` עובר גם בלי IMX219
- [ ] ניתוק ה-C270 תוך כדי זרם: השירות עולה מחדש עם `Camera: csi`, והתמונה חוזרת ממצלמת ה-Pi
- [ ] CPU וטמפרטורה עם C270 ב-degraded ו-normal (פענוח MJPEG ב-Pi), לפני שימוש בטיסה

## Windows

- [ ] Setup.exe מותקן על מחשב Windows 10/11 נקי, בלי Python; האפליקציה עולה עם מסך אמיתי (לא offscreen)
- [ ] חומת האש של Windows לא חוסמת את FFmpeg (‏SRT caller יוצא - בדרך כלל לא נדרש אישור)
- [ ] גופן עברי ו-RTL נכונים ב-Windows (Segoe UI)
- [ ] הסיסמה/passphrase נשמרים במנהל האישורים (Credential Manager → Windows Credentials → ‏`X500FlightStation`)
- [ ] הסרת ההתקנה משאירה את `%APPDATA%\X500FlightStation`

## mavlink-router והממשק

- [ ] `sudo systemctl stop mavlink-router` (על הספסל, Mission Planner מנותק): תוך מחזור איסוף אחד מופיע הכפתור האדום "הפעל mavlink-router מחדש"
- [ ] בלי `install_video_sudoers.sh --router`: הלחיצה מסבירה שנדרשת סיסמה ולא שולחת כלום נוסף. אחרי `--router --apply`: ה-router עולה, MAVLink חוזר ל-live והכפתור נעלם
- [ ] כש-mavlink-router רץ הכפתור לא מוצג בכלל
- [ ] MAVLink ב-UDP עם "לקבל רק מכתובת" = כתובת ה-Pi: נתונים מגיעים; עם כתובת שגויה - אין נתונים
- [ ] על מסך Windows אמיתי: "רק בעיות" ברשימת הבדיקות, קטגוריות תקינות מקופלות, לשונית המצלמה קטנה כשהווידאו כבוי

