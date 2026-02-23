import json
import logging
import re
import hmac
import hashlib
import os
import requests
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from services.github_service import GitHubService
from services.es_service import ElasticsearchService
from crew.crew import SageCrew
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

es = ElasticsearchService()


# ── Webhook signature verification ───────────────────────────────────

def verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping verification")
        return True
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Helpers ──────────────────────────────────────────────────────────

def parse_file_changes(crew_output: str) -> list[dict]:
    """
    Extract file changes from the crew output.

    Expected format produced by the code_fixer agent::

        ===FILE: path/to/file.py===
        <complete fixed file content>
        ===END FILE===
    """
    changes: list[dict] = []

    for match in re.finditer(
        r"===FILE:\s*(.+?)\s*===\n(.*?)===END FILE===", crew_output, re.DOTALL
    ):
        path, content = match.group(1).strip(), match.group(2).strip()
        if path and content:
            changes.append({"path": path, "content": content})

    if changes:
        return changes

    # Fallback: JSON  {"files": [{"path": …, "content": …}, …]}
    try:
        parsed = json.loads(crew_output)
        if isinstance(parsed, dict) and "files" in parsed:
            for f in parsed["files"]:
                changes.append({"path": f["path"], "content": f["content"]})
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return changes


# ── Background pipeline ─────────────────────────────────────────────

