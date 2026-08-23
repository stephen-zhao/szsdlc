"""Task 2 — config schema and loader.

Two things are under test here. The first is the loader: discovery, merging,
schema validation and the cross-reference checks. The second, and the more
important one, is that the *shipped defaults are data* — a project can flip one
flag, drop a type, or replace the whole entity-type set without any code in
this repo knowing the names of the six defaults.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from szsdlc import config as C
from szsdlc.errors import EXIT_CONFIG, ConfigError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_project(root, override=None, *, raw: str | None = None):
    """Create a project whose config is `override` merged over the defaults."""
    cfg_dir = root / C.CONFIG_DIRNAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    text = raw if raw is not None else yaml.safe_dump(override or {}, sort_keys=False)
    (cfg_dir / C.CONFIG_FILENAME).write_text(text, encoding="utf-8")
    return root


def load_failing(root, override=None, *, raw: str | None = None) -> ConfigError:
    write_project(root, override, raw=raw)
    with pytest.raises(ConfigError) as excinfo:
        C.load(root)
    return excinfo.value


# ---------------------------------------------------------------------------
# The shipped defaults
# ---------------------------------------------------------------------------


def test_defaults_are_valid_on_their_own():
    cfg = C.default_config(".")
    assert set(cfg.entity_types) == {
        "idea", "epic", "requirement", "spike", "work_item", "decision"
    }
    assert cfg.prefixes == ("ADR", "EPIC", "IDEA", "REQ", "SPK", "WI")


def test_defaults_carry_the_documented_capability_flags():
    cfg = C.default_config(".")
    flags = {
        name: tuple(f for f in C.CAPABILITY_FLAGS if t.flag(f))
        for name, t in cfg.entity_types.items()
    }
    assert flags["idea"] == ("intake",)
    assert flags["epic"] == ("can_parent", "schedulable")
    assert flags["spike"] == ("actionable", "schedulable")
    assert flags["work_item"] == ("actionable", "tracks_progress", "schedulable")
    # A requirement and a decision are both long-lived definitions rather than
    # work, so they must land on identical flags. If this ever diverges, the
    # flags have started encoding type names.
    assert flags["requirement"] == flags["decision"] == ("persistent",)


def test_requirement_stores_no_work_state():
    """A requirement's lifecycle describes the statement, never its delivery."""
    req = C.default_config(".").type_for("requirement")
    assert not req.tracks_progress and not req.schedulable and not req.actionable
    assert set(req.derived) == {"covered", "delivered"}
    assert req.progress_artifact is None


def test_no_module_needs_to_name_a_type():
    cfg = C.default_config(".")
    assert [t.name for t in cfg.types_with("actionable")] == ["spike", "work_item"]
    assert [t.name for t in cfg.types_with("can_parent")] == ["epic"]
    assert [t.name for t in cfg.types_with("persistent")] == ["requirement", "decision"]


def test_parent_targets_are_derived_from_can_parent():
    """`can_parent` is where that fact lives; the relation must not restate it."""
    cfg = C.default_config(".")
    assert cfg.data["relations"]["parent"]["target_types"] is None
    assert cfg.parent_relation.target_types == ("epic",)


def test_allowed_relations_are_derived_from_source_types():
    cfg = C.default_config(".")
    assert [r.name for r in cfg.relations_from("requirement")] == [
        "refined_from", "supersedes", "informed_by"
    ]
    # A requirement is not work, so it never sits under an epic.
    assert "parent" not in {r.name for r in cfg.relations_from("requirement")}
    assert "implements" in {r.name for r in cfg.relations_from("work_item")}


def test_inverses_are_declared_once_and_resolvable():
    cfg = C.default_config(".")
    assert cfg.relation_for_inverse("children").name == "parent"
    assert cfg.relation_for_inverse("implemented_by").name == "implements"
    assert cfg.relation_for_inverse("nothing") is None


def test_workflow_helpers():
    wf = C.default_config(".").type_for("work_item").workflow
    assert wf.initial == "idea"
    assert wf.can_move("ready", "designing")
    assert not wf.can_move("ready", "done")
    assert wf.is_terminal("done") and not wf.is_terminal("executing")
    # A hand-edited file may hold a status outside the workflow; asking about
    # it must answer, not raise.
    assert not wf.is_terminal("banana")
    assert wf.states["done"].requires_tasks_complete
    assert wf.states["planned"].requires_artifact == ("design.md",)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_config_is_found_by_walking_up(tmp_path):
    write_project(tmp_path)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert C.find_project_root(deep) == tmp_path.resolve()
    assert C.load(deep).root == tmp_path.resolve()


