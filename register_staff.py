"""
============================================================
NICU HAND HYGIENE SYSTEM
Staff Photo Registration Tool — register_staff.py
============================================================
USE THIS TO:
  - Add new staff members to the database
  - View all registered staff
  - Remove a staff member

RUN:
  python register_staff.py

HOW THE CAMERA CONNECTION WORKS:
  The ESP32-CAM announces itself on the network as
  "esp32cam.local" using mDNS. This script calls
  http://esp32cam.local/capture to pull each photo.
  You never need to know or hardcode the camera's IP address.

PHOTO REQUIREMENTS:
  - 4 photos per staff member captured from ESP32-CAM
  - Stand at the actual camera mounting distance (60-90 cm)
  - Match the angle the camera will see during violations:
      side_right.jpg  → right profile (as seen from wall camera)
      three_qtr.jpg   → 45° angle
      front.jpg       → facing camera directly
      mask.jpg        → same angle as side_right but with mask on
  - Wear hospital uniform in all photos
  - Good lighting, face clearly visible
============================================================
"""

import os
import glob
import shutil
import requests
from deepface import DeepFace

STAFF_DB_PATH = "staff_db"
ESP_HOSTNAME  = "esp32cam.local"   # mDNS hostname — never needs to change


def verify_face(image_path):
    """
    Check if a face is detectable in the photo.
    Uses yunet — same detector used in app.py for consistency.
    Returns True if face found, False otherwise.
    """
    try:
        faces = DeepFace.extract_faces(
            img_path          = image_path,
            detector_backend  = "yunet",     # matches app.py detector
            enforce_detection = True
        )
        return len(faces) > 0
    except Exception:
        return False


def rebuild_index():
    """
    Rebuild DeepFace embedding index for all models used in app.py.
    Must be called after any change to staff_db/.
    Deletes stale .pkl files first so nothing is cached from old data.
    """
    print("\n🔄 Rebuilding face recognition index...")
    print("   (This takes 1–3 minutes the first time. Please wait.)\n")

    # Delete stale cache files first
    for pkl in glob.glob(os.path.join(STAFF_DB_PATH, "*.pkl")):
        os.remove(pkl)
        print(f"   Cleared stale cache: {os.path.basename(pkl)}")

    # Find any registered photo to use as sample input
    samples = glob.glob(os.path.join(STAFF_DB_PATH, "*", "*.jpg"))
    if not samples:
        print("   ⚠️  No photos found — index will build on first app.py run.")
        return

    sample = samples[0]

    # Build index for BOTH models used in app.py
    # Must match the MODELS dict in app.py exactly
    for model in ["GhostFaceNet", "Facenet512"]:
        try:
            print(f"   Building {model} index...")
            DeepFace.find(
                img_path          = sample,
                db_path           = STAFF_DB_PATH,
                model_name        = model,
                detector_backend  = "yunet",
                enforce_detection = False,
                silent            = False
            )
            print(f"   ✅ {model} index ready.\n")
        except Exception as e:
            print(f"   ⚠️  {model} index will rebuild on next app.py run. ({e})\n")


