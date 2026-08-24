import logging
from typing import Any, Dict, List, Optional
import requests

from app.config import settings

logger = logging.getLogger(__name__)


class ApiClientError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API Error {status_code}: {detail}")


class ApiClient:
    def __init__(self, base_url: Optional[str] = None):
        self._base_url = base_url

    @property
    def base_url(self) -> str:
        base_url = (self._base_url or settings.fastapi_backend_url).strip().rstrip("/")
        if not base_url:
            raise ApiClientError(503, "FASTAPI_BACKEND_URL is not configured.")
        return base_url

    def _headers(self, session_token: str) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if session_token:
            headers["Authorization"] = f"Bearer {session_token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        session_token: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Any:
        normalized_path = "/" + path.lstrip("/")
        url = f"{self.base_url}{normalized_path}"
        headers = self._headers(session_token)
        logger.info("API request: %s %s", method.upper(), url)
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=timeout,
            )
            logger.info("API response: %s %s -> %s", method.upper(), url, response.status_code)
            if not response.ok:
                detail = f"HTTP {response.status_code}"
                try:
                    err_json = response.json()
                    detail = err_json.get("detail", detail)
                except Exception:
                    detail = response.text or detail
                raise ApiClientError(response.status_code, detail)
            return response.json()
        except requests.RequestException as e:
            logger.error("API request failed (%s %s): %s", method, path, e)
            raise ApiClientError(503, f"Backend API unavailable at {self.base_url}: {e}")

    def get_me(self, session_token: str) -> Dict[str, Any]:
        return self._request("GET", "/api/me", session_token)

    def get_briefing(self, session_token: str) -> Dict[str, Any]:
        return self._request("GET", "/api/briefing", session_token)

    def get_inbox(
        self,
        session_token: str,
        priority: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if priority:
            params["priority"] = priority
        if search:
            params["search"] = search
        return self._request("GET", "/api/inbox", session_token, params=params)

    def get_inbox_message(self, session_token: str, message_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/inbox/message/{message_id}", session_token)

    def acknowledge_triage(self, session_token: str, triage_id: int) -> Dict[str, Any]:
        return self._request("PATCH", f"/api/inbox/{triage_id}/acknowledge", session_token)

    def get_tasks(self, session_token: str, include_done: bool = False) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/tasks", session_token, params={"include_done": include_done})

    def update_task_status(self, session_token: str, task_id: int, status: str) -> Dict[str, Any]:
        return self._request("PATCH", f"/api/tasks/{task_id}", session_token, json_data={"status": status})

    def trigger_sync(self, session_token: str) -> Dict[str, Any]:
        return self._request("POST", "/api/sync", session_token, timeout=120)

    def get_contacts(self, session_token: str) -> List[str]:
        return self._request("GET", "/api/contacts", session_token)

    def compose_draft(
        self, session_token: str, instructions: str, target_email: str = "", subject: str = ""
    ) -> Dict[str, Any]:
        payload = {
            "instructions": instructions,
            "target_email": target_email,
            "subject": subject,
        }
        return self._request("POST", "/api/drafts/compose", session_token, json_data=payload, timeout=60)

    def compose_reply(self, session_token: str, message_id: str, prompt: str) -> Dict[str, Any]:
        payload = {"message_id": message_id, "prompt": prompt}
        return self._request("POST", "/api/drafts/reply", session_token, json_data=payload, timeout=60)

    def get_draft(self, session_token: str, draft_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/api/drafts/{draft_id}", session_token)

    def refine_draft(self, session_token: str, draft_id: int, feedback: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/drafts/{draft_id}/refine", session_token, json_data={"feedback": feedback}, timeout=60)

    def send_draft(self, session_token: str, draft_id: int, to_addr: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/drafts/{draft_id}/send", session_token, json_data={"to_addr": to_addr}, timeout=60)

    def discard_draft(self, session_token: str, draft_id: int) -> Dict[str, Any]:
        return self._request("POST", f"/api/drafts/{draft_id}/discard", session_token)


api_client = ApiClient()