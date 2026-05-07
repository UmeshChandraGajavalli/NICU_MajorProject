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

PHOTO REQUIREMENTS:
  - 3 photos per staff member:
      front.jpg  → facing camera directly
      left.jpg   → 45° left side view
      right.jpg  → 45° right side view
  - Wear hospital uniform in photos
  - Good lighting, face clearly visible
  - At least 640x480 resolution
  - If staff wears mask in hospital → take photos WITH mask
============================================================
"""

import os
import shutil
from deepface import DeepFace

STAFF_DB_PATH = "staff_db"


def verify_face(image_path):
    """
    Check if a face is detectable in the photo.
    Returns True if face found, False otherwise.
    """
    try:
        faces = DeepFace.extract_faces(
            img_path         = image_path,
            detector_backend = "retinaface",
            enforce_detection = True
        )
        return len(faces) > 0
    except Exception:
        return False


def register_new_staff():
    """Interactive staff registration flow."""
    print("\n" + "=" * 50)
    print("  REGISTER NEW STAFF MEMBER")
    print("=" * 50)

    # Get staff details
    staff_id = input("\nEnter Staff ID (e.g. 001, 002): ").strip().zfill(3)

    staff_name = input("Enter Staff Name (use underscore for space, e.g. Dr_Ravi or Nurse_Priya): ").strip()
    staff_name = staff_name.replace(" ", "_")  # Auto-replace spaces

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

    # Photo collection
    photos = {
        "front.jpg": "FRONT — Staff faces camera directly (0°)",
        "left.jpg" : "LEFT  — Staff turns 45° to their left",
        "right.jpg": "RIGHT — Staff turns 45° to their right"
    }

    print(f"\nRegistering: {staff_name} (ID: {staff_id})")
    print("Provide paths to 3 photos.\n")
    print("TIP: Drag and drop the photo file into this terminal window to get its path.\n")

    saved_photos = []

    for filename, description in photos.items():
        attempt = 0
        while True:
            attempt += 1
            print(f"📷  Photo {len(saved_photos)+1}/3: {description}")
            photo_path = input("   Path to photo: ").strip().strip('"').strip("'")

            if not photo_path:
                print("   Please enter a file path.\n")
                continue

            if not os.path.exists(photo_path):
                print(f"   ❌ File not found: {photo_path}")
                print("   Please check the path and try again.\n")
                continue

            if not photo_path.lower().endswith(('.jpg', '.jpeg', '.png')):
                print("   ❌ Please use a JPG or PNG photo.\n")
                continue

            print("   🔍 Checking for face...", end=" ", flush=True)
            if verify_face(photo_path):
                dest = os.path.join(folder_path, filename)
                shutil.copy2(photo_path, dest)
                print(f"✅ Face detected — saved as {filename}")
                saved_photos.append(filename)
                print()
                break
            else:
                print("❌ No face detected.")
                if attempt < 3:
                    print("   Tips for better photos:")
                    print("   → Make sure face is clearly visible")
                    print("   → Better lighting (no shadows on face)")
                    print("   → Move closer to camera")
                    print("   → Remove anything blocking the face\n")
                else:
                    skip = input("   Skip this photo and continue? (yes/no): ").strip().lower()
                    if skip == "yes":
                        print(f"   Skipped {filename}.\n")
                        break
                    attempt = 0

    if len(saved_photos) == 0:
        print("No photos saved. Registration failed.")
        shutil.rmtree(folder_path)
        return

    # Registration complete
    print("=" * 50)
    print(f"✅ Registration Complete!")
    print(f"   Staff  : {staff_name}")
    print(f"   ID     : {staff_id}")
    print(f"   Folder : {folder_path}/")
    print(f"   Photos : {', '.join(saved_photos)}")
    print("=" * 50)

    if len(saved_photos) < 3:
        print(f"\n⚠️  Only {len(saved_photos)}/3 photos saved.")
        print("   Recognition accuracy will be lower.")
        print("   Add more photos later by re-registering this staff member.\n")

    # Rebuild face index
    print("\n🔄 Rebuilding face recognition index...")
    print("   (This takes 1–3 minutes the first time. Please wait.)\n")

    try:
        import glob
        sample = glob.glob(os.path.join(folder_path, "*.jpg"))
        if sample:
            DeepFace.find(
                img_path          = sample[0],
                db_path           = STAFF_DB_PATH,
                model_name        = "ArcFace",
                enforce_detection = False,
                silent            = False
            )
            print("\n✅ Face index updated successfully!")
            print("   This staff member will now be recognized in violation photos.\n")
    except Exception as e:
        print(f"\n⚠️  Index will rebuild automatically when app.py runs next.")
        print(f"   (Error: {e})\n")


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
        print("  Run this script and choose option 1 to add staff.")
    else:
        print(f"  Total: {len(folders)} staff members\n")
        for i, folder in enumerate(folders, 1):
            folder_path = os.path.join(STAFF_DB_PATH, folder)
            photos = [f for f in os.listdir(folder_path) if f.endswith(".jpg")]
            status = "✅" if len(photos) >= 3 else f"⚠️  Only {len(photos)}/3 photos"
            print(f"  {i:2}. {folder}")
            print(f"       Photos: {', '.join(photos)} {status}")

    print()


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
        folder = folders[choice - 1]
        confirm = input(f"Remove '{folder}'? This cannot be undone. (yes/no): ").strip().lower()
        if confirm == "yes":
            shutil.rmtree(os.path.join(STAFF_DB_PATH, folder))
            # Remove old face index so it rebuilds
            import glob
            for pkl in glob.glob(os.path.join(STAFF_DB_PATH, "*.pkl")):
                os.remove(pkl)
            print(f"✅ '{folder}' removed from database.")
            print("   Face index will rebuild on next run of app.py.")
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
