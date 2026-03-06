"""
Azure DevOps Git REST API Client for dbt YAML Management

This client wraps the ADO Git API to support reading, editing, committing,
and creating pull requests for dbt YAML files — designed for use in a
Streamlit app deployed as a Databricks App.

Authentication:
    - Pass a Personal Access Token (PAT) directly, or
    - In Databricks, pull from secrets:
        pat = dbutils.secrets.get(scope="ado", key="pat")
        client = ADOGitClient(org, project, repo, pat)

API Reference:
    https://learn.microsoft.com/en-us/rest/api/azure/devops/git/?view=azure-devops-rest-7.1
"""

import base64
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API_VERSION = "7.1"


class ADOGitClientError(Exception):
    """Base exception for ADO Git client errors."""
    pass


class ADOConflictError(ADOGitClientError):
    """Raised when a push fails due to a ref conflict (stale oldObjectId)."""
    pass


class ADONotFoundError(ADOGitClientError):
    """Raised when a requested resource is not found."""
    pass


class ADOGitClient:
    """
    Azure DevOps Git REST API client for dbt YAML file operations.

    Supports:
        - Listing YAML files in the repo
        - Reading file contents by path and branch
        - Creating feature branches
        - Committing file changes (add/edit/delete)
        - Creating pull requests

    Usage:
        client = ADOGitClient(
            org="my-org",
            project="my-project",
            repo="my-dbt-repo",
            pat="xxxxx"
        )

        # List all YAML files under /models
        files = client.list_yaml_files("/models")

        # Read a file
        content = client.get_file("/models/staging/stg_orders.yml")

        # Create a branch, push changes, open a PR
        client.create_branch("feature/update-orders-yml")
        client.push_changes(
            branch="feature/update-orders-yml",
            file_path="/models/staging/stg_orders.yml",
            content=updated_yaml_string,
            commit_message="Update stg_orders.yml via dbt YAML editor"
        )
        pr = client.create_pull_request(
            source_branch="feature/update-orders-yml",
            title="Update stg_orders.yml",
            description="Automated update from dbt YAML editor"
        )
    """

    def __init__(
        self,
        org: str,
        project: str,
        repo: str,
        pat: Optional[str] = None,
        token: Optional[str] = None,
        target_branch: str = "main",
    ):
        if not pat and not token:
            raise ValueError("Either 'pat' or 'token' must be provided.")

        self.org = org
        self.project = project
        self.repo = repo
        self.target_branch = target_branch
        self.base_url = (
            f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}"
        )
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.session.auth = ("", pat)
        self.session.headers.update(
            {"Content-Type": "application/json"}
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _params(self, **kwargs) -> dict:
        """Build query params with the API version included."""
        kwargs["api-version"] = API_VERSION
        return kwargs

    def _get(self, path: str, params: Optional[dict] = None) -> requests.Response:
        url = f"{self.base_url}/{path}"
        resp = self.session.get(url, params=params)
        self._raise_for_status(resp)
        return resp

    def _post(self, path: str, json: dict) -> requests.Response:
        url = f"{self.base_url}/{path}"
        resp = self.session.post(url, json=json, params=self._params())
        self._raise_for_status(resp)
        return resp

    def _json(self, resp: requests.Response) -> dict:
        """Parse JSON from response, wrapping decode errors."""
        try:
            return resp.json()
        except ValueError as e:
            raise ADOGitClientError(
                f"Invalid JSON response from {resp.url} "
                f"(status {resp.status_code}): {e}"
            )

    def _raise_for_status(self, resp: requests.Response):
        if resp.status_code == 404:
            raise ADONotFoundError(
                f"Resource not found: {resp.url}\n{resp.text}"
            )
        if resp.status_code == 409:
            raise ADOConflictError(
                f"Conflict (likely stale ref): {resp.url}\n{resp.text}"
            )
        if not resp.ok:
            raise ADOGitClientError(
                f"ADO API error {resp.status_code}: {resp.url}\n{resp.text}"
            )

    def _full_ref(self, branch: str) -> str:
        """Ensure branch name has full refs/heads/ prefix."""
        if branch.startswith("refs/heads/"):
            return branch
        return f"refs/heads/{branch}"

    # -------------------------------------------------------------------------
    # Refs / Branches
    # -------------------------------------------------------------------------

    def get_ref(self, branch: str) -> dict:
        """
        Get the ref object for a branch, including its current objectId (commit SHA).

        Returns:
            dict with keys: name, objectId, etc.
        """
        filter_val = branch.removeprefix("refs/heads/")
        params = self._params(filter=f"heads/{filter_val}")
        resp = self._get("refs", params=params)
        refs = self._json(resp).get("value", [])

        full_ref = self._full_ref(branch)
        for ref in refs:
            if ref["name"] == full_ref:
                return ref

        raise ADONotFoundError(f"Branch '{branch}' not found.")

    def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists."""
        try:
            self.get_ref(branch)
            return True
        except ADONotFoundError:
            return False

    def create_branch(
        self,
        new_branch: str,
        source_branch: Optional[str] = None,
    ) -> dict:
        """
        Create a new branch from a source branch.

        Args:
            new_branch:    Name of the new branch (e.g. "feature/update-yml")
            source_branch: Branch to fork from (defaults to self.target_branch)

        Returns:
            The created ref object.
        """
        source = source_branch or self.target_branch
        source_ref = self.get_ref(source)
        source_sha = source_ref["objectId"]

        payload = [
            {
                "name": self._full_ref(new_branch),
                "oldObjectId": "0" * 40,  # all zeros = create new ref
                "newObjectId": source_sha,
            }
        ]
        resp = self._post("refs", json=payload)
        created = self._json(resp).get("value", [])
        if created and created[0].get("success", True):
            logger.info(f"Created branch '{new_branch}' from '{source}' at {source_sha}")
            return created[0]

        raise ADOGitClientError(
            f"Failed to create branch '{new_branch}': {self._json(resp)}"
        )

    def delete_branch(self, branch: str) -> dict:
        """
        Delete a branch.

        Args:
            branch: Branch name to delete.
        """
        ref = self.get_ref(branch)
        payload = [
            {
                "name": self._full_ref(branch),
                "oldObjectId": ref["objectId"],
                "newObjectId": "0" * 40,  # all zeros = delete
            }
        ]
        resp = self._post("refs", json=payload)
        logger.info(f"Deleted branch '{branch}'")
        return self._json(resp)

    # -------------------------------------------------------------------------
    # Items / File Operations
    # -------------------------------------------------------------------------

    def list_items(
        self,
        folder_path: str = "/",
        branch: Optional[str] = None,
        recursion_level: str = "full",
    ) -> list[dict]:
        """
        List items (files and folders) under a path.

        Args:
            folder_path:     Path in the repo (e.g. "/models")
            branch:          Branch to query (defaults to self.target_branch)
            recursion_level: "full", "oneLevel", or "none"

        Returns:
            List of item dicts with path, isFolder, objectId, etc.
        """
        version = branch or self.target_branch
        params = self._params(
            scopePath=folder_path,
            recursionLevel=recursion_level,
            **{
                "versionDescriptor.version": version,
                "versionDescriptor.versionType": "branch",
            },
        )
        resp = self._get("items", params=params)
        return self._json(resp).get("value", [])

    def list_yaml_files(
        self,
        folder_path: str = "/models",
        branch: Optional[str] = None,
        extensions: tuple[str, ...] = (".yml", ".yaml"),
    ) -> list[str]:
        """
        List all YAML file paths under a directory.

        Args:
            folder_path: Root path to search (e.g. "/models")
            branch:      Branch to query
            extensions:  File extensions to include

        Returns:
            Sorted list of file paths.
        """
        items = self.list_items(folder_path, branch=branch, recursion_level="full")
        yaml_files = [
            item["path"]
            for item in items
            if not item.get("isFolder", False)
            and item["path"].lower().endswith(extensions)
        ]
        return sorted(yaml_files)

    def get_file(
        self,
        file_path: str,
        branch: Optional[str] = None,
    ) -> str:
        """
        Get the text content of a file.

        Args:
            file_path: Path in the repo (e.g. "/models/staging/stg_orders.yml")
            branch:    Branch to read from

        Returns:
            File content as a string.
        """
        version = branch or self.target_branch
        params = self._params(
            path=file_path,
            **{
                "versionDescriptor.version": version,
                "versionDescriptor.versionType": "branch",
            },
        )
        # Request raw text
        url = f"{self.base_url}/items"
        resp = self.session.get(
            url,
            params=params,
            headers={"Accept": "application/octet-stream"},
        )
        self._raise_for_status(resp)
        return resp.text

    def get_file_metadata(
        self,
        file_path: str,
        branch: Optional[str] = None,
    ) -> dict:
        """
        Get metadata for a file (objectId, commitId, etc.) without content.

        Returns:
            Item dict with objectId, commitId, path, etc.
        """
        version = branch or self.target_branch
        params = self._params(
            path=file_path,
            **{
                "versionDescriptor.version": version,
                "versionDescriptor.versionType": "branch",
            },
        )
        resp = self._get("items", params=params)
        return self._json(resp)

    # -------------------------------------------------------------------------
    # Pushes / Commits
    # -------------------------------------------------------------------------

    def push_changes(
        self,
        branch: str,
        file_path: str,
        content: str,
        commit_message: str,
        change_type: str = "edit",
    ) -> dict:
        """
        Push a single file change to a branch.

        Args:
            branch:         Target branch name
            file_path:      Path of the file to change
            content:        New file content as string
            commit_message: Commit message
            change_type:    "add", "edit", or "delete"

        Returns:
            Push response dict.

        Raises:
            ADOConflictError: If the branch was updated since we last read it
                              (stale oldObjectId). Caller should re-fetch and retry.
        """
        ref = self.get_ref(branch)
        old_object_id = ref["objectId"]

        change = {
            "changeType": change_type,
            "item": {"path": file_path},
        }
        if change_type != "delete":
            change["newContent"] = {
                "content": content,
                "contentType": "rawtext",
            }

        payload = {
            "refUpdates": [
                {
                    "name": self._full_ref(branch),
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": [change],
                }
            ],
        }
        resp = self._post("pushes", json=payload)
        logger.info(
            f"Pushed {change_type} to '{file_path}' on branch '{branch}'"
        )
        return self._json(resp)

    def push_multiple_changes(
        self,
        branch: str,
        changes: list[dict],
        commit_message: str,
    ) -> dict:
        """
        Push multiple file changes in a single commit.

        Args:
            branch:         Target branch name
            changes:        List of change dicts, each with:
                            - file_path (str)
                            - content (str, optional for deletes)
                            - change_type (str): "add", "edit", "delete"
            commit_message: Commit message

        Returns:
            Push response dict.
        """
        ref = self.get_ref(branch)
        old_object_id = ref["objectId"]

        api_changes = []
        for ch in changes:
            change = {
                "changeType": ch["change_type"],
                "item": {"path": ch["file_path"]},
            }
            if ch["change_type"] != "delete":
                change["newContent"] = {
                    "content": ch["content"],
                    "contentType": "rawtext",
                }
            api_changes.append(change)

        payload = {
            "refUpdates": [
                {
                    "name": self._full_ref(branch),
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": api_changes,
                }
            ],
        }
        resp = self._post("pushes", json=payload)
        logger.info(
            f"Pushed {len(api_changes)} changes to branch '{branch}'"
        )
        return self._json(resp)

    # -------------------------------------------------------------------------
    # Pull Requests
    # -------------------------------------------------------------------------

    def create_pull_request(
        self,
        source_branch: str,
        title: str,
        description: str = "",
        target_branch: Optional[str] = None,
        reviewers: Optional[list[str]] = None,
        auto_complete: bool = False,
        delete_source_branch: bool = True,
    ) -> dict:
        """
        Create a pull request.

        Args:
            source_branch:        Branch with changes
            title:                PR title
            description:          PR description (supports markdown)
            target_branch:        Merge target (defaults to self.target_branch)
            reviewers:            List of reviewer unique names or IDs
            auto_complete:        Set PR to auto-complete when policies pass
            delete_source_branch: Delete source branch on merge

        Returns:
            PR response dict with pullRequestId, url, etc.
        """
        target = target_branch or self.target_branch

        payload = {
            "sourceRefName": self._full_ref(source_branch),
            "targetRefName": self._full_ref(target),
            "title": title,
            "description": description,
        }

        if reviewers:
            payload["reviewers"] = [
                {"uniqueName": r} if isinstance(r, str) else r
                for r in reviewers
            ]

        if delete_source_branch:
            payload["completionOptions"] = {
                "deleteSourceBranch": True,
            }

        resp = self._post("pullrequests", json=payload)
        pr = self._json(resp)
        pr_id = pr.get("pullRequestId")
        logger.info(f"Created PR #{pr_id}: {title}")

        # Optionally set auto-complete (requires a second call with the creator as auto-complete setter)
        if auto_complete and pr.get("createdBy", {}).get("id"):
            self._set_auto_complete(pr_id, pr["createdBy"]["id"])

        return pr

    def _set_auto_complete(self, pr_id: int, creator_id: str):
        """Set a PR to auto-complete."""
        url = f"{self.base_url}/pullrequests/{pr_id}"
        payload = {
            "autoCompleteSetBy": {"id": creator_id},
            "completionOptions": {
                "deleteSourceBranch": True,
                "mergeStrategy": "squash",
            },
        }
        resp = self.session.patch(
            url, json=payload, params=self._params()
        )
        self._raise_for_status(resp)
        logger.info(f"Set auto-complete on PR #{pr_id}")

    def get_pull_request(self, pr_id: int) -> dict:
        """Get PR details by ID."""
        resp = self._get(f"pullrequests/{pr_id}", params=self._params())
        return self._json(resp)

    def list_pull_requests(
        self,
        status: str = "active",
        source_branch: Optional[str] = None,
        top: int = 25,
    ) -> list[dict]:
        """
        List pull requests.

        Args:
            status:        "active", "completed", "abandoned", "all"
            source_branch: Filter by source branch
            top:           Max results to return
        """
        params = self._params(
            **{"searchCriteria.status": status, "$top": top}
        )
        if source_branch:
            params["searchCriteria.sourceRefName"] = self._full_ref(source_branch)

        resp = self._get("pullrequests", params=params)
        return self._json(resp).get("value", [])

    # -------------------------------------------------------------------------
    # Convenience: Full workflow
    # -------------------------------------------------------------------------

    def edit_and_pr(
        self,
        file_path: str,
        new_content: str,
        branch_name: str,
        commit_message: str,
        pr_title: str,
        pr_description: str = "",
        reviewers: Optional[list[str]] = None,
    ) -> dict:
        """
        Complete workflow: create branch, push change, open PR.

        This is the happy-path convenience method for the Streamlit app.

        Args:
            file_path:      Path of the YAML file to edit
            new_content:    Updated YAML content
            branch_name:    Feature branch name
            commit_message: Commit message
            pr_title:       Pull request title
            pr_description: Pull request description
            reviewers:      Optional list of reviewer unique names

        Returns:
            PR response dict.
        """
        # Create branch from target
        if not self.branch_exists(branch_name):
            self.create_branch(branch_name)
            logger.info(f"Created branch '{branch_name}'")
        else:
            logger.info(f"Branch '{branch_name}' already exists, reusing.")

        # Push changes
        self.push_changes(
            branch=branch_name,
            file_path=file_path,
            content=new_content,
            commit_message=commit_message,
        )

        # Create PR
        pr = self.create_pull_request(
            source_branch=branch_name,
            title=pr_title,
            description=pr_description,
            reviewers=reviewers,
        )

        return pr