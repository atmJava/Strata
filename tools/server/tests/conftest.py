import pytest

import mp_client


@pytest.fixture(scope="session")
def server():
    try:
        return mp_client.get_json("/health", timeout=10)
    except Exception as error:  # noqa: BLE001 - any connection failure means "come back later"
        pytest.skip(f"server {mp_client.BASE_URL} not reachable: {type(error).__name__}: {error}")
