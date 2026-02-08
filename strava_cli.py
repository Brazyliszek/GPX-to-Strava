import os
import time
import requests
import glob
import xml.etree.ElementTree as ET
from pathlib import Path
import math
from collections import Counter
from datetime import datetime

CONFIG_FILE = "strava_config.txt"
UPLOADED_LOG = "uploaded_activities.txt"
API_BASE = "https://www.strava.com"

ACTIVITY_MAP = {
    "ride": ["ride", "bike", "bicycle", "cycling", "rower", "rad", "velo", "mtb", "roadbike"],
    "run": ["run", "running", "jog", "jogging", "bieganie"],
    "swim": ["swim", "swimming", "pool", "open water", "basen", "pływanie"],
    "walk": ["walk", "walking", "spacer", "walking activity"],
    "hike": ["hike", "hiking", "trek", "trekking", "mountain", "góry"],
    "workout": ["workout", "training", "gym", "fitness", "strength", "hiit"],
}

NORMALIZED_OUTPUT = {
    "ride": "Ride",
    "run": "Run",
    "swim": "Swim",
    "walk": "Walk",
    "hike": "Hike",
    "workout": "Workout",
    "other": "Other"
}

# ----------------- Load and save config -----------------
def load_config():
    config = {}
    if not os.path.exists(CONFIG_FILE):
        return config
    with open(CONFIG_FILE, "r") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                config[k] = v
    return config

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        for k, v in config.items():
            f.write(f"{k}={v}\n")

# ----------------- Uploaded activities tracking -----------------
def is_already_uploaded(filename, filesize):
    """Check if file was already uploaded"""
    if not os.path.exists(UPLOADED_LOG):
        return False
    with open(UPLOADED_LOG, "r") as f:
        for line in f:
            if line.strip() == f"{filename}+{filesize}":
                return True
    return False

def log_uploaded_file(filepath):
    """Log successfully uploaded file to prevent re-uploading"""
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    
    with open(UPLOADED_LOG, "a") as f:
        f.write(f"{filename}+{filesize}\n")
    
    print(f"📝 Logged to {UPLOADED_LOG}: {filename}+{filesize}")

# ----------------- Tutorial setup -----------------
def tutorial_setup():
    print("=== Strava Configuration (one-time setup) ===")
    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()

    print("\nOpen this URL in your browser:\n")
    print(
        f"https://www.strava.com/oauth/authorize?"
        f"client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri=http://localhost"
        f"&scope=activity:write"
        f"&approval_prompt=force"
    )

    code = input("\nPaste the OAuth code here: ").strip()

    if "code=" in code:
        code = code.split("code=")[1]
    code = code.split("&")[0]

    r = requests.post(
        f"{API_BASE}/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )

    data = r.json()
    if "access_token" not in data:
        print("\n❌ OAuth error received from Strava:")
        print(data)
        exit(1)

    config = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": str(data["expires_at"]),
    }

    save_config(config)
    print("\n✅ Configuration saved to strava_config.txt")
    return config

