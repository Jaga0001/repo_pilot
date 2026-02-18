import requests
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")


class GitHubService:

    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }

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

    def create_pr(self, repo, title, body, branch):
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
            "base": "main"
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

        import base64
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
