"""Authentication/session orchestration for guest/live REST broker modes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from kiwoom_rest_client import KiwoomRestClient
from secrets_store_windows import (
    delete_credentials,
    is_remember_supported,
    load_credentials,
    save_credentials,
)


@dataclass
class AuthState:
    live_mode: bool = False
    guest_mode: bool = False
    account_no: str = ""


class AuthManager:
    def __init__(self) -> None:
        self.state = AuthState()
        self.client: Optional[KiwoomRestClient] = None

    def start_guest_mode(self) -> None:
        self.logout()
        self.state = AuthState(live_mode=False, guest_mode=True, account_no="")

    def start_live_mode(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        remember_login: bool,
    ) -> KiwoomRestClient:
        base_url = os.environ.get("KIWOOM_REST_BASE_URL", "").strip()
        if not base_url:
            raise RuntimeError("KIWOOM_REST_BASE_URL is required")

        client = KiwoomRestClient(base_url=base_url)
        client.create_session(app_key=app_key, app_secret=app_secret)

        if remember_login and is_remember_supported():
            save_credentials(app_key, app_secret, account_no)

        self.client = client
        self.state = AuthState(live_mode=True, guest_mode=False, account_no=account_no)
        return client

    def try_restore_saved_login(self) -> Optional[tuple[str, str, str]]:
        return load_credentials()

    def logout(self) -> None:
        if self.client:
            self.client.clear_session()
        self.client = None
        delete_credentials()
        self.state = AuthState()

    def remember_supported(self) -> bool:
        return is_remember_supported()
