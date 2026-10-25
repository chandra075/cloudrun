
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import logging
import json
import requests
from google.cloud import storage
import os

load_dotenv()

# --- APP CONFIG ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- CONSTANTS / ENV CONFIG ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = "chandra075"

REPOSITORIES = [
    "DS-Pojects",
    "exp_28_Mar_2025",
    "airflow_dags",
    "cloudrun",
    "proj",
]

# Existing GCP pipeline destination
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "crun")

# New Databricks destination
DATABRICKS_BUCKET_NAME = os.getenv(
    "DATABRICKS_BUCKET_NAME", "crundbr"
)

# Avoid attempting the same bucket twice if names are identical
DESTINATION_BUCKETS = list(dict.fromkeys([
    GCS_BUCKET_NAME,
    DATABRICKS_BUCKET_NAME,
]))

# --- GCS CLIENT ---
storage_client = storage.Client()


# --- HELPERS ---

def get_github_headers():
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured")

    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def save_to_gcs(payload: dict, filename: str) -> None:
    """Write the same PR JSON to every configured GCS bucket.

    Attempt all destinations, even if one upload fails.
    Raise an error if any destination fails.
    """
    errors = []
    json_payload = json.dumps(payload, indent=2)

    for bucket_name in DESTINATION_BUCKETS:
        try:
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(filename)

            blob.upload_from_string(
                json_payload,
                content_type="application/json",
                timeout=60,
            )

            logging.info(
                "Successfully saved gs://%s/%s",
                bucket_name,
                filename,
            )

        except Exception as exc:
            errors.append(bucket_name)

            # Do not log credentials or the PR payload.
            logging.error(
                "Upload failed for bucket=%s object=%s (%s)",
                bucket_name,
                filename,
                type(exc).__name__,
            )

    if errors:
        raise RuntimeError(
            "Dual-write incomplete; failed buckets: "
            + ", ".join(errors)
        )


def fetch_pr_details(pr_number: int, repo: str) -> dict | None:
    base_url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}"
    )

    try:
        pr_resp = requests.get(
            f"{base_url}/pulls/{pr_number}",
            headers=get_github_headers(),
            timeout=30,
        )
        pr_resp.raise_for_status()

        comments_resp = requests.get(
            f"{base_url}/issues/{pr_number}/comments",
            headers=get_github_headers(),
            timeout=30,
        )
        comments_resp.raise_for_status()

        return {
            "pull_request": pr_resp.json(),
            "comments": comments_resp.json(),
        }

    except requests.RequestException as e:
        logging.error(
            "Failed to fetch PR #%s from %s (%s)",
            pr_number,
            repo,
            type(e).__name__,
        )
        return None


def process_prs_for_repo(repo: str) -> int:
    saved_count = 0
    page = 1

    logging.info("Processing historical PRs for repo: %s", repo)

    while True:
        url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}"
            f"/pulls?state=all&per_page=100&page={page}"
        )

        try:
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=30,
            )
            response.raise_for_status()
            prs = response.json()

        except requests.RequestException as e:
            logging.error(
                "Failed to fetch PR list for repo=%s page=%s (%s)",
                repo,
                page,
                type(e).__name__,
            )
            raise RuntimeError(
                f"Could not fetch PR list for {repo}, page {page}"
            ) from e

        if not prs:
            break

        for pr in prs:
            pr_number = pr.get("number")
            if not pr_number:
                continue

            pr_info = fetch_pr_details(pr_number, repo)

            if not pr_info:
                raise RuntimeError(
                    f"Could not fetch PR #{pr_number} from {repo}"
                )

            filename = f"{repo}_pull_request_{pr_number}.json"

            # Raises if either bucket upload fails
            save_to_gcs(pr_info, filename)
            saved_count += 1

        page += 1

    return saved_count


# --- ROUTES ---

@app.route("/", methods=["POST"])
def github_webhook():
    try:
        payload = request.get_json(silent=True)

        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid JSON payload"}), 400

        event_type = request.headers.get("X-GitHub-Event")

        if event_type == "pull_request":
            pr = payload.get("pull_request", {})
            repo = payload.get("repository", {}).get("name")
            pr_number = pr.get("number")

            if not pr_number or not repo:
                return jsonify({
                    "error": "Missing PR number or repository"
                }), 400

            pr_info = fetch_pr_details(pr_number, repo)

            if not pr_info:
                return jsonify({
                    "error": "Failed to fetch PR details"
                }), 502

            filename = f"{repo}_pull_request_{pr_number}.json"
            save_to_gcs(pr_info, filename)

            return jsonify({
                "message": "Pull request saved to all destinations",
                "repository": repo,
                "pull_request": pr_number,
                "destinations": DESTINATION_BUCKETS,
            }), 200

        # Acknowledge other GitHub event types without processing them
        return jsonify({"message": "Webhook received"}), 200

    except Exception as e:
        logging.error(
            "Webhook processing failed (%s)",
            type(e).__name__,
        )
        return jsonify({
            "error": "Webhook processing failed; check service logs"
        }), 500


@app.route("/fetch_all_prs", methods=["POST"])
def fetch_all_prs():
    try:
        total_saved = 0

        for repo in REPOSITORIES:
            total_saved += process_prs_for_repo(repo)

        return jsonify({
            "message": "Historical PR fetch completed",
            "total_saved": total_saved,
            "destinations": DESTINATION_BUCKETS,
        }), 200

    except Exception as e:
        logging.error(
            "Historical PR fetch failed (%s)",
            type(e).__name__,
        )
        return jsonify({
            "error": "Historical PR fetch incomplete; check service logs"
        }), 500


# --- ENTRY POINT ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
