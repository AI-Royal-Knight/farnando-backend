"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from tests.factories.users import (
    AdminFactory,
    PatientFactory,
    PhysicianFactory,
    StaffFactory,
    UnverifiedUserFactory,
)


@pytest.fixture(scope="session", autouse=True)
def _collect_static_files() -> None:
    """The manifest storage required in production must work in tests too."""

    call_command("collectstatic", "--noinput", "--clear", verbosity=0)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _clear_mailbox() -> Iterator[None]:
    mail.outbox.clear()
    yield
    mail.outbox.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def make_auth_client() -> Callable[[User], APIClient]:
    def _make(user: User) -> APIClient:
        client = APIClient()
        refresh = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    return _make


@pytest.fixture
def patient_user(db: Any) -> User:
    return PatientFactory()


@pytest.fixture
def unverified_user(db: Any) -> User:
    return UnverifiedUserFactory()


@pytest.fixture
def physician_user(db: Any) -> User:
    return PhysicianFactory()


@pytest.fixture
def staff_user(db: Any) -> User:
    return StaffFactory()


@pytest.fixture
def admin_user(db: Any) -> User:
    return AdminFactory()


@pytest.fixture
def auth_client(patient_user: User, make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(patient_user)


@pytest.fixture
def staff_client(staff_user: User, make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(staff_user)


@pytest.fixture
def admin_client(admin_user: User, make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(admin_user)