def register_new_staff():
    """Interactive staff registration flow."""
    print("\n" + "=" * 50)
    print("  REGISTER NEW STAFF MEMBER")
    print("=" * 50)
    print(f"\n  Camera: http://{ESP_HOSTNAME}/capture")
    print(f"  Make sure ESP32-CAM is powered on and on the same WiFi.\n")

    # Get staff details
    staff_id   = input("Enter Staff ID (e.g. 001, 002): ").strip().zfill(3)
    staff_name = input("Enter Staff Name (use underscore for space, e.g. Dr_Ravi): ").strip()
    staff_name = staff_name.replace(" ", "_")

    folder_name = f"staff_{staff_id}_{staff_name}"
    folder_path = os.path.join(STAFF_DB_PATH, folder_name)

    # Check if already exists
    if os.path.exists(folder_path):
        print(f"\n⚠️  Staff folder already exists: {folder_path}")
        choice = input("Overwrite? (yes/no): ").strip().lower()
        if choice != "yes":
            print("Registration cancelled.")
            return
        shutil.rmtree(folder_path)
        print("Old data removed.")

    os.makedirs(folder_path)

    # Photo list — matched to the actual camera angle (wall mount, 60-90cm)
    # Order matters: start with the angle most commonly seen during violations
    photos = {
        "front.jpg": "FRONT — Staff faces camera directly (0°)",
        "left.jpg" : "LEFT  — Staff turns 45° to their left",
        "right.jpg": "RIGHT — Staff turns 45° to their right",
        "front_mask.jpg": "FRONT WITH MASK — Staff faces camera with mask on (0°)"
    }

    print(f"\nRegistering: {staff_name} (ID: {staff_id})")
    print("Stand at the actual camera distance (60–90 cm) for each photo.")
    print("Match the angle the camera will see during real violations.\n")

    saved_photos = []

    for filename, description in photos.items():
        attempt = 0
        while True:
            attempt += 1
            print(f"📷  Photo {len(saved_photos)+1}/{len(photos)}: {description}")
            input("   Press Enter when ready to capture...")

            try:
                url      = f"http://{ESP_HOSTNAME}/capture"
                response = requests.get(url, timeout=10)

                if response.status_code != 200:
                    print(f"   ❌ Camera error: HTTP {response.status_code}")
                    if attempt < 3:
                        print("   Retrying...\n")
                        continue
                    else:
                        skip = input("   Skip this photo? (yes/no): ").strip().lower()
                        if skip == "yes":
                            print(f"   Skipped {filename}.\n")
                            break
                        attempt = 0
                        continue

                # Save photo
                dest = os.path.join(folder_path, filename)
                with open(dest, "wb") as f:
                    f.write(response.content)

                file_size = os.path.getsize(dest)
                print(f"   📁 Saved ({file_size} bytes) — checking for face...",
                      end=" ", flush=True)

                if verify_face(dest):
                    print(f"✅ Face detected — {filename} saved.")
                    saved_photos.append(filename)
                    print()
                    break
                else:
                    print("❌ No face detected.")
                    os.remove(dest)
                    if attempt < 3:
                        print("   Tips:")
                        print("   → Face must be clearly visible from this angle")
                        print("   → Improve lighting if shadows on face")
                        print("   → Check you are at 60–90 cm distance")
                        print("   → For mask photo: eyes must still be visible\n")
                    else:
                        skip = input("   Skip this photo? (yes/no): ").strip().lower()
                        if skip == "yes":
                            print(f"   Skipped {filename}.\n")
                            break
                        attempt = 0

            except requests.exceptions.ConnectionError:
                print(f"   ❌ Cannot reach {ESP_HOSTNAME}")
                print(f"   → Is ESP32-CAM powered on?")
                print(f"   → Is it on the same WiFi network?")
                print(f"   → Check Serial Monitor for its IP address\n")
                if attempt >= 3:
                    skip = input("   Skip this photo? (yes/no): ").strip().lower()
                    if skip == "yes":
                        print(f"   Skipped {filename}.\n")
                        break
                    attempt = 0

            except requests.exceptions.Timeout:
                print(f"   ❌ Camera timed out — try again.\n")
                if attempt >= 3:
                    skip = input("   Skip this photo? (yes/no): ").strip().lower()
                    if skip == "yes":
                        print(f"   Skipped {filename}.\n")
                        break
                    attempt = 0

    if len(saved_photos) == 0:
        print("\n❌ No photos saved. Registration failed.")
        shutil.rmtree(folder_path)
        return

    # Summary
    print("\n" + "=" * 50)
    print(f"✅ Registration Complete!")
    print(f"   Staff  : {staff_name}")
    print(f"   ID     : {staff_id}")
    print(f"   Folder : {folder_path}/")
    print(f"   Photos : {', '.join(saved_photos)}")
    print("=" * 50)

    if len(saved_photos) < len(photos):
        print(f"\n⚠️  Only {len(saved_photos)}/{len(photos)} photos saved.")
        print("   Recognition accuracy may be lower.")
        print("   Re-register to add missing photos later.\n")

    # Rebuild index for both models
    rebuild_index()
    print("✅ Done. This staff member will now be recognized in violation photos.\n")


def list_all_staff():
    """Display all registered staff members."""
    print("\n" + "=" * 50)
    print("  REGISTERED STAFF MEMBERS")
    print("=" * 50)

    if not os.path.exists(STAFF_DB_PATH):
        print("  ⚠️  staff_db/ folder not found.")
        return

    folders = sorted([
        f for f in os.listdir(STAFF_DB_PATH)
        if os.path.isdir(os.path.join(STAFF_DB_PATH, f))
    ])

    if not folders:
        print("  No staff registered yet.")
        print("  Run option 1 to add staff.")
    else:
        print(f"  Total: {len(folders)} staff members\n")
        for i, folder in enumerate(folders, 1):
            folder_path = os.path.join(STAFF_DB_PATH, folder)
            photos      = [f for f in os.listdir(folder_path) if f.endswith(".jpg")]
            status      = "✅ Ready" if len(photos) >= 4 else f"⚠️  Only {len(photos)}/4 photos"
            print(f"  {i:2}. {folder}")
            print(f"       Photos : {', '.join(photos)}")
            print(f"       Status : {status}\n")


def remove_staff():
    """Remove a staff member from the database."""
    list_all_staff()

    if not os.path.exists(STAFF_DB_PATH):
        return

    folders = sorted([
        f for f in os.listdir(STAFF_DB_PATH)
        if os.path.isdir(os.path.join(STAFF_DB_PATH, f))
    ])

    if not folders:
        return

    print("Enter the NUMBER of the staff to remove (or 0 to cancel):")
    try:
        choice = int(input("Choice: ").strip())
    except ValueError:
        print("Invalid input.")
        return

    if choice == 0:
        return

    if 1 <= choice <= len(folders):
        folder  = folders[choice - 1]
        confirm = input(f"\nRemove '{folder}'? Cannot be undone. (yes/no): ").strip().lower()
        if confirm == "yes":
            shutil.rmtree(os.path.join(STAFF_DB_PATH, folder))
            # Clear all .pkl cache files so stale embeddings are removed
            for pkl in glob.glob(os.path.join(STAFF_DB_PATH, "*.pkl")):
                os.remove(pkl)
            print(f"✅ '{folder}' removed.")
            print("   Index will rebuild on next app.py run.")
        else:
            print("Cancelled.")
    else:
        print("Invalid choice.")


# ─── MAIN MENU ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(STAFF_DB_PATH, exist_ok=True)

    print("\n" + "=" * 50)
    print("  NICU STAFF PHOTO REGISTRATION TOOL")
    print("=" * 50)
    print(f"\n  Camera address: http://{ESP_HOSTNAME}/capture")
    print("\n  1. Register new staff member")
    print("  2. View all registered staff")
    print("  3. Remove a staff member")
    print("  4. Exit")

    choice = input("\nEnter choice (1–4): ").strip()

    if choice == "1":
        register_new_staff()
    elif choice == "2":
        list_all_staff()
    elif choice == "3":
        remove_staff()
    elif choice == "4":
        print("Bye!")
    else:
        print("Invalid choice.")