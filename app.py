"""
============================================================
NICU HAND HYGIENE SYSTEM
Python Identification Server — app.py
============================================================
FUNCTION:
  - Receives violation photos from ESP32-CAM over WiFi
  - Runs DeepFace face recognition against staff_db/
  - Identifies which staff member violated
  - Sends enriched Telegram alert with:
      → Staff name
      → Confidence score
      → Number of violations today
      → Repeat offender warning

HOW TO RUN:
  python app.py

REQUIREMENTS:
  pip install -r requirements.txt

STAFF DATABASE STRUCTURE:
  staff_db/
  ├── staff_001_Dr_Ravi/
  │     ├── front.jpg         (facing camera directly)
  │     ├── left.jpg          (45° left side view)
  │     ├── right.jpg         (45° right side view)
  │     └── front_mask.jpg    (facing camera with mask on)
  └── staff_002_Nurse_Priya/
        ├── front.jpg
        ├── left.jpg
        ├── right.jpg
        └── front_mask.jpg
============================================================
"""

from flask import Flask, request, jsonify
from deepface import DeepFace
import requests
import datetime
import os
import json
import threading
import sqlite3
import cv2

app = Flask(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN   = "8608535596:AAH3trNFwW1dlJIifgR2c6_VCV37R7B_Glw"
TELEGRAM_CHAT_ID     = "-1003714141923"

STAFF_DB_PATH        = "staff_db"
VIOLATIONS_PATH      = "violations"
LOG_FILE             = "violations_log.json"
DB_FILE              = "violations.db"

CONFIDENCE_THRESHOLD = 60   # Minimum % to confirm identity (Facenet512 scale)
                             # Below this → reported as "Unknown"

# ──────────────────────────────────────────────────────────────────────────────

os.makedirs(VIOLATIONS_PATH, exist_ok=True)
os.makedirs(STAFF_DB_PATH,   exist_ok=True)


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_telegram_message(text):
    """Send a plain text message to Telegram group."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.status_code == 200:
            print("[Telegram] ✓ Message sent.")
        else:
            print(f"[Telegram] ✗ Error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Telegram] ✗ Failed: {e}")


def send_telegram_photo(image_path, caption):
    """Send violation photo with caption to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as photo_file:
            resp = requests.post(
                url,
                data={
                    "chat_id":    TELEGRAM_CHAT_ID,
                    "caption":    caption,
                    "parse_mode": "Markdown"
                },
                files={"photo": photo_file},
                timeout=15
            )
        if resp.status_code == 200:
            print("[Telegram] ✓ Photo alert sent.")
        else:
            print(f"[Telegram] ✗ Photo error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Telegram] ✗ Photo send failed: {e}")


# ─── FACE RECOGNITION ─────────────────────────────────────────────────────────

def identify_staff(image_path):
    """
    Compare violation photo against all photos in staff_db/.
    Returns: (staff_name, confidence_percent)
    If no match found or error: returns ("Unknown", 0)

    Model: Facenet512 — handles compressed/low-quality JPEG better than ArcFace.
    Detector: opencv — stable, no extra dependencies, works well for close-up faces.
    """
    print(f"[DeepFace] Running recognition on: {image_path}")

    if not os.path.exists(STAFF_DB_PATH):
        print("[DeepFace] ✗ staff_db/ folder not found!")
        return "Unknown", 0

    staff_folders = [
        f for f in os.listdir(STAFF_DB_PATH)
        if os.path.isdir(os.path.join(STAFF_DB_PATH, f))
    ]
    if not staff_folders:
        print("[DeepFace] ✗ No staff registered in staff_db/")
        return "Unknown", 0

    print(f"[DeepFace] Comparing against {len(staff_folders)} staff members...")

    try:
        results = DeepFace.find(
            img_path          = image_path,
            db_path           = STAFF_DB_PATH,
            model_name        = "Facenet512",  # handles ESP32-CAM JPEG compression well
            detector_backend  = "opencv",      # stable, no extra deps
            enforce_detection = False,         # don't crash on partial/masked faces
            silent            = True
        )

        if results and len(results[0]) > 0:
            top_match  = results[0].iloc[0]
            distance   = top_match["distance"]
            confidence = round((1 - distance) * 100, 1)

            print(f"[DeepFace] Best match distance: {distance:.4f} → Confidence: {confidence}%")

            if confidence >= CONFIDENCE_THRESHOLD:
                # Path example: staff_db/staff_001_Dr_Ravi/front.jpg
                identity_path = top_match["identity"].replace("\\", "/")
                parts         = identity_path.split("/")
                folder_name   = parts[-2] if len(parts) >= 2 else parts[0]

                # folder_name = "staff_001_Dr_Ravi" → skip "staff" and "001"
                name_parts = folder_name.split("_")
                staff_name = " ".join(name_parts[2:]) if len(name_parts) > 2 else folder_name

                print(f"[DeepFace] ✓ Identified: {staff_name} ({confidence}%)")
                return staff_name, confidence
            else:
                print(f"[DeepFace] ✗ Best confidence {confidence}% is below threshold {CONFIDENCE_THRESHOLD}%")
        else:
            print("[DeepFace] ✗ No face detected in violation photo (empty result)")

    except Exception as e:
        print(f"[DeepFace] ✗ Recognition error: {e}")

    return "Unknown", 0


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def initialize_database():
    """Create SQLite tables for violations and daily counts."""
    conn = sqlite3.connect(DB_FILE)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS violations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT NOT NULL,
                staff_name TEXT NOT NULL,
                confidence REAL NOT NULL,
                image_path TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_counts (
                date       TEXT NOT NULL,
                staff_name TEXT NOT NULL,
                count      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (date, staff_name)
            )
        """)
    conn.close()
    print(f"[DB] Initialized: {DB_FILE}")


def save_violation_to_db(timestamp, staff_name, confidence, image_path):
    """Insert violation record and increment the daily count."""
    today = timestamp.split("_")[0]
    conn  = sqlite3.connect(DB_FILE)
    with conn:
        conn.execute(
            "INSERT INTO violations (timestamp, staff_name, confidence, image_path) VALUES (?, ?, ?, ?)",
            (timestamp, staff_name, confidence, image_path)
        )
        conn.execute(
            "INSERT OR IGNORE INTO daily_counts (date, staff_name, count) VALUES (?, ?, 0)",
            (today, staff_name)
        )
        conn.execute(
            "UPDATE daily_counts SET count = count + 1 WHERE date = ? AND staff_name = ?",
            (today, staff_name)
        )
    conn.close()
    print(f"[DB] Saved violation for {staff_name} on {today}")


def count_today_violations(staff_name):
    """Return how many times a staff member has violated today."""
    today = datetime.date.today().isoformat()
    try:
        conn = sqlite3.connect(DB_FILE)
        cur  = conn.execute(
            "SELECT count FROM daily_counts WHERE date = ? AND staff_name = ?",
            (today, staff_name)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        print(f"[DB] Warning: count query failed: {e}")
        return 0


# ─── VIOLATION LOG ────────────────────────────────────────────────────────────

def log_violation(timestamp, staff_name, confidence, image_path):
    """Append violation record to JSON log file."""
    record = {
        "timestamp":  timestamp,
        "staff_name": staff_name,
        "confidence": confidence,
        "image":      image_path
    }
    existing = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            try:
                existing = json.load(f)
            except Exception:
                existing = []
    existing.append(record)
    with open(LOG_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[Log] Logged violation for: {staff_name}")


# ─── BACKGROUND PROCESSOR ─────────────────────────────────────────────────────

def process_violation(img_path, timestamp):
    """
    Runs in a background thread for every incoming violation photo.
    Identifies staff → logs → sends Telegram alert.
    img_path and timestamp are passed explicitly to avoid closure bugs.
    """
    try:
        # Step 1: identify
        staff_name, confidence = identify_staff(img_path)

        # Step 2: log
        log_violation(timestamp, staff_name, confidence, img_path)
        save_violation_to_db(timestamp, staff_name, confidence, img_path)

        # Step 3: count today's violations for this person
        today_count  = count_today_violations(staff_name)
        time_display = timestamp.replace("_", " ")

        # Step 4: build Telegram caption
        if staff_name != "Unknown":
            caption = (
                f"🔴 *HAND HYGIENE VIOLATION*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 *Time:* `{time_display}`\n"
                f"👤 *Staff:* {staff_name}\n"
                f"📊 *Confidence:* {confidence}%\n"
                f"🔢 *Violations today:* {today_count}\n"
                f"📍 *Location:* NICU Warmer Area\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            if today_count >= 3:
                caption += "\n\n🚨 *REPEAT OFFENDER*\nImmediate intervention required!"
            elif today_count == 2:
                caption += "\n\n⚠️ *Second violation today — please take action.*"
        else:
            caption = (
                f"🔴 *HAND HYGIENE VIOLATION*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 *Time:* `{time_display}`\n"
                f"👤 *Staff:* Not identified\n"
                f"   _(Not in database or face not visible)_\n"
                f"📍 *Location:* NICU Warmer Area\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )

        # Step 5: send
        send_telegram_photo(img_path, caption)
        print(f"[Server] Alert sent — {staff_name} | {confidence}% | {today_count} today")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"[ERROR] process_violation crashed: {e}")


# ─── MAIN ROUTE ───────────────────────────────────────────────────────────────

@app.route("/violation", methods=["GET", "POST"])
def handle_violation():
    """
    Receives violation photos from ESP32-CAM (multipart/form-data).
    Falls back to laptop camera if no photo uploaded (test mode).
    Returns 200 immediately; all heavy work runs in a background thread.
    """

    # ── ESP32-CAM upload path ──────────────────────────────────────────────────
    if "photo" in request.files:
        file = request.files["photo"]
        if file.filename == "":
            return jsonify({"status": "error", "message": "Empty filename"}), 400

        now       = datetime.datetime.now()
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        img_path  = os.path.join(VIOLATIONS_PATH, f"{timestamp}.jpg")

        file.save(img_path)
        file_size = os.path.getsize(img_path)

        print(f"\n{'=' * 60}")
        print(f"[Server] ✓ Photo received from ESP32-CAM")
        print(f"[Server]   Saved: {img_path} ({file_size} bytes)")

        # Pass img_path and timestamp explicitly — avoids closure/overwrite bug
        threading.Thread(
            target=process_violation,
            args=(img_path, timestamp),
            daemon=True
        ).start()

        return jsonify({"status": "received"}), 200

    # ── Laptop camera fallback (test mode) ────────────────────────────────────
    else:
        print("[Server] No photo uploaded — capturing from laptop camera (test mode)...")

        now       = datetime.datetime.now()
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        img_path  = os.path.join(VIOLATIONS_PATH, f"{timestamp}.jpg")

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[Server] ✗ Cannot access laptop camera.")
            return jsonify({"status": "error", "message": "Camera not available"}), 500

        ret, frame = cap.read()
        cap.release()

        if not ret:
            print("[Server] ✗ Capture failed.")
            return jsonify({"status": "error", "message": "Capture failed"}), 500

        cv2.imwrite(img_path, frame)
        file_size = os.path.getsize(img_path)

        print(f"\n{'=' * 50}")
        print(f"[Server] ✓ Captured from laptop camera")
        print(f"[Server]   Saved: {img_path} ({file_size} bytes)")

        threading.Thread(
            target=process_violation,
            args=(img_path, timestamp),
            daemon=True
        ).start()

        return jsonify({"status": "received"}), 200


# ─── STATS ────────────────────────────────────────────────────────────────────

@app.route("/stats", methods=["GET"])
def get_stats():
    """Today's violation summary. Open in browser: http://localhost:5000/stats"""
    today = datetime.date.today().isoformat()
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        records = [dict(r) for r in conn.execute(
            "SELECT id, timestamp, staff_name, confidence, image_path "
            "FROM violations WHERE timestamp LIKE ? ORDER BY timestamp",
            (today + "%",)
        ).fetchall()]
        staff_counts = {r["staff_name"]: r["count"] for r in conn.execute(
            "SELECT staff_name, count FROM daily_counts WHERE date = ?", (today,)
        ).fetchall()}
        conn.close()
        return jsonify({
            "date":        today,
            "total_today": len(records),
            "per_staff":   staff_counts,
            "records":     records
        })
    except Exception as e:
        print(f"[DB] Stats query failed: {e}")
        return jsonify({"date": today, "total_today": 0, "records": []})


# ─── STAFF LIST ───────────────────────────────────────────────────────────────

@app.route("/staff", methods=["GET"])
def list_staff():
    """Lists all registered staff. Open in browser: http://localhost:5000/staff"""
    if not os.path.exists(STAFF_DB_PATH):
        return jsonify({"staff": [], "count": 0})
    folders = sorted(
        f for f in os.listdir(STAFF_DB_PATH)
        if os.path.isdir(os.path.join(STAFF_DB_PATH, f))
    )
    staff_list = []
    for folder in folders:
        photos    = os.listdir(os.path.join(STAFF_DB_PATH, folder))
        jpg_count = sum(1 for p in photos if p.lower().endswith(".jpg"))
        staff_list.append({
            "folder":     folder,
            "photos":     jpg_count,
            "registered": jpg_count >= 3
        })
    return jsonify({"staff": staff_list, "count": len(staff_list)})


# ─── HEALTH CHECK ─────────────────────────────────────────────────────────────

@app.route("/ping", methods=["GET"])
def ping():
    """Health check. Open in browser: http://localhost:5000/ping"""
    staff_count = 0
    if os.path.exists(STAFF_DB_PATH):
        staff_count = len([
            f for f in os.listdir(STAFF_DB_PATH)
            if os.path.isdir(os.path.join(STAFF_DB_PATH, f))
        ])
    return jsonify({
        "status":      "running",
        "staff_in_db": staff_count,
        "threshold":   f"{CONFIDENCE_THRESHOLD}%",
        "model":       "Facenet512",
        "detector":    "opencv"
    }), 200


# ─── STARTUP ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    initialize_database()

    staff_count = 0
    if os.path.exists(STAFF_DB_PATH):
        staff_count = len([
            f for f in os.listdir(STAFF_DB_PATH)
            if os.path.isdir(os.path.join(STAFF_DB_PATH, f))
        ])

    print("=" * 55)
    print("  NICU HAND HYGIENE — IDENTIFICATION SERVER")
    print("=" * 55)
    print(f"  Staff registered : {staff_count} members")
    print(f"  Staff DB folder  : {STAFF_DB_PATH}/")
    print(f"  Violations folder: {VIOLATIONS_PATH}/")
    print(f"  Database file    : {DB_FILE}")
    print(f"  Confidence min   : {CONFIDENCE_THRESHOLD}%")
    print(f"  Model            : Facenet512 + opencv detector")
    print(f"  Endpoints        :")
    print(f"    POST /violation  ← ESP32-CAM sends photos here")
    print(f"    GET  /ping       ← health check")
    print(f"    GET  /stats      ← today's violations")
    print(f"    GET  /staff      ← registered staff list")
    print("=" * 55)

    app.run(
        host  = "0.0.0.0",
        port  = 5000,
        debug = False      # True causes auto-reload which interrupts recognition threads
    )