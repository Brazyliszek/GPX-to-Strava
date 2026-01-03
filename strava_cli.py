import os
import time
import requests
import glob

CONFIG_FILE = "strava_config.txt"
API_BASE = "https://www.strava.com"

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

    # --- Robust extraction of the code from URL if user pasted full URL ---
    if "code=" in code:
        code = code.split("code=")[1]
    code = code.split("&")[0]
    # -----------------------------------------------------------------------

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

    # --- Handle errors gracefully ---
    if "access_token" not in data:
        print("\n❌ OAuth error received from Strava:")
        print(data)
        print("\n👉 Make sure you pasted the correct OAuth code, not the access token.")
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

# ----------------- Upload single file -----------------
def upload_file(config):
    path = input("Path to file (gpx/tcx/fit): ").strip()
    data_type = input("Data type (gpx, tcx, fit, etc.): ").strip()

    check_token(config)

    with open(path, "rb") as f:
        r = requests.post(
            f"{API_BASE}/api/v3/uploads",
            headers={"Authorization": f"Bearer {config['access_token']}"},
            files={"file": f},
            data={"data_type": data_type},
        )

    print("📤 Upload response:")
    print(r.json())

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

    # Find all GPX/TCX/FIT files
    extensions = ("*.gpx", "*.tcx", "*.fit", "*.fit.gz", "*.tcx.gz", "*.gpx.gz")
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(folder_path, ext)))

    if not files:
        print("❌ No GPX/TCX/FIT files found in folder.")
        return

    print(f"📂 Found {len(files)} files. Starting upload with 6s delay to respect rate limits...")

    for idx, file_path in enumerate(files, start=1):
        print(f"\nUploading file {idx}/{len(files)}: {os.path.basename(file_path)}")
        check_token(config)

        # Detect data type from extension
        ext = os.path.splitext(file_path)[1].lower()
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
            data_type = "gpx"  # fallback

        try:
            with open(file_path, "rb") as f:
                r = requests.post(
                    f"{API_BASE}/api/v3/uploads",
                    headers={"Authorization": f"Bearer {config['access_token']}"},
                    files={"file": f},
                    data={"data_type": data_type},
                )
            resp = r.json()
            print(resp)
        except Exception as e:
            print(f"❌ Error uploading {file_path}: {e}")

        print("⏳ Waiting 6 seconds to avoid hitting Strava rate limits...")
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
