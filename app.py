"""
============================================================
NICU HAND HYGIENE SYSTEM
Python Identification Server — app.py
============================================================
FUNCTION:
  - Receives violation photos from ESP32-CAM via HTTP POST
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
  │     ├── front.jpg    (facing camera directly)
  │     ├── left.jpg     (45° left side view)
  │     └── right.jpg    (45° right side view)
  └── staff_002_Nurse_Priya/
        ├── front.jpg
        ├── left.jpg
        └── right.jpg
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

app = Flask(__name__)

# ─── CONFIGURATION — UPDATE THESE ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN   = "8608535596:AAH3trNFwW1dlJIifgR2c6_VCV37R7B_Glw"  # Your bot token
TELEGRAM_CHAT_ID     = "-1003714141923"   # Your group/channel chat ID

STAFF_DB_PATH        = "staff_db"         # Folder with staff photos
VIOLATIONS_PATH      = "violations"       # Folder to save violation photos
LOG_FILE             = "violations_log.json"
DB_FILE              = "violations.db"
CONFIDENCE_THRESHOLD = 80                 # Minimum % to confirm identity
                                          # Below this → reported as "Unknown"
# ──────────────────────────────────────────────────────────────────────────────

# Create folders if they don't exist
os.makedirs(VIOLATIONS_PATH, exist_ok=True)
os.makedirs(STAFF_DB_PATH,   exist_ok=True)


# ─── TELEGRAM FUNCTIONS ───────────────────────────────────────────────────────

def send_telegram_message(text):
    """Send a plain text message to Telegram group."""
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code == 200:
            print("[Telegram] ✓ Message sent.")
        else:
            print(f"[Telegram] ✗ Error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Telegram] ✗ Failed: {e}")


def send_telegram_photo(image_path, caption):
    """Send the violation photo with enriched caption to Telegram."""
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
    If no match found: returns ("Unknown", 0)

    HOW IT WORKS:
    - DeepFace scans every photo in every subfolder of staff_db/
    - Compares face features using ArcFace model
    - Returns the closest matching staff member
    - Distance 0 = perfect match, Distance 1 = no match
    - We convert distance to confidence percentage
    """
    print(f"[DeepFace] Running recognition on: {image_path}")

    # Check if staff database has any photos
    if not os.path.exists(STAFF_DB_PATH):
        print("[DeepFace] ✗ staff_db/ folder not found!")
        return "Unknown", 0

    staff_folders = [f for f in os.listdir(STAFF_DB_PATH)
                     if os.path.isdir(os.path.join(STAFF_DB_PATH, f))]
    if not staff_folders:
        print("[DeepFace] ✗ No staff registered in staff_db/")
        return "Unknown", 0

    print(f"[DeepFace] Comparing against {len(staff_folders)} staff members...")

    try:
        results = DeepFace.find(
            img_path          = image_path,
            db_path           = STAFF_DB_PATH,
            model_name        = "ArcFace",       # Best accuracy for masked/side views
            detector_backend  = "retinaface",    # Best face detector
            enforce_detection = False,           # Don't crash if face is partially hidden
            silent            = True             # Suppress progress bars
        )

        # results is a list of DataFrames — one per face found in the image
        if results and len(results[0]) > 0:
            top_match  = results[0].iloc[0]    # Best match
            distance   = top_match["distance"]
            confidence = round((1 - distance) * 100, 1)

            print(f"[DeepFace] Best match distance: {distance:.3f} → Confidence: {confidence}%")

            if confidence >= CONFIDENCE_THRESHOLD:
                # Extract name from folder path
                # Path example: staff_db/staff_001_Dr_Ravi/front.jpg
                identity_path = top_match["identity"].replace("\\", "/")
                parts         = identity_path.split("/")
                folder_name   = parts[-2] if len(parts) >= 2 else parts[0]

                # folder_name = "staff_001_Dr_Ravi"
                # Split by _ and skip first two parts (staff, 001)
                name_parts = folder_name.split("_")
                staff_name = " ".join(name_parts[2:]) if len(name_parts) > 2 else folder_name

                print(f"[DeepFace] ✓ Identified: {staff_name} ({confidence}%)")
                return staff_name, confidence
            else:
                print(f"[DeepFace] ✗ Confidence {confidence}% below threshold {CONFIDENCE_THRESHOLD}%")

    except Exception as e:
        print(f"[DeepFace] ✗ Recognition error: {e}")

    return "Unknown", 0


# ─── VIOLATION LOGGING ────────────────────────────────────────────────────────

