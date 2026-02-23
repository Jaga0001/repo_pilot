import os
import logging
from datetime import datetime, timezone
from elasticsearch import Elasticsearch
from dotenv import load_dotenv

load_dotenv()

ELASTIC_URL = os.getenv("ELASTIC_URL", "")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY", "")
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "sage-ci-fixes")

logger = logging.getLogger(__name__)


class ElasticsearchService:
    """
    Stores and retrieves CI/CD fix records using Elasticsearch.

    Each document contains the error logs, the AI analysis, the code
    changes that fixed the problem, and the PR URL.  The ``error_text``
    field uses ``semantic_text`` so we can do semantic similarity search
    to find past fixes that match a new failure.
    """

    # ── Index mapping ────────────────────────────────────────────────
    INDEX_MAPPINGS = {
        "properties": {
            "error_text": {"type": "semantic_text"},   # CI logs + analysis
            "repo": {"type": "keyword"},
            "branch": {"type": "keyword"},
            "head_sha": {"type": "keyword"},
            "pr_url": {"type": "keyword"},
            "analysis": {"type": "text"},
            "file_changes": {"type": "object", "enabled": False},  # stored, not indexed
            "created_at": {"type": "date"},
        }
    }

    def __init__(self):
        if not ELASTIC_URL or not ELASTIC_API_KEY:
            logger.warning("Elasticsearch URL or API key not configured — ES features disabled")
            self.client = None
            return

        self.client = Elasticsearch(ELASTIC_URL, api_key=ELASTIC_API_KEY)
        self._ensure_index()

    # ── Index bootstrap ──────────────────────────────────────────────

    def _ensure_index(self):
        """Create the index with semantic mappings if it doesn't already exist."""
        if self.client is None:
            return
        try:
            if not self.client.indices.exists(index=ELASTIC_INDEX):
                self.client.indices.create(index=ELASTIC_INDEX)
                logger.info("Created Elasticsearch index '%s'", ELASTIC_INDEX)

            self.client.indices.put_mapping(
                index=ELASTIC_INDEX,
                body=self.INDEX_MAPPINGS,
            )
            logger.info("Elasticsearch mappings updated for '%s'", ELASTIC_INDEX)
        except Exception as exc:
            logger.error("Failed to bootstrap Elasticsearch index: %s", exc)

    # ── Store a fix ──────────────────────────────────────────────────

    def store_fix(
        self,
        *,
        repo: str,
        branch: str,
        head_sha: str,
        ci_logs: str,
        analysis: str,
        file_changes: list[dict],
        pr_url: str,
    ) -> bool:
        """
        Index a CI fix document.  Returns True on success.

        Parameters
        ----------
        repo : str          Owner/repo
        branch : str        Branch that was pushed
        head_sha : str      Commit SHA of the push
        ci_logs : str       Raw CI failure logs
        analysis : str      AI analysis / crew output
        file_changes : list Files that were changed  [{path, content}, …]
        pr_url : str        URL of the created PR
        """
        if self.client is None:
            logger.debug("ES client not configured — skipping store_fix")
            return False

        doc = {
            "error_text": ci_logs,
            "repo": repo,
            "branch": branch,
            "head_sha": head_sha,
            "analysis": analysis,
            "file_changes": file_changes,
            "pr_url": pr_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.client.index(
                index=ELASTIC_INDEX,
                document=doc,
                refresh="wait_for",
            )
            logger.info("Stored fix in ES for %s (%s)", repo, head_sha[:7])
            return True
        except Exception as exc:
            logger.error("Failed to store fix in ES: %s", exc)
            return False

    # ── Search for similar past fixes ────────────────────────────────

    def search_similar_fixes(self, error_text: str, repo: str | None = None, top_k: int = 3) -> list[dict]:
        """
        Semantic search for past fixes whose ``error_text`` is similar to
        the current failure logs.

        Returns up to *top_k* results, each a dict with keys:
        ``repo, branch, head_sha, analysis, file_changes, pr_url, score``.
        """
        if self.client is None:
            return []

        # Build retriever using semantic search on the error_text field
        retriever = {
            "standard": {
                "query": {
                    "semantic": {
                        "field": "error_text",
                        "query": error_text[:2000],   # trim to stay within limits
                    }
                }
            }
        }

        # Optionally boost results from the same repo
        filter_clauses = []
        if repo:
            filter_clauses.append({"term": {"repo": repo}})

        try:
            if filter_clauses:
                resp = self.client.search(
                    index=ELASTIC_INDEX,
                    retriever=retriever,
                    post_filter={"bool": {"should": filter_clauses}},
                    size=top_k,
                )
            else:
                resp = self.client.search(
                    index=ELASTIC_INDEX,
                    retriever=retriever,
                    size=top_k,
                )

            results: list[dict] = []
            for hit in resp["hits"]["hits"]:
                src = hit["_source"]
                results.append({
                    "repo": src.get("repo", ""),
                    "branch": src.get("branch", ""),
                    "head_sha": src.get("head_sha", ""),
                    "analysis": src.get("analysis", ""),
                    "file_changes": src.get("file_changes", []),
                    "pr_url": src.get("pr_url", ""),
                    "score": hit.get("_score", 0),
                })
            return results

        except Exception as exc:
            logger.warning("Elasticsearch search failed: %s", exc)
            return []
