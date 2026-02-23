import requests
import os
import base64
import logging
import time
from dotenv import load_dotenv
from services.github_auth import get_installation_token, get_installation_id_for_repo

load_dotenv()

# Legacy PAT fallback (optional, for local dev)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")

logger = logging.getLogger(__name__)

# File extensions considered as source code when scanning repos
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".php", ".c", ".cpp", ".h", ".cs", ".yaml", ".yml", ".json", ".toml",
    ".cfg", ".ini", ".sh", ".bash", ".dockerfile", ".xml", ".gradle",
    ".kt", ".swift", ".scala", ".r", ".sql", ".html", ".css", ".scss",
}

# How long we wait for CI to finish (seconds)
CI_POLL_TIMEOUT = int(os.getenv("CI_POLL_TIMEOUT", "600"))  # 10 min default
CI_POLL_INTERVAL = int(os.getenv("CI_POLL_INTERVAL", "20"))  # 20 sec default


class GitHubService:

    def __init__(self, installation_id: int | None = None):
        """
        If running as a GitHub App, pass the installation_id to get
        a scoped token. Falls back to GITHUB_TOKEN (PAT) for local dev.
        """
        if GITHUB_APP_ID and installation_id:
            token = get_installation_token(installation_id)
        elif GITHUB_TOKEN:
            token = GITHUB_TOKEN
        else:
            raise RuntimeError("No GitHub authentication configured")

        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    @classmethod
    def for_repo(cls, repo: str) -> "GitHubService":
        """Create a GitHubService authenticated for the given repo's installation."""
        if GITHUB_APP_ID:
            installation_id = get_installation_id_for_repo(repo)
            return cls(installation_id=installation_id)
        return cls()

    # ── Repo metadata ────────────────────────────────────────────────

    def get_default_branch(self, repo: str) -> str:
        """Return the default branch name (e.g. 'main') for *repo*."""
        url = f"https://api.github.com/repos/{repo}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            return "main"
        return resp.json().get("default_branch", "main")

    # ── CI / CD polling & logs ─────────────────────────────────────

    def get_workflow_runs_for_commit(self, repo: str, head_sha: str) -> list[dict]:
        """Return all workflow runs triggered for a specific commit SHA."""
        url = f"https://api.github.com/repos/{repo}/actions/runs"
        resp = requests.get(
            url, params={"head_sha": head_sha, "per_page": 20}, headers=self.headers
        )
        if resp.status_code != 200:
            logger.warning("Failed to list workflow runs for %s: %s", head_sha, resp.status_code)
            return []
        return resp.json().get("workflow_runs", [])

    def get_check_suites_for_commit(self, repo: str, head_sha: str) -> list[dict]:
        """Return all check suites for a specific commit SHA."""
        url = f"https://api.github.com/repos/{repo}/commits/{head_sha}/check-suites"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            logger.warning("Failed to list check suites for %s: %s", head_sha, resp.status_code)
            return []
        return resp.json().get("check_suites", [])

    def wait_for_ci(
        self, repo: str, head_sha: str,
        timeout: int | None = None, interval: int | None = None,
    ) -> dict:
        """
        Poll GitHub until every CI workflow run for *head_sha* has completed.

        Returns a dict::

            {
                "status": "failure" | "success" | "timeout" | "no_ci",
                "failed_runs": [ {id, name, html_url, conclusion}, … ],
                "all_runs":    [ … ],
            }
        """
        timeout = timeout or CI_POLL_TIMEOUT
        interval = interval or CI_POLL_INTERVAL
        deadline = time.time() + timeout

        logger.info("Waiting for CI on %s commit %s (timeout %ss)", repo, head_sha[:7], timeout)

        # Give GitHub a moment to register the workflow runs
        time.sleep(min(10, interval))

        while True:
            runs = self.get_workflow_runs_for_commit(repo, head_sha)

            if not runs:
                # No workflow runs found yet — could still be spinning up
                if time.time() >= deadline:
                    logger.info("No CI workflow runs found for %s within timeout", head_sha[:7])
                    return {"status": "no_ci", "failed_runs": [], "all_runs": []}
                time.sleep(interval)
                continue

            all_completed = all(r.get("status") == "completed" for r in runs)

            if all_completed:
                failed = [
                    {
                        "id": r["id"],
                        "name": r.get("name", ""),
                        "html_url": r.get("html_url", ""),
                        "conclusion": r.get("conclusion", ""),
                    }
                    for r in runs
                    if r.get("conclusion") in ("failure", "timed_out", "cancelled")
                ]
                status = "failure" if failed else "success"
                logger.info(
                    "CI completed for %s — %s (%d runs, %d failed)",
                    head_sha[:7], status, len(runs), len(failed),
                )
                return {"status": status, "failed_runs": failed, "all_runs": runs}

            if time.time() >= deadline:
                logger.warning("CI poll timeout for %s", head_sha[:7])
                return {"status": "timeout", "failed_runs": [], "all_runs": runs}

            pending = sum(1 for r in runs if r.get("status") != "completed")
            logger.debug("CI not done — %d pending runs, retrying in %ds", pending, interval)
            time.sleep(interval)

    def get_workflow_run_logs(self, repo: str, run_id: int) -> str:
        """Fetch the plain-text logs of every *failed* job in a workflow run."""
        jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
        resp = requests.get(jobs_url, headers=self.headers)

        if resp.status_code != 200:
            logger.warning("Failed to fetch jobs for run %s: %s", run_id, resp.status_code)
            return ""

        jobs = resp.json().get("jobs", [])
        all_logs: list[str] = []

        for job in jobs:
            if job.get("conclusion") != "failure":
                continue
            job_id = job["id"]
            job_name = job.get("name", "unknown")
            log_url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
            log_resp = requests.get(log_url, headers=self.headers)

            if log_resp.status_code == 200:
                text = log_resp.text
                # Keep the tail — errors are almost always at the end
                if len(text) > 6000:
                    text = "...(truncated)...\n" + text[-6000:]
                all_logs.append(f"=== Job: {job_name} (id {job_id}) ===\n{text}")
            else:
                logger.warning("Failed to fetch logs for job %s: %s", job_id, log_resp.status_code)

        return "\n\n".join(all_logs) if all_logs else "No failure logs available"

    # ── Repository source code ───────────────────────────────────────

    def get_repo_tree(self, repo: str, ref: str = "main") -> list[dict]:
        """Return a list of ``{path, size}`` dicts for every code file in the repo."""
        url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
        resp = requests.get(url, headers=self.headers)

        if resp.status_code != 200:
            logger.warning("Failed to fetch repo tree: %s", resp.status_code)
            return []

        files: list[dict] = []
        for item in resp.json().get("tree", []):
            if item["type"] != "blob":
                continue
            ext = os.path.splitext(item["path"])[1].lower()
            if ext in CODE_EXTENSIONS:
                files.append({"path": item["path"], "size": item.get("size", 0)})
        return files

    def get_file_content(self, repo: str, path: str, ref: str = "main") -> str | None:
        """Return the UTF-8 content of a single file, or *None* on failure."""
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        resp = requests.get(url, params={"ref": ref}, headers=self.headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content")

    # ── Branch / commit / PR operations ──────────────────────────────

    def create_branch(self, repo, branch_name, base="main"):
        if not repo:
            raise ValueError("Repository name is required")
        
        url = f"https://api.github.com/repos/{repo}/git/refs/heads/{base}"

        response = requests.get(url, headers=self.headers)
        
        # Check if request was successful
        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"Failed to get base branch '{base}': {error_msg} (Status: {response.status_code})")
        
        data = response.json()
        if "object" not in data:
            raise Exception(f"Invalid response from GitHub API: {data}")
            
        sha = data["object"]["sha"]

        create_url = f"https://api.github.com/repos/{repo}/git/refs"

        data = {
            "ref": f"refs/heads/{branch_name}",
            "sha": sha
        }

        create_response = requests.post(create_url, json=data, headers=self.headers)
        
        if create_response.status_code == 422:
            # Branch already exists — not an error
            return {"status": "already_exists", "ref": f"refs/heads/{branch_name}"}
        
        if create_response.status_code not in [200, 201]:
            error_msg = create_response.json().get("message", "Unknown error")
            raise Exception(f"Failed to create branch '{branch_name}': {error_msg}")
        
        return create_response.json()

    def create_pr(self, repo, title, body, branch, base="main"):
        if not repo:
            raise ValueError("Repository name is required")

        # Check if a PR already exists for this branch
        existing_url = f"https://api.github.com/repos/{repo}/pulls"
        existing_response = requests.get(
            existing_url,
            params={"head": f"{repo.split('/')[0]}:{branch}", "state": "open"},
            headers=self.headers
        )
        if existing_response.status_code == 200:
            existing_prs = existing_response.json()
            if existing_prs:
                return existing_prs[0]  # Return the already-existing PR

        url = f"https://api.github.com/repos/{repo}/pulls"

        data = {
            "title": title,
            "body": body,
            "head": branch,
            "base": base,
        }

        response = requests.post(
            url,
            json=data,
            headers=self.headers
        )
        
        if response.status_code == 422:
            # Validation failed — likely PR already exists or no diff
            errors = response.json().get("errors", [])
            error_msg = response.json().get("message", "Validation Failed")
            raise Exception(f"Failed to create PR: {error_msg} — {errors}")

        if response.status_code not in [200, 201]:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"Failed to create PR: {error_msg}")

        return response.json()

    def commit_file(self, repo, branch, file_path, content, message):
        """Create or update a file on the given branch via the GitHub Contents API."""
        url = f"https://api.github.com/repos/{repo}/contents/{file_path}"

        # Check if file already exists to get its SHA (needed for update)
        existing = requests.get(url, params={"ref": branch}, headers=self.headers)
        file_sha = None
        if existing.status_code == 200:
            file_sha = existing.json().get("sha")

        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        data = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if file_sha:
            data["sha"] = file_sha

        response = requests.put(url, json=data, headers=self.headers)

        if response.status_code not in [200, 201]:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"Failed to commit file '{file_path}': {error_msg}")

        return response.json()

    def get_open_fix_prs(self, repo: str, base: str = "main") -> list[dict]:
        """Return all open PRs whose branch starts with 'fix/ci-'."""
        url = f"https://api.github.com/repos/{repo}/pulls"
        resp = requests.get(
            url,
            params={"state": "open", "base": base, "per_page": 50},
            headers=self.headers,
        )
        if resp.status_code != 200:
            return []
        return [
            pr for pr in resp.json()
            if pr.get("head", {}).get("ref", "").startswith("fix/ci-")
        ]