def log_violation(timestamp, staff_name, confidence, image_path):
    """Save violation record to JSON log file."""
    record = {
        "timestamp":  timestamp,
        "staff_name": staff_name,
        "confidence": confidence,
        "image":      image_path
    }

    existing_log = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            try:
                existing_log = json.load(f)
            except Exception:
                existing_log = []

    existing_log.append(record)

    with open(LOG_FILE, "w") as f:
        json.dump(existing_log, f, indent=2)

    print(f"[Log] Violation logged for: {staff_name}")


def initialize_database():
    """Create SQLite tables for violations and daily counts."""
    conn = sqlite3.connect(DB_FILE)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                staff_name TEXT NOT NULL,
                confidence REAL NOT NULL,
                image_path TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_counts (
                date TEXT NOT NULL,
                staff_name TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (date, staff_name)
            )
        """)
    conn.close()
    print(f"[DB] Initialized database: {DB_FILE}")


def save_violation_to_db(timestamp, staff_name, confidence, image_path):
    """Insert violation record and update the daily count."""
    today = timestamp.split("_")[0]
    conn = sqlite3.connect(DB_FILE)
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
    print(f"[DB] Violation saved for {staff_name} on {today}")


def count_today_violations(staff_name):
    """Count how many times a staff member has violated today."""
    today = datetime.date.today().isoformat()  # e.g. "2025-01-15"

    if os.path.exists(DB_FILE):
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.execute(
                "SELECT count FROM daily_counts WHERE date = ? AND staff_name = ?",
                (today, staff_name)
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return row[0]
        except Exception as e:
            print(f"[DB] Warning: count query failed: {e}")

    if not os.path.exists(LOG_FILE):
        return 0

    with open(LOG_FILE, "r") as f:
        try:
            log = json.load(f)
        except Exception:
            return 0

    count = sum(
        1 for record in log
        if record.get("staff_name") == staff_name
        and record.get("timestamp", "").startswith(today)
    )
    return count


# ─── MAIN ROUTE — RECEIVES PHOTO FROM ESP32-CAM ───────────────────────────────

@app.route("/violation", methods=["POST"])
def handle_violation():
    """
    ESP32-CAM POSTs raw JPEG bytes to this endpoint.
    This function:
    1. Saves the photo with a timestamp filename
    2. Runs face recognition in a background thread
    3. Sends enriched Telegram alert
    Returns immediately so ESP32-CAM doesn't time out.
    """

    # Generate timestamp for filename
    now       = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    img_path  = os.path.join(VIOLATIONS_PATH, f"{timestamp}.jpg")

    # Save the photo
    with open(img_path, "wb") as f:
        f.write(request.data)

    file_size = os.path.getsize(img_path)
    print(f"\n{'='*50}")
    print(f"[Server] ✓ Violation photo received!")
    print(f"[Server]   Saved: {img_path} ({file_size} bytes)")

    # Run recognition in background thread
    # (So ESP32-CAM gets a fast HTTP 200 response)
    def process_violation():
        # Step 1: Identify staff
        staff_name, confidence = identify_staff(img_path)

        # Step 2: Log the violation
        log_violation(timestamp, staff_name, confidence, img_path)
        save_violation_to_db(timestamp, staff_name, confidence, img_path)

        # Step 3: Count today's violations for this person
        today_count = count_today_violations(staff_name)

        # Step 4: Build Telegram alert message
        time_display = timestamp.replace("_", " ")

        if staff_name != "Unknown":
            # ── Staff identified ──
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

            # Add repeat offender warning
            if today_count >= 3:
                caption += "\n\n🚨 *REPEAT OFFENDER*\nImmediate intervention required!"
            elif today_count == 2:
                caption += "\n\n⚠️ *Second violation today — please take action.*"

        else:
            # ── Staff not identified ──
            caption = (
                f"🔴 *HAND HYGIENE VIOLATION*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 *Time:* `{time_display}`\n"
                f"👤 *Staff:* Not identified\n"
                f"   _(Not in database or face not visible)_\n"
                f"📍 *Location:* NICU Warmer Area\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )

        # Step 5: Send photo + caption to Telegram
        send_telegram_photo(img_path, caption)

        print(f"[Server] Alert sent — {staff_name} | {confidence}% | {today_count} today")
        print(f"{'='*50}\n")

    # Start background processing
    threading.Thread(target=process_violation, daemon=True).start()

    # Return immediately to ESP32-CAM
    return jsonify({"status": "received"}), 200


# ─── STATS ROUTE ──────────────────────────────────────────────────────────────

@app.route("/stats", methods=["GET"])
def get_stats():
    """Returns today's violation summary. Open in browser to check."""
    today = datetime.date.today().isoformat()

    if os.path.exists(DB_FILE):
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id, timestamp, staff_name, confidence, image_path FROM violations WHERE timestamp LIKE ? ORDER BY timestamp",
                (today + "%",)
            )
            records = [dict(row) for row in cur.fetchall()]

            cur = conn.execute(
                "SELECT staff_name, count FROM daily_counts WHERE date = ?",
                (today,)
            )
            staff_counts = {row["staff_name"]: row["count"] for row in cur.fetchall()}
            conn.close()

            return jsonify({
                "date":        today,
                "total_today": len(records),
                "per_staff":   staff_counts,
                "records":     records
            })
        except Exception as e:
            print(f"[DB] Warning: stats query failed: {e}")

    if not os.path.exists(LOG_FILE):
        return jsonify({"date": today, "total_today": 0, "records": []})

    with open(LOG_FILE, "r") as f:
        try:
            log = json.load(f)
        except Exception:
            return jsonify({"date": today, "total_today": 0, "records": []})

    today_records = [r for r in log if r.get("timestamp", "").startswith(today)]

    # Count per staff member
    staff_counts = {}
    for r in today_records:
        name = r.get("staff_name", "Unknown")
        staff_counts[name] = staff_counts.get(name, 0) + 1

    return jsonify({
        "date":        today,
        "total_today": len(today_records),
        "per_staff":   staff_counts,
        "records":     today_records
    })


