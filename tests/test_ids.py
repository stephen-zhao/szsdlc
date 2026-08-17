"""Task 3 — identifiers.

The properties under test are the ones the whole design rests on: an id is
opaque, lookups are tolerant of padding and case, allocation reads the
directory rather than a stored counter, and a number is never reissued — not
after a gap, and not after a conversion.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from szsdlc import config as C
from szsdlc.errors import EXIT_BAD_INPUT, BadInput
from szsdlc.ids import IdSpace, Tombstones


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A project on the shipped defaults, with the entity directories made."""

    def build(override=None, raw=None):
        cfg_dir = tmp_path / C.CONFIG_DIRNAME
        cfg_dir.mkdir(parents=True, exist_ok=True)
        text = raw if raw is not None else yaml.safe_dump(override or {}, sort_keys=False)
        (cfg_dir / C.CONFIG_FILENAME).write_text(text, encoding="utf-8")
        cfg = C.load(tmp_path)
        for entity_type in cfg.entity_types.values():
            cfg.dir_for(entity_type).mkdir(parents=True, exist_ok=True)
        return cfg

    return build


def make(cfg, type_name: str, *names: str) -> None:
    """Create entities on disk by name alone — this module only reads names."""
    entity_type = cfg.type_for(type_name)
    directory = cfg.dir_for(entity_type)
    for name in names:
        if entity_type.is_directory_layout:
            (directory / name).mkdir(parents=True, exist_ok=True)
            (directory / name / "entity.md").write_text("", encoding="utf-8")
        else:
            (directory / f"{name}.md").write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_canonical_form_is_zero_padded(project):
    ids = IdSpace(project())
    assert ids.make("WI", 42).text == "WI-0042"
    assert str(ids.make("IDEA", 7)) == "IDEA-0007"


@pytest.mark.parametrize("ref", ["WI-0042", "wi-0042", "Wi-42", "WI-42", "  wi-42  ", "WI-00042"])
def test_lookups_tolerate_padding_and_case(project, ref):
    """`szsdlc show wi-42` must resolve WI-0042; only writes are canonical."""
    ids = IdSpace(project())
    parsed = ids.parse(ref)
    assert parsed.prefix == "WI" and parsed.number == 42
    assert parsed.text == "WI-0042"


def test_unknown_prefix_lists_the_configured_ones(project):
    ids = IdSpace(project())
    with pytest.raises(BadInput) as excinfo:
        ids.parse("TASK-0001")
    err = excinfo.value
    assert "TASK" in err.problem
    assert "WI" in err.fix and "IDEA" in err.fix
    assert err.exit_code == EXIT_BAD_INPUT


def test_malformed_reference_shows_the_expected_shape(project):
    ids = IdSpace(project())
    with pytest.raises(BadInput) as excinfo:
        ids.parse("just some words")
    assert "not an id" in excinfo.value.problem
    assert len(excinfo.value.render().splitlines()) <= 3


def test_padding_is_configurable_and_only_affects_writing(project):
    ids = IdSpace(project({"id": {"padding": 2}}))
    assert ids.make("WI", 7).text == "WI-07"
    assert ids.parse("WI-0007").number == 7


def test_project_key_round_trips(project):
    ids = IdSpace(project({"id": {"key": "ACME", "pattern": "{key}-{prefix}-{number}"}}))
    assert ids.make("WI", 42).text == "ACME-WI-0042"
    assert ids.parse("acme-wi-42").number == 42

    with pytest.raises(BadInput) as excinfo:
        ids.parse("OTHER-WI-0042")
    assert "OTHER" in excinfo.value.problem


def test_leading_id_is_extracted_from_an_on_disk_name(project):
    ids = IdSpace(project())
    assert ids.parse_leading("WI-0042-add-sentinel-quorum").text == "WI-0042"
    assert ids.parse_leading("WI-0042").text == "WI-0042"
    assert ids.parse_leading("notes") is None
    assert ids.parse_leading("XX-0001-something") is None


def test_basename_pairs_the_id_with_a_cosmetic_slug(project):
    ids = IdSpace(project())
    entity_id = ids.make("WI", 42)
    assert ids.basename(entity_id, "Add sentinel quorum config") == \
        "WI-0042-add-sentinel-quorum-config"
    assert ids.basename(entity_id, "") == "WI-0042"
    assert ids.basename(entity_id, "...") == "WI-0042"


