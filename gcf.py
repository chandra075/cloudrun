import functions_framework
import logging
import json
import requests
from flask import jsonify
from google.cloud import storage

# GitHub and Google Cloud Storage Configuration
GITHUB_TOKEN = ""  # Replace with your GitHub repos token
GITHUB_OWNER = "chandra075"
GITHUB_REPO = "DS-Pojects"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
GCS_BUCKET_NAME = "gcf-75"

# Initialize Google Cloud Storage Client
storage_client = storage.Client()

def save_to_gcs(payload, filename):
    """Save JSON data to Google Cloud Storage."""
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(json.dumps(payload, indent=2))
        logging.info(f"✅ Saved data to GCS: {filename}")
    except Exception as e:
        logging.error(f"❌ Error saving to GCS: {e}", exc_info=True)

def fetch_pr_details(pr_number):
    """Fetch pull request details and comments from GitHub API."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        pr_url = f"{GITHUB_API_URL}/pulls/{pr_number}"
        pr_response = requests.get(pr_url, headers=headers)
        pr_response.raise_for_status()
        pr_data = pr_response.json()

        comments_url = f"{GITHUB_API_URL}/issues/{pr_number}/comments"
        comments_response = requests.get(comments_url, headers=headers)
        comments_response.raise_for_status()
        comments_data = comments_response.json()

        return {"pull_request": pr_data, "comments": comments_data}

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error fetching PR details: {e}", exc_info=True)
        return None

@functions_framework.http
def github_webhook(request):
    """Handle GitHub webhook events and fetch PR details."""
    try:
        if request.method != "POST":
            logging.warning("❌ Received non-POST request")
            return jsonify({"error": "Only POST requests are allowed"}), 405

        payload = request.get_json()
        event_type = request.headers.get("X-GitHub-Event")

        if not payload:
            return jsonify({"error": "Invalid payload"}), 400

        if event_type == "pull_request":
            pr_number = payload.get("pull_request", {}).get("number")

            if not pr_number:
                return jsonify({"error": "PR number not found"}), 400

            pr_info = fetch_pr_details(pr_number)
            if not pr_info:
                return jsonify({"error": "Failed to fetch PR details"}), 500

            filename = f"pull_request_{pr_number}.json"
            save_to_gcs(pr_info, filename)

        return jsonify({"message": "Webhook received"}), 200

    except Exception as e:
        logging.error(f"❌ Internal Server Error: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error"}), 500