# ─── REGISTERED STAFF ROUTE ───────────────────────────────────────────────────

@app.route("/staff", methods=["GET"])
def list_staff():
    """Lists all registered staff members. Open in browser to check."""
    if not os.path.exists(STAFF_DB_PATH):
        return jsonify({"staff": [], "count": 0})

    folders = [f for f in os.listdir(STAFF_DB_PATH)
               if os.path.isdir(os.path.join(STAFF_DB_PATH, f))]

    staff_list = []
    for folder in sorted(folders):
        photos = os.listdir(os.path.join(STAFF_DB_PATH, folder))
        jpg_count = sum(1 for p in photos if p.endswith(".jpg"))
        staff_list.append({
            "folder":     folder,
            "photos":     jpg_count,
            "registered": jpg_count >= 3
        })

    return jsonify({"staff": staff_list, "count": len(staff_list)})


# ─── HEALTH CHECK ─────────────────────────────────────────────────────────────

@app.route("/ping", methods=["GET"])
def ping():
    """Quick health check. Open in browser: http://localhost:5000/ping"""
    staff_count = 0
    if os.path.exists(STAFF_DB_PATH):
        staff_count = len([f for f in os.listdir(STAFF_DB_PATH)
                           if os.path.isdir(os.path.join(STAFF_DB_PATH, f))])
    return jsonify({
        "status":       "running",
        "staff_in_db":  staff_count,
        "threshold":    f"{CONFIDENCE_THRESHOLD}%"
    }), 200


# ─── STARTUP ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    initialize_database()

    # Count registered staff
    staff_count = 0
    if os.path.exists(STAFF_DB_PATH):
        staff_count = len([f for f in os.listdir(STAFF_DB_PATH)
                           if os.path.isdir(os.path.join(STAFF_DB_PATH, f))])

    print("=" * 55)
    print("  NICU HAND HYGIENE — IDENTIFICATION SERVER")
    print("=" * 55)
    print(f"  Staff registered : {staff_count} members")
    print(f"  Staff DB folder  : {STAFF_DB_PATH}/")
    print(f"  Violations folder: {VIOLATIONS_PATH}/")
    print(f"  Database file    : {DB_FILE}")
    print(f"  Confidence min   : {CONFIDENCE_THRESHOLD}%")
    print(f"  Endpoints:")
    print(f"    POST /violation  ← ESP32-CAM sends photo here")
    print(f"    GET  /ping       ← Health check")
    print(f"    GET  /stats      ← Today's violations")
    print(f"    GET  /staff      ← Registered staff list")
    print("=" * 55)

    if staff_count == 0:
        print("\n⚠️  WARNING: No staff registered in staff_db/")
        print("   All violations will be reported as 'Unknown'")
        print("   Run: python register_staff.py  to add staff\n")

    app.run(host="0.0.0.0", port=5000, debug=False)