# ---------------------------------------------------------------------------
# Scanning and allocation
# ---------------------------------------------------------------------------


def test_first_id_of_an_empty_directory_is_one(project):
    ids = IdSpace(project())
    assert ids.next_id("work_item").text == "WI-0001"


def test_missing_directory_is_not_an_error(project):
    cfg = project()
    import shutil

    shutil.rmtree(cfg.dir_for("spike"))
    ids = IdSpace(cfg)
    assert ids.entries("spike") == {}
    assert ids.next_id("spike").text == "SPK-0001"


def test_allocation_reads_the_directory_rather_than_a_counter(project):
    cfg = project()
    make(cfg, "work_item", "WI-0001-first", "WI-0002-second")
    ids = IdSpace(cfg)
    assert ids.next_id("work_item").text == "WI-0003"
    # No counter file is written, so there is no second copy to disagree with.
    assert not (cfg.root / ".szsdlc" / "counters.yml").exists()


def test_gaps_are_never_backfilled(project):
    """Reissuing a number would silently repoint every published reference."""
    cfg = project()
    make(cfg, "work_item", "WI-0001-first", "WI-0007-seventh")
    assert IdSpace(cfg).next_id("work_item").text == "WI-0008"


def test_counters_are_per_type(project):
    cfg = project()
    make(cfg, "work_item", "WI-0009-nine")
    make(cfg, "spike", "SPK-0002-two")
    ids = IdSpace(cfg)
    assert ids.next_id("work_item").text == "WI-0010"
    assert ids.next_id("spike").text == "SPK-0003"
    assert ids.next_id("epic").text == "EPIC-0001"


def test_scan_ignores_names_that_are_not_ids(project):
    cfg = project()
    make(cfg, "work_item", "WI-0003-real", "README", "notes-about-WI-0099")
    ids = IdSpace(cfg)
    assert {i.text for i in ids.entries("work_item")} == {"WI-0003"}
    assert ids.next_id("work_item").text == "WI-0004"


def test_scan_respects_each_type_layout(project):
    cfg = project()
    # An idea is a single file; a work item is a directory. Putting each in the
    # other's shape must not register.
    (cfg.dir_for("idea") / "IDEA-0005-thought.md").write_text("", encoding="utf-8")
    (cfg.dir_for("idea") / "IDEA-0006-dir").mkdir()
    (cfg.dir_for("work_item") / "WI-0005-loose.md").write_text("", encoding="utf-8")

    ids = IdSpace(cfg)
    assert {i.text for i in ids.entries("idea")} == {"IDEA-0005"}
    assert ids.entries("work_item") == {}


def test_all_entries_spans_every_type(project):
    cfg = project()
    make(cfg, "work_item", "WI-0001-a")
    make(cfg, "requirement", "REQ-0004-b")
    make(cfg, "idea", "IDEA-0002-c")
    assert {i.text for i in IdSpace(cfg).all_entries()} == {"WI-0001", "REQ-0004", "IDEA-0002"}


def test_unpadded_and_overpadded_names_on_disk_still_scan(project):
    cfg = project()
    make(cfg, "work_item", "WI-7-hand-written", "WI-00008-overpadded")
    ids = IdSpace(cfg)
    assert {i.text for i in ids.entries("work_item")} == {"WI-0007", "WI-0008"}
    assert ids.next_id("work_item").text == "WI-0009"


# ---------------------------------------------------------------------------
# Resolution and tombstones
# ---------------------------------------------------------------------------


def test_resolve_finds_an_existing_entity(project):
    cfg = project()
    make(cfg, "work_item", "WI-0042-add-tls")
    assert IdSpace(cfg).resolve("wi-42").text == "WI-0042"


def test_unknown_reference_suggests_the_nearest_id(project):
    cfg = project()
    make(cfg, "work_item", "WI-0042-add-tls")
    with pytest.raises(BadInput) as excinfo:
        IdSpace(cfg).resolve("WI-0420")
    err = excinfo.value
    assert "did you mean WI-0042" in err.problem
    assert err.fix == "szsdlc show WI-0042"


