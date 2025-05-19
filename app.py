from flask import Flask, request, jsonify
import logging
import json
import requests
from google.cloud import storage
import os

# --- APP CONFIG ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- CONSTANTS / ENV CONFIG ---
GITHUB_TOKEN = "" # Securely injected in Cloud Run
GITHUB_OWNER = "chandra075"
REPOSITORIES = ["DS-Pojects", "exp_28_Mar_2025"]  # <-- Fixed typo
GCS_BUCKET_NAME = "crun"

# --- GCS CLIENT ---
storage_client = storage.Client()

# --- HELPERS ---

def get_github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

def save_to_gcs(payload: dict, filename: str) -> None:
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(json.dumps(payload, indent=2))
        logging.info(f"✅ Saved to GCS: {filename}")
    except Exception as e:
        logging.error(f"❌ Error saving to GCS ({filename}): {e}", exc_info=True)

def fetch_pr_details(pr_number: int, repo: str) -> dict | None:
    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}"
    try:
        pr_resp = requests.get(f"{base_url}/pulls/{pr_number}", headers=get_github_headers())
        pr_resp.raise_for_status()

        comments_resp = requests.get(f"{base_url}/issues/{pr_number}/comments", headers=get_github_headers())
        comments_resp.raise_for_status()

        return {
            "pull_request": pr_resp.json(),
            "comments": comments_resp.json(),
        }

    except requests.RequestException as e:
        logging.error(f"❌ Failed to fetch PR #{pr_number} from {repo}: {e}", exc_info=True)
        return None

def process_prs_for_repo(repo: str) -> int:
    saved_count = 0
    page = 1
    logging.info(f"📦 Processing historical PRs for repo: {repo}")

    while True:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}/pulls?state=all&per_page=100&page={page}"
        try:
            response = requests.get(url, headers=get_github_headers())
            response.raise_for_status()
            prs = response.json()

            if not prs:
                break

            for pr in prs:
                pr_number = pr.get("number")
                if not pr_number:
                    continue

                pr_info = fetch_pr_details(pr_number, repo)
                if pr_info:
                    filename = f"{repo}_pull_request_{pr_number}.json"
                    save_to_gcs(pr_info, filename)
                    saved_count += 1

            page += 1

        except requests.RequestException as e:
            logging.error(f"❌ Error fetching PRs for {repo}, page {page}: {e}", exc_info=True)
            break

    return saved_count

# --- ROUTES ---

@app.route("/", methods=["POST"])
def github_webhook():
    try:
        payload = request.get_json()
        event_type = request.headers.get("X-GitHub-Event")

        if event_type == "pull_request":
            pr = payload.get("pull_request", {})
            repo = payload.get("repository", {}).get("name")

            pr_number = pr.get("number")
            if not pr_number or not repo:
                return jsonify({"error": "Missing PR number or repository"}), 400

            pr_info = fetch_pr_details(pr_number, repo)
            if not pr_info:
                return jsonify({"error": "Failed to fetch PR details"}), 500

            filename = f"{repo}_pull_request_{pr_number}.json"
            save_to_gcs(pr_info, filename)

        return jsonify({"message": "Webhook received"}), 200

    except Exception as e:
        logging.error(f"❌ Webhook processing error: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/fetch_all_prs", methods=["POST"])
def fetch_all_prs():
    try:
        total_saved = sum(process_prs_for_repo(repo) for repo in REPOSITORIES)
        return jsonify({"message": f"✅ Fetched and saved {total_saved} PRs from all repositories"}), 200

    except Exception as e:
        logging.error(f"❌ Error during bulk PR fetch: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch historical PRs"}), 500

# --- ENTRY POINT ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
