# צד ה-Raspberry Pi

**עיקרון:** ניטור והתקנה הם דברים נפרדים. חיבור או בדיקה מהאפליקציה **לא מתקינים ולא משנים כלום** ב-Pi.
ה-collector נשלח דרך SSH ל-`python3 -` ורץ בקריאה בלבד. כל מה שבתיקייה הזו מריצים **ידנית**, רק אחרי שקראת אותו.

הסקריפטים הקיימים שלך (`x500-preflight`, ‏`x500-status`, mavlink-router, ה-watchdog והקופסה השחורה) לא נכללים במאגר
ולא שונו. אחרי שתשלח את הקוד שלהם, נוסיף להם פלט JSON בלי לשנות את ההתנהגות הקיימת שלהם.

## 1. מלאי (קריאה בלבד) - להריץ ראשון

```bash
bash pi/deploy/probe.sh > probe.txt 2>&1
```

הסקריפט בודק: מערכת הפעלה, ארכיטקטורה, כלי מצלמה, ‏`vcgencmd`, שמות שירותים, הגדרות mavlink-router, פורטים מאזינים,
רשת, Tailscale, התקני USB (מודם ה-ZTE) וטביעות אצבע של מפתחות SSH. לפי הפלט הזה בוחרים את פקודות ההתקנה.

## 2. וידאו: MediaMTX

```bash
bash pi/deploy/install_mediamtx.sh --dry-run      # מה הסקריפט היה עושה
sudo bash pi/deploy/install_mediamtx.sh            # התקנה בלי הפעלה
sudo systemctl enable --now mediamtx               # הפעלה (או: install_mediamtx.sh --enable)
journalctl -u mediamtx -n 50 --no-pager            # בדיקה שאין שגיאות קונפיגורציה
```

מה הסקריפט עושה:
- מוריד את MediaMTX ‏(גרסה נעולה: v1.21.2) עבור המעבד של ה-Pi, ומאמת SHA256 מול `checksums.sha256` של אותה גרסה.
- מתקין את `/usr/local/bin/mediamtx` ואת `/etc/mediamtx/mediamtx.yml` (מתוך `mediamtx-x500.yml`).
  קובץ קיים לא נדרס - החדש נשמר בשם `.new`.
- יוצר משתמש מערכת `mediamtx` בקבוצת `video`, ושירות systemd ‏`mediamtx.service`.
- מפעיל את השירות רק עם `--enable`.

מה הקונפיגורציה קובעת: נתיב `x500cam`, מקור `rpiCamera`, ‏`sourceOnDemand: yes`, ‏640×480@15, ‏1Mbps.
ה-RTSP וה-API מאזינים רק על localhost. לפני הפעלה, בדוק שהגרסה העדכנית ב-<https://github.com/bluenviron/mediamtx/releases>
תואמת, ושאין שדות שהשתנו (MediaMTX לא עולה אם יש בקובץ שדה לא מוכר).

באפליקציה: **הגדרות → Pi → שירות MediaMTX** = `mediamtx`.

## 3. (אופציונלי) התקנת ה-collector ב-Pi

רק אם אתה רוצה להריץ אותו גם ישירות ב-Pi, למשל מתוך `x500-status`:

```bash
sudo bash pi/deploy/install_collector.sh   # → /usr/local/bin/x500fs-collector, x500fs-camera
x500fs-collector --pretty | less
```

## מה ה-collector קורא

הכול לקריאה בלבד: ‏`/proc`, ‏`/sys`, ‏`/etc/os-release`, ‏`vcgencmd get_throttled` ו-`pmic_read_adc`, ‏`systemctl show`
(בלי start/stop), ‏`rpicam-hello --list-cameras`, ‏`ip -j addr/route`, ‏`iw dev <if> link`, ‏`tailscale status --json`
(רק המצב העצמי והעמית שמולו החיבור, בלי שמות משתמשים), התקני USB ב-sysfs, ‏`ss -ltnu`, ו-MediaMTX API ב-localhost.
אופציונלי: `ping` ליעד שהגדרת, וסקריפטים קיימים שסימנת `enabled`. כל פקודה מוגבלת בזמן, ואין sudo.
