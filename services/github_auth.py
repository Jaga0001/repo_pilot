import os
import time
import logging
import jwt
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH", "./sage-app.pem")

logger = logging.getLogger(__name__)


def _load_private_key() -> str:
    with open(GITHUB_PRIVATE_KEY_PATH, "r") as f:
        return f.read()


def generate_jwt() -> str:
    """Generate a short-lived JWT to authenticate as the GitHub App."""
    now = int(time.time())
    payload = {
        "iat": now - 60,          # issued at (60s clock drift allowance)
        "exp": now + (10 * 60),   # expires in 10 minutes
        "iss": GITHUB_APP_ID,
    }
    private_key = _load_private_key()
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """
    Exchange the App JWT for a short-lived installation access token.
    This token is scoped to the repos where the App is installed.
    """
    app_jwt = generate_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
    )
    if resp.status_code != 201:
        raise Exception(f"Failed to get installation token: {resp.status_code} {resp.text}")
    return resp.json()["token"]


def get_installation_id_for_repo(repo: str) -> int:
    """Find the installation ID for a given owner/repo."""
    app_jwt = generate_jwt()
    owner = repo.split("/")[0]

    # Try org installation first, then user
    for endpoint in [
        f"https://api.github.com/orgs/{owner}/installation",
        f"https://api.github.com/users/{owner}/installation",
    ]:
        resp = requests.get(
            endpoint,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        if resp.status_code == 200:
            return resp.json()["id"]

    raise Exception(f"No GitHub App installation found for {repo}")