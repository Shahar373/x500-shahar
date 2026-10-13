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
- [ ] `pi.watchdog`, ‏`pi.blackbox`: אחרי מילוי שמות השירותים ונתיב הלוגים - PASS. כשמעצרים את הלוגר (ידנית) - FAIL תוך 120 שניות
- [ ] `pi.mavlink_router`: השירות מזוהה; רשימת ה-endpoints בפרטים תואמת ל-`/etc/mavlink-router/main.conf`
- [ ] `pi.camera`: ‏IMX219 מזוהה. בדוק גם כשהווידאו פועל (האם `--list-cameras` עדיין מראה את המצלמה)
- [ ] `pi.uptime`: אתחול ה-Pi במהלך הסשן מסומן WARN
- [ ] הסקריפטים הקיימים (`x500-status`, ‏`x500-preflight`) - אחרי שתשלח את הקוד שלהם, נחבר אותם עם פלט JSON

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
- [ ] `fc.prearm`: כשיש תקלת Pre-arm ידועה - FAIL, והודעת `PreArm:` מופיעה תוך כ-30 שניות
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

## וידאו

- [ ] `install_mediamtx.sh --dry-run`, ואז התקנה עם `--enable`; ‏`journalctl -u mediamtx` בלי שגיאות קונפיגורציה
- [ ] הווידאו נפתח באפליקציה דרך מנהרת SSH; ‏FPS בערך 15
- [ ] מדידה: CPU כולל, CPU של תהליכי המצלמה וטמפרטורה ב-640×480@15 במשך 10 דקות (מטבלת הביצועים בדוח)
- [ ] ניתוק Wi-Fi באמצע → "תמונה ישנה" תוך כ-2 שניות, ניסיונות חיבור מחדש מוגבלים, ואין תמונה קפואה שמוצגת כחיה
- [ ] הפעלת וידאו נחסמת כשהרחפן חמוש, וכשאין HEARTBEAT
- [ ] אחרי עצירה - המצלמה נכבית תוך כ-10 שניות (sourceOnDemandCloseAfter)

## Windows

- [ ] Setup.exe מותקן על מחשב Windows 10/11 נקי, בלי Python; האפליקציה עולה עם מסך אמיתי (לא offscreen)
- [ ] גופן עברי ו-RTL נכונים ב-Windows (Segoe UI)
- [ ] הסיסמה/passphrase נשמרים במנהל האישורים (Credential Manager → Windows Credentials → ‏`X500FlightStation`)
- [ ] הסרת ההתקנה משאירה את `%APPDATA%\X500FlightStation`
