import logging
import time
from fastapi import APIRouter, Request, HTTPException
from services.github_service import GitHubService
from crew.crew import SageCrew

logger = logging.getLogger(__name__)

router = APIRouter()

github = GitHubService()


def extract_repo(data: dict) -> str | None:
    """Extract repository full name from various GitHub event payload formats."""
    repo_obj = data.get("repository")
    if isinstance(repo_obj, dict):
        return repo_obj.get("full_name")
    if isinstance(repo_obj, str):
        return repo_obj
    return data.get("repo")


def extract_build_id(data: dict) -> str | None:
    """Extract a build/run identifier from various GitHub event payload formats."""
    for key in ("workflow_run", "check_suite", "check_run", "workflow_job"):
        obj = data.get(key)
        if isinstance(obj, dict) and obj.get("id"):
            return str(obj["id"])
    if data.get("build_id"):
        return str(data["build_id"])
    # For push events, use the short commit SHA
    if data.get("after"):
        return data["after"][:7]
    # For other events, use top-level id if present
    if data.get("id"):
        return str(data["id"])
    return None


def extract_conclusion(data: dict) -> str:
    """Extract the conclusion/status from the event payload."""
    for key in ("workflow_run", "check_suite", "check_run", "workflow_job"):
        obj = data.get(key)
        if isinstance(obj, dict):
            conclusion = obj.get("conclusion") or obj.get("status")
            if conclusion:
                return conclusion
    return data.get("conclusion", "failure")


def extract_logs(data: dict) -> str:
    """Extract useful log/context info from the event payload."""
    if data.get("logs"):
        return str(data["logs"])
    for key in ("workflow_run", "check_run", "check_suite"):
        obj = data.get(key)
        if isinstance(obj, dict):
            parts = []
            for field in ("name", "display_title", "head_branch", "html_url"):
                if obj.get(field):
                    parts.append(f"{field}: {obj[field]}")
            if obj.get("output", {}).get("summary"):
                parts.append(f"summary: {obj['output']['summary']}")
            if parts:
                return "\n".join(parts)
    return "No logs provided"


@router.post("/webhook")
async def handle_webhook(request: Request):

    data = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    logger.info(f"Received GitHub event: {event_type}, action: {data.get('action')}")
    logger.debug(f"Payload keys: {list(data.keys())}")

    repo = extract_repo(data)
    if not repo:
        logger.error(f"Could not extract repo. Payload keys: {list(data.keys())}")
        raise HTTPException(status_code=400, detail="Repository name could not be determined from payload")

    build_id = extract_build_id(data)
    if not build_id:
        # Fallback: generate a unique ID so we can still proceed
        build_id = str(int(time.time()))
        logger.warning(f"Could not extract build_id, using generated ID: {build_id}")

    conclusion = extract_conclusion(data)
    if conclusion == "success":
        return {"status": "skipped", "reason": "Build succeeded, no fix needed"}

    logs = extract_logs(data)

    # Run the crew with proper inputs
    inputs = {
        'log_output': logs,
        'fix_context': 'Based on the analysis and fix suggestions provided.',
    }

    try:
        result = SageCrew().crew().kickoff(inputs=inputs)
    except Exception as e:
        logger.error(f"Crew execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Crew execution failed: {str(e)}")

    branch = f"fix-{build_id}"

    try:
        github.create_branch(repo, branch)
    except Exception as e:
        logger.error(f"Failed to create branch '{branch}' in {repo}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create branch: {str(e)}")

    # Commit the fix suggestion to the branch so there's a diff for the PR
    fix_content = str(result)
    try:
        github.commit_file(
            repo=repo,
            branch=branch,
            file_path=f"fixes/fix-{build_id}.md",
            content=fix_content,
            message=f"Add fix suggestion for CI failure {build_id}"
        )
    except Exception as e:
        logger.error(f"Failed to commit fix file to '{branch}' in {repo}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to commit fix: {str(e)}")

    try:
        pr = github.create_pr(
            repo=repo,
            title=f"Fix CI Failure {build_id}",
            body=fix_content,
            branch=branch
        )
    except Exception as e:
        logger.error(f"Failed to create PR in {repo}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PR: {str(e)}")

    return {"status": "PR created", "pr_url": pr.get("html_url")}