def process_push(repo: str, head_sha: str, branch: str, pusher: str):
    """
    Called in the background after every push.
    Now creates a per-repo GitHubService (with installation token).
    """
    logger.info(
        "Processing push — repo=%s  sha=%s  branch=%s  pusher=%s",
        repo, head_sha[:7], branch, pusher,
    )

    # Create a GitHubService scoped to this repo's installation
    github = GitHubService.for_repo(repo)

    # Step 1 — Wait for CI to complete
    ci_result = github.wait_for_ci(repo, head_sha)
    status = ci_result["status"]

    if status == "success":
        logger.info("CI passed for %s — nothing to do", head_sha[:7])
        # Close any stale fix PRs since CI is now green
        _close_stale_fix_prs(repo, branch, reason="CI is now passing")
        return

    if status == "no_ci":
        logger.info("No CI workflows configured for %s — skipping", repo)
        return

    if status == "timeout":
        logger.warning("CI did not finish in time for %s — skipping", head_sha[:7])
        return

    # status == "failure" — at least one run failed
    failed_runs = ci_result["failed_runs"]
    logger.info("CI FAILED for %s — %d failed run(s)", head_sha[:7], len(failed_runs))

    # ── Guard: skip if there is already an open fix PR for this branch ──
    default_branch = github.get_default_branch(repo)
    open_fix_prs = github.get_open_fix_prs(repo, base=default_branch)

    if open_fix_prs:
        pr_urls = [pr.get("html_url", "") for pr in open_fix_prs]
        logger.info(
            "Skipping fix — %d open fix PR(s) already exist: %s",
            len(open_fix_prs), ", ".join(pr_urls),
        )

        _comment_on_existing_pr(
            repo, open_fix_prs[0], head_sha, failed_runs
        )
        return

    # Step 2 — Collect failure logs from every failed run
    log_parts: list[str] = []
    for run in failed_runs:
        try:
            run_logs = github.get_workflow_run_logs(repo, run["id"])
            if run_logs:
                log_parts.append(
                    f"## Workflow: {run['name']}  (run {run['id']})\n"
                    f"URL: {run['html_url']}\n\n{run_logs}"
                )
        except Exception as exc:
            logger.warning("Could not fetch logs for run %s: %s", run["id"], exc)

    ci_logs = "\n\n".join(log_parts) if log_parts else "No detailed failure logs available"

    # Step 3 — Fetch repository source code for analysis
    repo_files = github.get_repo_tree(repo, ref=default_branch)
    file_tree = "\n".join(f["path"] for f in repo_files)

    source_parts: list[str] = []
    total_len = 0
    MAX_SOURCE_CHARS = 30_000

    for f in repo_files:
        if total_len >= MAX_SOURCE_CHARS:
            break
        if f.get("size", 0) > 50_000:
            continue
        content = github.get_file_content(repo, f["path"], ref=default_branch)
        if content:
            part = f"--- {f['path']} ---\n{content}"
            total_len += len(part)
            source_parts.append(part)

    source_code = "\n\n".join(source_parts)


    past_fixes_text = ""
    try:
        similar = es.search_similar_fixes(ci_logs, repo=repo, top_k=3)
        if similar:
            parts: list[str] = []
            for i, fix in enumerate(similar, 1):
                changed = ", ".join(
                    fc.get("path", "?") for fc in fix.get("file_changes", [])
                )
                parts.append(
                    f"### Past Fix #{i} (repo: {fix['repo']}, "
                    f"sha: {fix['head_sha'][:7]}, PR: {fix['pr_url']})\n"
                    f"**Analysis:** {fix['analysis'][:1500]}\n"
                    f"**Files changed:** {changed}"
                )
            past_fixes_text = "\n\n".join(parts)
            logger.info("Found %d similar past fix(es) in ES", len(similar))
    except Exception as exc:
        logger.warning("ES search for past fixes failed: %s", exc)


    inputs = {
        "ci_logs": ci_logs,
        "repo_name": repo,
        "file_tree": file_tree,
        "source_code": source_code,
        "past_fixes": past_fixes_text or "No similar past fixes found.",
    }

    try:
        result = SageCrew().crew().kickoff(inputs=inputs)
    except Exception as exc:
        logger.error("Crew execution failed for %s: %s", head_sha[:7], exc)
        return

    crew_output = str(result)


    file_changes = parse_file_changes(crew_output)
    first_run_id = failed_runs[0]["id"] if failed_runs else head_sha[:7]

    if not file_changes:
        logger.warning("Crew produced no structured file changes — creating advisory PR")
        file_changes = [
            {
                "path": f"fixes/ci-fix-{first_run_id}.md",
                "content": f"# CI Fix Suggestion (run {first_run_id})\n\n{crew_output}",
            }
        ]

    # Step 7 — Create branch, commit fixes, open PR
    fix_branch = f"fix/ci-{head_sha[:7]}"

    try:
        github.create_branch(repo, fix_branch, base=default_branch)
    except Exception as exc:
        logger.error("Failed to create branch '%s': %s", fix_branch, exc)
        return

    committed: list[str] = []
    for change in file_changes:
        try:
            github.commit_file(
                repo=repo,
                branch=fix_branch,
                file_path=change["path"],
                content=change["content"],
                message=f"fix: {change['path']} — auto-fix for CI failure ({head_sha[:7]})",
            )
            committed.append(change["path"])
        except Exception as exc:
            logger.error("Failed to commit %s: %s", change["path"], exc)

    if not committed:
        logger.error("Could not commit any files for %s", head_sha[:7])
        return

    # Build PR body
    file_list_md = "\n".join(f"- `{p}`" for p in committed)
    run_links = "\n".join(
        f"| {r['name']} | [{r['id']}]({r['html_url']}) | `{r['conclusion']}` |"
        for r in failed_runs
    )
    pr_body = (
        f"## Automated CI/CD Fix\n\n"
        f"**Triggered by push:** `{head_sha[:7]}` on `{branch}` by @{pusher}\n\n"
        f"### Failed Runs\n"
        f"| Workflow | Run | Conclusion |\n|---|---|---|\n{run_links}\n\n"
        f"### Files Changed\n{file_list_md}\n\n"
        f"### Analysis & Fix\n{crew_output[:4000]}"
    )

    try:
        pr = github.create_pr(
            repo=repo,
            title=f"fix: auto-fix CI failure for {head_sha[:7]}",
            body=pr_body,
            branch=fix_branch,
            base=default_branch,
        )
        logger.info("PR created: %s", pr.get("html_url"))
    except Exception as exc:
        logger.error("Failed to create PR: %s", exc)
        return

    # Step 8 — Store the fix in Elasticsearch for future reference
    try:
        es.store_fix(
            repo=repo,
            branch=branch,
            head_sha=head_sha,
            ci_logs=ci_logs,
            analysis=crew_output,
            file_changes=file_changes,
            pr_url=pr.get("html_url", ""),
        )
    except Exception as exc:
        logger.warning("Failed to store fix in ES: %s", exc)