def test_missing_project_names_the_fix(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        C.load(tmp_path)
    err = excinfo.value
    assert "no szsdlc project" in err.problem
    assert err.fix == "szsdlc init"
    assert err.exit_code == EXIT_CONFIG


def test_empty_config_file_is_pure_defaults(tmp_path):
    write_project(tmp_path, raw="")
    assert set(C.load(tmp_path).entity_types) == set(C.default_config(".").entity_types)


def test_broken_yaml_reports_the_line(tmp_path):
    err = load_failing(tmp_path, raw="paths:\n  views: [unclosed\n")
    assert str(C.CONFIG_FILENAME) in err.problem
    assert err.exit_code == EXIT_CONFIG


def test_non_mapping_config_is_refused(tmp_path):
    err = load_failing(tmp_path, raw="- one\n- two\n")
    assert "must be a mapping" in err.problem


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def test_override_adjusts_one_flag_and_leaves_the_rest(tmp_path):
    write_project(tmp_path, {"entity_types": {"spike": {"persistent": True}}})
    cfg = C.load(tmp_path)
    assert cfg.type_for("spike").persistent
    assert cfg.type_for("spike").prefix == "SPK"
    assert set(cfg.entity_types) == set(C.default_config(".").entity_types)


def test_lists_replace_rather_than_append():
    merged = C.deep_merge({"a": [1, 2, 3]}, {"a": [9]})
    assert merged["a"] == [9]


def test_null_removes_a_declared_block_but_not_a_scalar():
    base = {"types": {"spike": {"prefix": "SPK"}}, "id": {"key": "ACME"}}
    merged = C.deep_merge(base, {"types": {"spike": None}, "id": {"key": None}})
    assert merged["types"] == {}
    # Clearing a scalar is an assignment, not a deletion — otherwise
    # `id.key: null` would silently drop the key instead of unsetting it.
    assert merged["id"] == {"key": None}


def test_replace_swaps_a_subtree_wholesale():
    merged = C.deep_merge(
        {"types": {"a": {"x": 1}, "b": {"x": 2}}},
        {"types": {"_replace": True, "c": {"x": 3}}},
    )
    assert merged["types"] == {"c": {"x": 3}}
    assert C.REPLACE_KEY not in str(merged)


def test_merging_does_not_mutate_the_defaults(tmp_path):
    before = C.load_defaults()
    write_project(tmp_path, {"entity_types": {"spike": {"prefix": "ZZZ"}}})
    C.load(tmp_path)
    assert C.load_defaults() == before


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_is_named(tmp_path):
    err = load_failing(tmp_path, {"nonsense": 1})
    assert "nonsense" in err.problem
    assert err.fix


def test_unknown_nested_key_is_named_with_its_path(tmp_path):
    err = load_failing(tmp_path, {"entity_types": {"spike": {"colour": "blue"}}})
    assert "entity_types.spike.colour" in err.problem


def test_bad_enum_value_lists_the_legal_ones(tmp_path):
    err = load_failing(tmp_path, {"entity_types": {"spike": {"layout": "folder"}}})
    assert "entity_types.spike.layout" in err.problem
    assert "file" in err.fix and "directory" in err.fix


def test_every_config_error_fits_the_stderr_contract(tmp_path):
    """C7: at most three lines, always a runnable-or-actionable Fix."""
    cases = [
        {"nonsense": 1},
        {"entity_types": {"spike": {"layout": "folder"}}},
        {"entity_types": {"spike": {"prefix": "WI"}}},
        {"relations": {"implements": {"source_types": ["nonesuch"]}}},
        {"roadmaps": {"roadmap": {"requires_scheduling": {"requirement": "draft"}}}},
        {"id": {"pattern": "{number}"}},
    ]
    for i, override in enumerate(cases):
        root = tmp_path / f"case{i}"
        root.mkdir()
        err = load_failing(root, override)
        rendered = err.render()
        assert len(rendered.splitlines()) <= 3, rendered
        assert "Fix: " in rendered, rendered
        assert "Traceback" not in rendered
        assert err.exit_code == EXIT_CONFIG


# ---------------------------------------------------------------------------
# Semantic checks
# ---------------------------------------------------------------------------


def test_duplicate_prefix_is_refused(tmp_path):
    err = load_failing(tmp_path, {"entity_types": {"spike": {"prefix": "WI"}}})
    assert "prefix" in err.problem and "WI" in err.problem


def test_duplicate_directory_is_refused(tmp_path):
    err = load_failing(tmp_path, {"entity_types": {"spike": {"dir": "work-items"}}})
    assert "work-items" in err.problem


def test_illegal_transition_target_is_refused(tmp_path):
    err = load_failing(
        tmp_path,
        {"entity_types": {"work_item": {"workflow": {"states": {"ready": {"to": ["shipped"]}}}}}},
    )
    assert "shipped" in err.problem
    assert "entity_types.work_item.workflow.states.ready.to" in err.problem


def test_initial_status_must_be_a_state(tmp_path):
    err = load_failing(
        tmp_path, {"entity_types": {"spike": {"workflow": {"initial": "nowhere"}}}}
    )
    assert "workflow.initial" in err.problem


def test_unreachable_state_is_refused(tmp_path):
    err = load_failing(
        tmp_path,
        {"entity_types": {"spike": {"workflow": {"states": {"stranded": {"terminal": True}}}}}},
    )
    assert "stranded" in err.problem and "unreachable" in err.problem


def test_terminal_state_cannot_transition_out(tmp_path):
    err = load_failing(
        tmp_path,
        {"entity_types": {"spike": {"workflow": {"states": {"answered": {"to": ["open"]}}}}}},
    )
    assert "terminal" in err.problem


def test_gate_cannot_require_an_undeclared_artifact(tmp_path):
    err = load_failing(
        tmp_path,
        {"entity_types": {"spike": {"workflow": {"states": {"answered": {
            "requires_artifact": ["report.md"]}}}}}},
    )
    assert "report.md" in err.problem
    assert "entity_types.spike.artifacts" in err.fix


def test_tasks_gate_requires_progress_tracking(tmp_path):
    err = load_failing(
        tmp_path,
        {"entity_types": {"spike": {"workflow": {"states": {"answered": {
            "requires_tasks_complete": True}}}}}},
    )
    assert "tracks_progress" in err.problem


def test_progress_tracking_requires_an_artifact(tmp_path):
    err = load_failing(
        tmp_path, {"entity_types": {"work_item": {"progress_artifact": None}}}
    )
    assert "progress_artifact" in err.problem


def test_a_layout_with_no_room_cannot_carry_artifacts(tmp_path):
    """Both of the layouts that are not `directory`, named in the refusal.

    The message says which layout was actually declared, because "a file
    layout entity cannot" is confusing advice to read about a section one.
    """
    for layout in ("section", "file"):
        err = load_failing(tmp_path, {"entity_types": {"idea": {
            "layout": layout, "artifacts": ["notes.md"]}}})
        assert f"a `{layout}` layout entity has nowhere to put an artifact" in err.problem


def test_section_file_on_a_type_that_has_no_shared_file_is_refused(tmp_path):
    """A key that does nothing is a key someone is relying on."""
    err = load_failing(tmp_path, {"entity_types": {"idea": {
        "layout": "file", "section_file": "ideas.md"}}})
    assert "not stored in a shared file" in err.problem


def test_custom_field_cannot_shadow_a_core_field(tmp_path):
    err = load_failing(
        tmp_path, {"entity_types": {"spike": {"fields": {"status": {"type": "string"}}}}}
    )
    assert "core frontmatter field" in err.problem


def test_relation_naming_an_unknown_type_is_refused(tmp_path):
    err = load_failing(tmp_path, {"relations": {"implements": {"source_types": ["nonesuch"]}}})
    assert "nonesuch" in err.problem
    assert "relations.implements.source_types" in err.problem


def test_relation_inverse_may_not_shadow_an_authored_relation(tmp_path):
    err = load_failing(tmp_path, {"relations": {"informed_by": {"inverse": "implements"}}})
    assert "implements" in err.problem


def test_duplicate_inverse_is_refused(tmp_path):
    err = load_failing(tmp_path, {"relations": {"informed_by": {"inverse": "children"}}})
    assert "children" in err.problem


def test_required_relation_must_be_authorable_by_that_type(tmp_path):
    err = load_failing(tmp_path, {"relations": {"parent": {"required_on": ["requirement"]}}})
    assert "required_on" in err.problem


def test_parent_target_must_declare_can_parent(tmp_path):
    err = load_failing(tmp_path, {"relations": {"parent": {"target_types": ["work_item"]}}})
    assert "can_parent" in err.problem


def test_parent_relation_must_exist(tmp_path):
    err = load_failing(tmp_path, {"parent_relation": "belongs_to"})
    assert "parent_relation" in err.problem


def test_derived_attribute_needs_a_real_relation(tmp_path):
    err = load_failing(
        tmp_path,
        {"entity_types": {"requirement": {"derived": {"covered": {
            "kind": "has_incoming", "relation": "satisfies"}}}}},
    )
    assert "satisfies" in err.problem


def test_derived_attribute_may_not_shadow_a_generated_inverse(tmp_path):
    """Stating one fact twice is the failure mode this framework exists to stop."""
    err = load_failing(
        tmp_path,
        {"entity_types": {"idea": {"derived": {"refined_into": {
            "kind": "incoming_refs", "relation": "refined_from"}}}}},
    )
    assert "refined_into" in err.problem


def test_roadmap_cannot_demand_a_non_schedulable_type(tmp_path):
    err = load_failing(
        tmp_path,
        {"roadmaps": {"roadmap": {"requires_scheduling": {"requirement": "draft"}}}},
    )
    assert "schedulable" in err.problem
    assert "requirement" in err.problem


def test_roadmap_status_must_belong_to_that_type(tmp_path):
    err = load_failing(
        tmp_path, {"roadmaps": {"roadmap": {"requires_scheduling": {"spike": "groomed"}}}}
    )
    assert "groomed" in err.problem


def test_roadmap_cannot_demand_a_terminal_status(tmp_path):
    err = load_failing(
        tmp_path, {"roadmaps": {"roadmap": {"requires_scheduling": {"spike": "answered"}}}}
    )
    assert "terminal" in err.problem


def test_id_pattern_must_use_prefix_and_number(tmp_path):
    assert "{prefix}" in load_failing(tmp_path, {"id": {"pattern": "{number}"}}).fix


def test_id_key_and_pattern_must_agree(tmp_path):
    err = load_failing(tmp_path, {"id": {"key": "ACME"}})
    assert "{key}" in err.problem or "{key}" in err.fix


def test_paths_may_not_escape_the_project(tmp_path):
    err = load_failing(tmp_path, {"paths": {"views": "../elsewhere"}})
    assert "escapes the project root" in err.problem


def test_deleting_a_type_without_pruning_its_relations_is_refused(tmp_path):
    """The dangling reference is reported rather than silently tolerated."""
    err = load_failing(tmp_path, {"entity_types": {"spike": None}})
    assert "spike" in err.problem
    assert err.problem.startswith("config: relations.")


# ---------------------------------------------------------------------------
# Genericity: a project may replace the shipped model outright
# ---------------------------------------------------------------------------


CUSTOM = textwrap.dedent(
    """
    project:
      name: Wholly Custom

    parent_relation: belongs_to

    id:
      pattern: "{key}-{prefix}-{number}"
      key: ACME
      padding: 3

    entity_types:
      _replace: true
      ticket:
        prefix: TKT
        dir: tickets
        layout: directory
        actionable: true
        tracks_progress: true
        schedulable: true
        artifacts: [tasks.md]
        progress_artifact: tasks.md
        workflow:
          initial: new
          states:
            new: {to: [doing]}
            doing: {to: [shipped]}
            shipped: {terminal: true, requires_tasks_complete: true}
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
        acyclic: true
        required_on: [ticket]

    roadmaps:
      _replace: true
      release-1:
        horizons: [alpha, beta, ga]
        requires_scheduling:
          ticket: new

    views:
      _replace: true

    records: {}
    """
)


def test_a_wholly_custom_entity_type_set_loads(tmp_path):
    write_project(tmp_path, raw=CUSTOM)
    cfg = C.load(tmp_path)

    assert set(cfg.entity_types) == {"ticket", "bucket"}
    assert cfg.prefixes == ("BKT", "TKT")
    assert cfg.type_for_prefix("tkt").name == "ticket"

    # The parent axis is configuration too: a differently named relation, with
    # its targets still derived from can_parent.
    assert cfg.parent_relation.name == "belongs_to"
    assert cfg.parent_relation.target_types == ("bucket",)
    assert cfg.relation_for_inverse("holds").name == "belongs_to"

    assert cfg.only_roadmap().horizons == ("alpha", "beta", "ga")
    assert cfg.id_key == "ACME" and cfg.id_padding == 3
    assert cfg.views == {} and cfg.records == {}


def test_custom_set_resolves_its_own_paths(tmp_path):
    write_project(tmp_path, raw=CUSTOM)
    cfg = C.load(tmp_path)
    assert cfg.dir_for("ticket") == (tmp_path / "tickets").resolve()
    assert cfg.roadmap_path("release-1") == tmp_path.resolve() / "roadmaps" / "release-1.yml"


def test_roadmap_defaulting_needs_exactly_one(tmp_path):
    write_project(tmp_path, {"roadmaps": {"per-epic": {"horizons": ["now", "later"]}}})
    cfg = C.load(tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        cfg.only_roadmap()
    assert "--roadmap" in excinfo.value.fix
