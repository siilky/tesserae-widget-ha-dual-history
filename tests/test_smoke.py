from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from flask.testing import FlaskClient

# These renders need the Tesserae host: its root conftest supplies the client
# fixture and the /_test/render route. They run with this folder under the
# host's plugins/ and skip in a standalone checkout of the widget.
pytest.importorskip("app.main", reason="needs the Tesserae host; run from its plugins/ folder")


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_ha_dual_history_renders(client: FlaskClient, size: str) -> None:
    response = client.get(f"/_test/render?plugin=ha_dual_history&size={size}")
    assert response.status_code == 200