# ── Helper: close stale fix PRs when CI goes green ──────────────────

def _close_stale_fix_prs(repo: str, branch: str, reason: str):
    """Close all open fix/ci-* PRs and leave a comment explaining why."""
    github = GitHubService.for_repo(repo)
    default_branch = github.get_default_branch(repo)
    open_prs = github.get_open_fix_prs(repo, base=default_branch)
    for pr in open_prs:
        pr_number = pr["number"]
        try:
            # Add comment
            comment_url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
            requests.post(
                comment_url,
                json={"body": f"🤖 **Sage Agent:** Closing this PR — {reason}."},
                headers=github.headers,
            )
            # Close PR
            patch_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
            requests.patch(
                patch_url,
                json={"state": "closed"},
                headers=github.headers,
            )
            logger.info("Closed stale fix PR #%d (%s)", pr_number, reason)
        except Exception as exc:
            logger.warning("Failed to close PR #%d: %s", pr_number, exc)




def _comment_on_existing_pr(repo: str, pr: dict, head_sha: str, failed_runs: list[dict]):
    
    github = GitHubService.for_repo(repo)
    pr_number = pr["number"]
    run_summary = ", ".join(
        f"[{r['name']}]({r['html_url']})" for r in failed_runs
    )
    body = (
        f"🤖 **Sage Agent:** CI failed again on commit `{head_sha[:7]}`.\n\n"
        f"**Failed runs:** {run_summary}\n\n"
        f"A fix PR already exists here — please review and merge it, "
        f"or close it so the agent can create a new one."
    )
    try:
        url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
        requests.post(url, json={"body": body}, headers=github.headers)
        logger.info("Commented on existing fix PR #%d about new failure", pr_number)
    except Exception as exc:
        logger.warning("Failed to comment on PR #%d: %s", pr_number, exc)


# ── Webhook endpoint ────────────────────────────────────────────────

@router.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives GitHub webhook events with signature verification."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not verify_signature(body, signature):
        raise HTTPException(status_code= 401, detail="Invalid webhook signature")

    data = json.loads(body)
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    logger.info("Received GitHub event: %s", event_type)

    # ── Gate: only act on push events ────────────────────────────────
    if event_type != "push":
        return {
            "status": "skipped",
            "reason": f"Event '{event_type}' is not a push — ignoring",
        }

    # Extract push details
    repo_obj = data.get("repository", {})
    repo = repo_obj.get("full_name", "")
    head_sha = data.get("after", "")
    ref = data.get("ref", "")                     # e.g. "refs/heads/main"
    branch = ref.removeprefix("refs/heads/")
    pusher = data.get("pusher", {}).get("name", "unknown")

    if not repo or not head_sha:
        raise HTTPException(
            status_code=400,
            detail="Could not extract repository or commit SHA from push payload",
        )

    # Ignore branch deletions (head_sha is all zeros)
    if head_sha == "0" * 40:
        return {"status": "skipped", "reason": "Branch deletion — ignoring"}

    # Schedule the pipeline in the background
    background_tasks.add_task(process_push, repo, head_sha, branch, pusher)

    return {
        "status": "accepted",
        "message": f"Push on {repo} ({head_sha[:7]}) received — CI will be monitored",
    }
