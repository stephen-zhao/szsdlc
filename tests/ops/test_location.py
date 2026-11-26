"""The `location` operation: what a location names, without creating anything."""

from __future__ import annotations

import pytest

from szsdlc import ops
from szsdlc.errors import BadInput, ConfigError


def test_a_repository_url_names_its_backend_and_store_dir(tmp_path, isolated_settings):
    from szsdlc import backend

    text = "https://tok@github.com/o/tracker.git#main"
    found = ops.call("location", {"location": text}, str(tmp_path))
    expected = backend.from_location(text).project_sdlc_store_dir
    assert found.type == "git" and found.location == "https://***@github.com/o/tracker.git#main"
    assert found.project_sdlc_store_dir == str(expected)
    assert expected.parent == isolated_settings / "szsdlc" / "git"
    assert backend.project_sdlc_store_dir_name(expected) == "tracker"
    assert found.project_sdlc_root == found.project_sdlc_store_dir and found.materialized is False


def test_a_token_never_reaches_the_store_dir(tmp_path, isolated_settings):
    for text in ("https://host/org/tracker.git?access_token=s3cret", "https://user:s3cret@host"):
        found = ops.call("location", {"location": text}, str(tmp_path))
        assert "s3cret" not in found.project_sdlc_store_dir and "s3cret" not in found.location, text


def test_a_file_in_the_way_is_not_a_materialized_store(tmp_path, isolated_settings):
    from szsdlc import backend

    text = "https://host/org/tracker.git"
    path = backend.from_location(text).project_sdlc_store_dir
    path.parent.mkdir(parents=True)
    path.write_text("not a clone", encoding="utf-8")
    assert ops.call("location", {"location": text}, str(tmp_path)).materialized is False


@pytest.mark.parametrize("text", ["not a url", "  ", "~no_such_user_szsdlc/repo"])
def test_nothing_claims_it(tmp_path, text):
    with pytest.raises(BadInput) as refused:
        ops.call("location", {"location": text}, str(tmp_path))
    assert refused.value.fix.startswith("name a repository URL")  # the remedy, in words
    assert ops.refusal_to_data(refused.value)["fix"] is None  # and not a command to run


def test_a_folder_is_not_a_location(tmp_path):
    with pytest.raises(BadInput, match="is a folder"):
        ops.call("location", {"location": str(tmp_path)}, str(tmp_path))


def test_a_refused_location_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="remote helper"):
        ops.call("location", {"location": "ext::sh -c x"}, str(tmp_path))
