from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_ha_dual_history_renders(client: FlaskClient, size: str) -> None:
    response = client.get(f"/_test/render?plugin=ha_dual_history&size={size}")
    assert response.status_code == 200