def test_unknown_reference_with_nothing_close_offers_a_listing(project):
    cfg = project()
    make(cfg, "work_item", "WI-0042-add-tls")
    with pytest.raises(BadInput) as excinfo:
        IdSpace(cfg).resolve("WI-9999")
    assert "did you mean" not in excinfo.value.problem
    assert excinfo.value.fix == "szsdlc list --type work_item"


def test_tombstoned_id_resolves_to_its_successor(project):
    cfg = project()
    make(cfg, "spike", "SPK-0003-quorum")
    tombstones = Tombstones({"WI-0042": "SPK-0003"}, cfg.root / ".szsdlc" / "tombstones.yml")
    assert IdSpace(cfg, tombstones).resolve("WI-0042").text == "SPK-0003"


def test_tombstone_chains_are_followed_to_the_end(project):
    cfg = project()
    make(cfg, "spike", "SPK-0009-final")
    tombstones = Tombstones({"WI-0042": "WI-0050", "WI-0050": "SPK-0009"})
    assert tombstones.chain("WI-0042") == ["WI-0042", "WI-0050", "SPK-0009"]
    assert IdSpace(cfg, tombstones).resolve("WI-0042").text == "SPK-0009"


def test_a_tombstone_cycle_terminates_rather_than_hanging(project):
    """Reporting the cycle is validate's job; resolving must stay total."""
    tombstones = Tombstones({"WI-0001": "WI-0002", "WI-0002": "WI-0001"})
    assert tombstones.chain("WI-0001") == ["WI-0001", "WI-0002"]


def test_a_retired_id_is_never_reissued(project):
    """Its directory is gone, but the number stays spoken for."""
    cfg = project()
    make(cfg, "work_item", "WI-0001-still-here")
    tombstones = Tombstones({"WI-0009": "SPK-0002"})
    ids = IdSpace(cfg, tombstones)
    assert ids.numbers("work_item") == {1, 9}
    assert ids.next_id("work_item").text == "WI-0010"


def test_tombstones_round_trip_through_disk(project):
    cfg = project()
    tombstones = Tombstones.load(cfg.root)
    assert len(tombstones) == 0
    tombstones.record("WI-0042", "SPK-0003")
    tombstones.save()

    reloaded = Tombstones.load(cfg.root)
    assert reloaded.mapping == {"WI-0042": "SPK-0003"}
    # One entry per line, so a concurrent add resolves as a line-level merge.
    body = tombstones.path.read_text(encoding="utf-8")
    assert "WI-0042: SPK-0003\n" in body


def test_a_broken_tombstone_file_is_refused_with_its_path(project):
    cfg = project()
    path = cfg.root / ".szsdlc" / "tombstones.yml"
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(BadInput) as excinfo:
        Tombstones.load(cfg.root)
    assert "tombstones.yml" in excinfo.value.problem


# ---------------------------------------------------------------------------
# Genericity
# ---------------------------------------------------------------------------


CUSTOM = textwrap.dedent(
    """
    parent_relation: belongs_to
    id:
      pattern: "{prefix}#{number}"
      key: null
      padding: 2
    entity_types:
      _replace: true
      ticket:
        prefix: TKT
        dir: tickets
        layout: file
        actionable: true
        workflow:
          initial: new
          states:
            new: {to: [done]}
            done: {terminal: true}
      bucket:
        prefix: BKT
        dir: buckets
        layout: directory
        can_parent: true
        workflow:
          initial: open
          states:
            open: {to: [closed]}
            closed: {terminal: true}
    relations:
      _replace: true
      belongs_to:
        inverse: holds
        source_types: [ticket]
        target_types: null
        cardinality: one
    roadmaps: {_replace: true}
    views: {_replace: true}
    """
)


def test_a_non_default_id_pattern_works_end_to_end(project):
    cfg = project(raw=CUSTOM)
    ids = IdSpace(cfg)
    assert ids.make("TKT", 3).text == "TKT#03"
    assert ids.parse("tkt#3").number == 3

    make(cfg, "ticket", "TKT#07-something")
    assert ids.next_id("ticket").text == "TKT#08"
    assert ids.resolve("TKT#7").text == "TKT#07"