# ----------------- Refresh token -----------------
def refresh_token(config):
    print("🔄 Refreshing access token...")
    r = requests.post(
        f"{API_BASE}/oauth/token",
        data={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": config["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    data = r.json()
    config["access_token"] = data["access_token"]
    config["refresh_token"] = data["refresh_token"]
    config["expires_at"] = str(data["expires_at"])
    save_config(config)
    print("✅ Token refreshed successfully")

# ----------------- Check token expiry -----------------
def check_token(config):
    if time.time() > int(config["expires_at"]):
        refresh_token(config)


# ----------------- Determine activity type -----------------

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


def normalize_activity(label):
    if not label:
        return "Other"
    label = label.lower()
    return NORMALIZED_OUTPUT.get(label, "Other")


def test_metadata(root):
    """
    Test 1:
    Szukanie jednoznacznych metadanych w extensions:
    Garmin, Strava, Locus, Polar, Suunto, GPX generic
    """
    candidates = []

    for elem in root.iter():
        tag = elem.tag.lower()
        text = (elem.text or "").lower()

        # Najczęstsze pola spotykane w GPX
        if any(k in tag for k in [
            "activity", "sport", "type", "activitytype",
            "tracktype", "keywords"
        ]):
            candidates.append(text)

    for text in candidates:
        for act, keywords in ACTIVITY_MAP.items():
            if any(k in text for k in keywords):
                print(f"[META] Detected {act} from metadata")
                return act

    return 0


def test_keywords(root):
    """
    Test 2:
    Szukanie słów kluczowych w:
    - name
    - desc
    - cmt
    - keywords
    """
    texts = []

    for tag in ["name", "desc", "cmt", "keywords"]:
        for el in root.findall(f".//{{*}}{tag}"):
            if el.text:
                texts.append(el.text.lower())

    blob = " ".join(texts)

    for act, keywords in ACTIVITY_MAP.items():
        if any(k in blob for k in keywords):
            print(f"[KEYWORD] Detected {act} from text")
            return act

    return 0


def extract_points(root):
    points = []
    for trkpt in root.findall(".//{*}trkpt"):
        lat = trkpt.attrib.get("lat")
        lon = trkpt.attrib.get("lon")
        time_el = trkpt.find("{*}time")
        if lat and lon and time_el is not None:
            try:
                t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
                points.append((float(lat), float(lon), t))
            except:
                pass
    return points


def test_data(root):
    """
    Test 3:
    Heurystyka na podstawie:
    - średniej prędkości
    - całkowitego dystansu
    - czasu trwania
    - presence depth (pływanie)
    """
    # Swim: depth / pool data
    for elem in root.iter():
        if "depth" in elem.tag.lower():
            print("[DATA] Detected Swim from depth data")
            return "swim"

    points = extract_points(root)
    if len(points) < 10:
        return 0

    total_dist = 0
    total_time = (points[-1][2] - points[0][2]).total_seconds()

    for i in range(1, len(points)):
        total_dist += haversine(
            points[i-1][0], points[i-1][1],
            points[i][0], points[i][1]
        )

    if total_time <= 0:
        return 0

    avg_speed_kmh = (total_dist / 1000) / (total_time / 3600)

    print(f"[DATA] avg_speed={avg_speed_kmh:.2f} km/h, dist={total_dist/1000:.2f} km")

    # Heurystyka
    if avg_speed_kmh > 12:
        return "ride"
    if 7 < avg_speed_kmh <= 12:
        return "run"
    if 4 < avg_speed_kmh <= 7:
        return "walk"
    if avg_speed_kmh <= 4 and total_dist > 5000:
        return "hike"

    # Workout: krótko, intensywnie, mały dystans
    if total_time < 1800 and total_dist < 2000:
        return "workout"

    return 0


def determine_gpx_activity(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()

    r1 = test_metadata(root)
    r2 = test_keywords(root)
    r3 = test_data(root)

    results = [r for r in [r1, r2, r3] if r != 0]

    if results:
        counter = Counter(results)
        best, votes = counter.most_common(1)[0]
        print(f"  test1={r1}, test2={r2}, test3={r3}")
        
        if votes >= 2:
            print(f"[FINAL] Majority vote: {best}")
            return normalize_activity(best)

    # Fallback: kolejno 1 → 2 → 3
    for r in [r1, r2, r3]:
        if r != 0:
            print(f"[FINAL] No majority decision, allback to {r}")
            return normalize_activity(r)

    print("[FINAL] Unable to determine activity")
    return "Other"

# ----------------- Upload single file -----------------
def upload_file(config):
    path = input("Path to file (gpx/tcx/fit): ").strip()
    
    if not os.path.exists(path):
        print("❌ File does not exist!")
        return
    
    # Check if already uploaded
    filesize = os.path.getsize(path)
    filenamewithext = os.path.basename(path)
    if is_already_uploaded(filenamewithext, filesize):
        print(f"⚠ File already uploaded: {idx}/{len(files)}: {os.path.basename(path)}")
        return
    else:
        print(f"\nUploading file {idx}/{len(files)}: {os.path.basename(path)}")
    
    # Get filename without extension for title
    filename = os.path.splitext(os.path.basename(path))[0]
    description = ""
    
    # Determine activity type from file
    ext = os.path.splitext(path)[1].lower()
    
    if ext == ".gpx":
        data_type = "gpx"
        activity_type = determine_gpx_activity(path)
    else:
        data_type = ext.lstrip(".")
        activity_type = "Workout"
    
    check_token(config)
    
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/api/v3/uploads",
                headers={"Authorization": f"Bearer {config['access_token']}"},
                files={"file": f},
                data={
                    "data_type": data_type,
                    "name": filename,
                    "description": description,
                    "activity_type": activity_type
                },
            )
        
        response = r.json()
        print("📤 Upload response:")
        print(response)
        
        if "id" in response or "activity_id" in response:
            log_uploaded_file(path)
            print("✅ File successfully uploaded and logged!")
        
    except Exception as e:
        print(f"❌ Error uploading {path}: {e}")

# ----------------- Check upload status -----------------
def check_upload_status(config):
    upload_id = input("Upload ID: ").strip()
    check_token(config)

    r = requests.get(
        f"{API_BASE}/api/v3/uploads/{upload_id}",
        headers={"Authorization": f"Bearer {config['access_token']}"},
    )

    print("📊 Upload status:")
    print(r.json())

# ----------------- Bulk folder upload -----------------
def upload_folder(config):
    folder_path = input("Path to folder containing GPX/TCX/FIT files: ").strip()
    if not os.path.exists(folder_path):
        print("❌ Folder does not exist!")
        return

    extensions = ("*.gpx", "*.tcx", "*.fit", "*.fit.gz", "*.tcx.gz", "*.gpx.gz")
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(folder_path, ext)))

    if not files:
        print("❌ No GPX/TCX/FIT files found in folder.")
        return

    print(f"📂 Found {len(files)} files. Starting upload with 6s delay...")

    for idx, path in enumerate(files, start=1):
        # Check if already uploaded
        filesize = os.path.getsize(path)
        filenamewithext = os.path.basename(path)
        if is_already_uploaded(filenamewithext, filesize):
            print(f"⚠ File already uploaded: {idx}/{len(files)}: {os.path.basename(path)}")
            continue
        else:
            print(f"\nUploading file {idx}/{len(files)}: {os.path.basename(path)}")


        # Get filename without extension for title
        filename = os.path.splitext(os.path.basename(path))[0]
        description = ""
        
        # Determine activity type from file
        ext = os.path.splitext(path)[1].lower()
        
        if ext == ".gpx":
            data_type = "gpx"
            activity_type = determine_gpx_activity(path)
        else:
            data_type = ext.lstrip(".")
            activity_type = "Workout"
        
        check_token(config)

        ext = os.path.splitext(path)[1].lower()
        if ext in [".gpx"]:
            data_type = "gpx"
        elif ext in [".tcx"]:
            data_type = "tcx"
        elif ext in [".fit"]:
            data_type = "fit"
        elif ext.endswith(".gz"):
            if ".gpx" in ext:
                data_type = "gpx.gz"
            elif ".tcx" in ext:
                data_type = "tcx.gz"
            elif ".fit" in ext:
                data_type = "fit.gz"
        else:
            data_type = "gpx"

        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{API_BASE}/api/v3/uploads",
                    headers={"Authorization": f"Bearer {config['access_token']}"},
                    files={"file": f},
                    data={
                        "data_type": data_type,
                        "name": filename,
                        "description": description,
                        "activity_type": activity_type
                    },
                )
            response = r.json()
            print("📤 Upload response:")
            print(response)
            
            if "id" in response or "activity_id" in response:
                log_uploaded_file(path)
                print("✅ File successfully uploaded and logged!")
                
            print("\n")
            
        except Exception as e:
            print(f"❌ Error uploading {path}: {e}")






        print("⏳ Waiting 6 seconds...")
        time.sleep(6)

    print("\n✅ All files processed.")

# ----------------- Main menu -----------------
def menu(config):
    while True:
        print("\n=== STRAVA CLI ===")
        print("1) Upload a single file")
        print("2) Check upload status")
        print("3) Refresh token")
        print("4) Upload an entire folder")
        print("5) Quit")

        choice = input("> ").strip()

        if choice == "1":
            upload_file(config)
        elif choice == "2":
            check_upload_status(config)
        elif choice == "3":
            refresh_token(config)
        elif choice == "4":
            upload_folder(config)
        elif choice == "5":
            break
        else:
            print("❌ Invalid choice")

# ----------------- Main -----------------
def main():
    config = load_config()
    required_keys = [
        "client_id",
        "client_secret",
        "access_token",
        "refresh_token",
        "expires_at",
    ]
    if not all(k in config and config[k] for k in required_keys):
        config = tutorial_setup()

    menu(config)

if __name__ == "__main__":
    main()
