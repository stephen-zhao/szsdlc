"""The `location` operation: what a location names, without creating anything."""

from __future__ import annotations

import pytest

from szsdlc import ops
from szsdlc.errors import BadInput, ConfigError


def test_a_repository_url_names_its_backend_and_store_dir(tmp_path, isolated_settings):
    found = ops.call("location", {"location": "https://tok@github.com/o/tracker.git#main"}, str(tmp_path))
    assert found.type == "git" and found.location == "https://***@github.com/o/tracker.git#main"
    assert found.project_sdlc_store_dir.startswith(str(isolated_settings / "szsdlc" / "git"))
    assert found.project_sdlc_root == found.project_sdlc_store_dir and found.materialized is False


@pytest.mark.parametrize("text", ["not a url", "  "])
def test_nothing_claims_it(tmp_path, text):
    with pytest.raises(BadInput) as refused:
        ops.call("location", {"location": text}, str(tmp_path))
    assert ops.refusal_to_data(refused.value)["fix"] is None  # a remedy, not a command


def test_a_folder_is_not_a_location(tmp_path):
    with pytest.raises(BadInput, match="is a folder"):
        ops.call("location", {"location": str(tmp_path)}, str(tmp_path))


def test_a_refused_location_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="remote helper"):
        ops.call("location", {"location": "ext::sh -c x"}, str(tmp_path))
