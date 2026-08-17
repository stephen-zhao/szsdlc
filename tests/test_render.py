"""Task 10 — the sync engine.

Two properties carry the design. `sync` renders a **broken** project rather
than refusing to, because intermediate states are legitimately invalid and
views are most needed when something is wrong. And nothing it generates is ever
a second home for a fact: rendering twice changes nothing, and driving work to
done flips a requirement's derived coverage in the register without a byte
changing in the requirement itself.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
import yaml

from szsdlc import render as R
from szsdlc.cli import main
from szsdlc.graph import Graph
from szsdlc.ids import IdSpace
from szsdlc.model import load_all
from szsdlc.roadmap import Roadmap


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def renderer(config):
    ids = IdSpace(config)
    store = load_all(config, ids)
    return R.Renderer(config, store, Graph(config, store, ids))


def view_text(config, name):
    return renderer(config).view(name).content


@pytest.fixture
def world(project, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2026-01-01\n",
                 "an unrefined thought\n", slug="unrefined")
    write_entity("idea", 2, "status: refined\ncaptured: 2026-01-01\n",
                 "a refined thought\n", slug="refined")
    write_entity("epic", 1, "title: Sentinel quorum\nstatus: active\nopened: 2026-08-01\n")
    write_entity("epic", 2, "title: Finished work\nstatus: closed\nopened: 2026-08-01\n",
                 slug="closed")
    write_entity("requirement", 1, "title: Quorum survives\nstatus: approved\n"
                                   "opened: 2026-08-01\ntags: [valkey]\n", slug="quorum")
    write_entity("requirement", 2, "title: Nobody built this\nstatus: approved\n"
                                   "opened: 2026-08-01\n", slug="uncovered")
    write_entity("decision", 1, "title: Sentinel not cluster\nstatus: accepted\n"
                                "opened: 2026-08-01\ntags: [valkey]\n")
    write_entity("spike", 1, "title: Investigate\nstatus: answered\nopened: 2026-08-01\n"
                             "relations:\n  parent: EPIC-0001\n",
                 artifacts={"findings.md": "It works.\n"})
    write_entity("work_item", 1, "title: Add config\nstatus: executing\nopened: 2026-08-01\n"
                                 "tags: [valkey, tls]\nrelations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0001]\n  refined_from: IDEA-0002\n",
                 slug="add-config", artifacts={"plan.md": "- [x] one\n- [ ] two\n"})
    write_entity("work_item", 2, "title: Document it\nstatus: done\nopened: 2026-08-02\n"
                                 "relations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0001]\n", slug="document",
                 artifacts={"plan.md": "- [x] all\n"})

    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0001", "now")
    roadmap.place("SPK-0001", "next")
    roadmap.save()
    return project


def fingerprint(root, pattern="*"):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob(pattern)) if p.is_file()}


# ---------------------------------------------------------------------------
# The generated banner and hash
# ---------------------------------------------------------------------------


def test_every_generated_file_carries_the_banner_and_a_hash(world):
    for name in world.views:
        content = view_text(world, name)
        assert content.startswith(R.BANNER)
        assert R.is_generated(content)
        assert R.stored_hash(content).startswith("sha256:")


def test_the_hash_distinguishes_a_hand_edit_from_staleness(world):
    content = view_text(world, "board")
    assert not R.hand_edited(content)

    tampered = content.replace("# Board", "# Board (I typed this)")
    # A hash over the rendered body is what makes this detectable at all; a
    # hash of the inputs could only ever tell you the sources had moved on.
    assert R.hand_edited(tampered)


def test_rendering_is_deterministic(world):
    assert view_text(world, "roadmap") == view_text(world, "roadmap")
    assert view_text(world, "traceability") == view_text(world, "traceability")


def test_no_timestamp_leaks_into_a_generated_file(world):
    """A clock in the output would make every sync a diff."""
    import datetime as dt

    today = dt.date.today().isoformat()
    for name in world.views:
        body = view_text(world, name).split("\n\n", 1)[1]
        assert today not in body or name == "inbox"


# ---------------------------------------------------------------------------
# The default views
# ---------------------------------------------------------------------------


def test_inbox_shows_only_unrefined_ideas(world):
    body = view_text(world, "inbox")
    assert "IDEA-0001" in body
    assert "IDEA-0002" not in body


def test_roadmap_preserves_sequence_rather_than_sorting(world):
    body = view_text(world, "roadmap")
    assert body.index("### now") < body.index("### next")
    assert "WI-0001 — Add config" in body
    assert "_empty_" in body  # `later` has nothing in it


def test_board_covers_work_only_and_groups_by_parent(world):
    body = view_text(world, "board")
    assert "Add config" in body and "Investigate" in body
    # Requirements and decisions are not work.
    assert "Quorum survives" not in body
    assert "Sentinel not cluster" not in body
    assert "**Sentinel quorum**" in body


def test_tag_index_answers_what_touches_this(world):
    body = view_text(world, "tag-index")
    assert "## valkey (3)" in body
    assert "## tls (1)" in body


def test_epic_rollup_derives_progress_and_drops_closed_epics(world):
    body = view_text(world, "epic-rollup")
    assert "EPIC-0001 — Sentinel quorum" in body
    assert "EPIC-0002" not in body
    # Two of three children terminal.
    assert "2/3 children done (67%)" in body
    assert "[x] SPK-0001" in body and "[ ] WI-0001" in body


def test_requirements_register_flags_the_uncovered_and_prunes_nothing(world):
    body = view_text(world, "requirements-register")
    assert "| REQ-0001 | approved | ✓ |" in body
    assert "| REQ-0002 | approved | **no** |" in body
    assert "WI-0001, WI-0002" in body


def test_traceability_walks_both_directions(world):
    body = view_text(world, "traceability")
    assert "### REQ-0001 — Quorum survives" in body
    assert "implemented_by: WI-0001" in body
    assert "_nothing implements this_" in body
    # And back to the originating idea.
    assert "IDEA-0002" in body


def test_decisions_lists_the_log(world):
    body = view_text(world, "decisions")
    assert "| ADR-0001 | accepted | Sentinel not cluster | valkey |" in body


# ---------------------------------------------------------------------------
# Degrade visibly, never silently
# ---------------------------------------------------------------------------


@pytest.fixture
def broken(project, write_entity):
    """A deliberately broken graph — the state sync most needs to survive."""
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-01\n")
    write_entity("work_item", 1, "title: Points nowhere\nstatus: idea\n"
                                 "opened: 2026-08-01\nrelations:\n"
                                 "  parent: EPIC-0001\n  implements: [REQ-0420]\n",
                 slug="dangling")
    write_entity("work_item", 2, raw="---\ntitle: [unclosed\n---\n", slug="corrupt")
    write_entity("work_item", 3, "title: A\nstatus: idea\nopened: 2026-08-01\n",
                 slug="dup-a")
    write_entity("work_item", 3, "title: B\nstatus: idea\nopened: 2026-08-01\n",
                 slug="dup-b")

    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0420", "now")
    roadmap.save()
    return project


def test_sync_succeeds_against_a_broken_graph(run, broken):
    """Views are most needed exactly when something is wrong."""
    code, output, err = run("sync")
    assert code == 0 and output == "" and err == ""
    assert (broken.views_dir / "board.md").is_file()


def test_a_dangling_reference_renders_visibly(broken):
    body = view_text(broken, "traceability")
    assert "REQ-0420 (missing)" in body


def test_a_dangling_roadmap_entry_renders_visibly(broken):
    assert "WI-0420 (missing)" in view_text(broken, "roadmap")


def test_an_unparseable_file_appears_with_its_path(broken):
    body = view_text(broken, "board")
    assert "## Not loaded" in body
    assert "WI-0002-corrupt/entity.md" in body
    assert "unclosed" in body or "frontmatter" in body


def test_a_duplicate_id_appears_with_its_path(broken):
    body = view_text(broken, "board")
    assert "duplicate id WI-0003" in body


def test_every_view_carries_the_broken_tail(broken):
    for name in broken.views:
        assert "## Not loaded" in view_text(broken, name), name


# ---------------------------------------------------------------------------
# compare() and sync
# ---------------------------------------------------------------------------


def test_compare_reports_what_would_differ(world):
    rendered = renderer(world).all()
    assert len(R.compare(rendered)) == len(rendered)

    for item in rendered:
        item.write()
    assert R.compare(renderer(world).all()) == []


def test_sync_writes_only_what_changed(run, world):
    run("sync")
    before = fingerprint(world.views_dir)

    entity = load_all(world, IdSpace(world)).by_text("WI-0001")
    entity.set_tags(["valkey", "tls", "quorum"])
    entity.save()
    run("sync")

    after = fingerprint(world.views_dir)
    changed = {k for k in before if before[k] != after.get(k)}
    # The tag index and board move; the decision log has no reason to.
    assert "tag-index.md" in changed
    assert "decisions.md" not in changed


def test_syncing_twice_changes_nothing(run, world):
    run("sync")
    before = fingerprint(world.root)
    run("sync")
    assert fingerprint(world.root) == before


def test_sync_never_writes_to_a_source_file(run, world):
    before = fingerprint(world.root, "*.md")
    before = {k: v for k, v in before.items() if not k.startswith("views/")}
    run("sync")
    after = {k: v for k, v in fingerprint(world.root, "*.md").items()
             if not k.startswith("views/")}
    assert after == before


def test_completing_work_flips_coverage_without_touching_the_requirement(run, world):
    """The definitional/delivery boundary, asserted through sync."""
    run("sync")
    requirements_before = fingerprint(world.dir_for("requirement"))
    register_before = (world.views_dir / "requirements-register.md").read_text(
        encoding="utf-8")
    assert "| REQ-0001 | approved | ✓ | — |" in register_before

    entity = load_all(world, IdSpace(world)).by_text("WI-0001")
    entity.set_status("dropped")
    entity.save()
    run("sync")

    register_after = (world.views_dir / "requirements-register.md").read_text(
        encoding="utf-8")
    assert "| REQ-0001 | approved | ✓ | ✓ |" in register_after
    assert fingerprint(world.dir_for("requirement")) == requirements_before


def test_sync_is_silent_on_success_and_verbose_opts_in(run, world):
    """C4 — this runs after every write and at every turn end."""
    code, output, err = run("sync")
    assert (code, output, err) == (0, "", "")

    entity = load_all(world, IdSpace(world)).by_text("WI-0001")
    entity.set_tags(["something-new"])
    entity.save()
    _, output, _ = run("sync", "--verbose")
    assert "views/tag-index.md" in output


def test_there_is_no_check_flag(run, world):
    """Staleness is a validate rule; CI asks one question, not two."""
    code, _, err = run("sync", "--check")
    assert code != 0
    assert "usage:" not in err


# ---------------------------------------------------------------------------
# Template overrides
# ---------------------------------------------------------------------------


def test_a_project_template_overrides_the_bundled_default(world):
    override = world.templates_dir / "views" / "decisions.md.j2"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("# Ours\n\n{{ of_type('decision') | length }} decisions\n",
                        encoding="utf-8")
    body = view_text(world, "decisions")
    assert "# Ours" in body and "1 decisions" in body


# ---------------------------------------------------------------------------
# Records — the generic project-specific mechanism
# ---------------------------------------------------------------------------


@pytest.fixture
def with_record(make_project, tmp_path):
    project = make_project({
        "records": {
            "hosts": {
                "title": "Hosts",
                "data": "records/hosts.yml",
                "schema": "records/hosts.schema.json",
                "template": "records/hosts.md.j2",
                "output": "hosts.md",
            }
        }
    })
    (project.root / "records").mkdir(parents=True, exist_ok=True)
    (project.root / "records" / "hosts.yml").write_text(
        yaml.safe_dump([{"name": "alpha", "role": "control"},
                        {"name": "beta", "role": "worker"}]), encoding="utf-8")
    (project.root / "records" / "hosts.schema.json").write_text(json.dumps({
        "type": "array",
        "items": {"type": "object",
                  "required": ["name", "role"],
                  "properties": {"name": {"type": "string"},
                                 "role": {"type": "string"}}},
    }), encoding="utf-8")
    template = project.templates_dir / "records" / "hosts.md.j2"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        "# Hosts\n\n{% for row in data %}- {{ row.name }} ({{ row.role }})"
        "{{ ' last seen ' ~ row.last_seen if row.last_seen is defined }}\n"
        "{% endfor %}",
        encoding="utf-8")
    return project


def test_a_record_renders_through_its_own_template(with_record):
    body = renderer(with_record).record("hosts").content
    assert "- alpha (control)" in body
    assert body.startswith(R.BANNER)


def test_sync_generates_records_alongside_views(with_record, capsys):
    assert main(["-C", str(with_record.root), "sync"]) == 0
    capsys.readouterr()
    assert (with_record.records_dir / "hosts.md").is_file()


def test_a_record_that_violates_its_schema_is_reported_by_validate(with_record,
                                                                   capsys):
    """`sync` skips it; `validate` is the command whose job it is to say so.

    `sync` is contractually silent and never validates, so a schema check
    failing there was the contract violating itself. It matters beyond
    tidiness: `context` builds the same renderer, so one bad dataset used to
    take down the SessionStart payload — a stray colon in hand-authored YAML
    costing the model everything it knows about the work.
    """
    (with_record.root / "records" / "hosts.yml").write_text(
        yaml.safe_dump([{"name": "alpha"}]), encoding="utf-8")

    assert main(["-C", str(with_record.root), "sync"]) == 0
    assert capsys.readouterr().out == ""

    code = main(["-C", str(with_record.root), "validate"])
    reported = capsys.readouterr().out
    assert code == 4
    assert "role" in reported
    assert "hosts" in reported


def test_one_broken_record_does_not_take_down_the_other_views(with_record, capsys):
    (with_record.root / "records" / "hosts.yml").write_text(
        yaml.safe_dump([{"name": "alpha"}]), encoding="utf-8")
    main(["-C", str(with_record.root), "sync"])
    capsys.readouterr()
    assert (with_record.views_dir / "inbox.md").is_file()

    assert main(["-C", str(with_record.root), "context"]) == 0
    assert "entities" in capsys.readouterr().out


def test_a_missing_dataset_names_the_file_to_create(with_record, capsys):
    (with_record.root / "records" / "hosts.yml").unlink()
    assert main(["-C", str(with_record.root), "validate"]) == 4
    assert "records/hosts.yml" in capsys.readouterr().out


def test_yaml_dates_reach_a_json_schema_as_strings(with_record, capsys):
    """`last_seen: 2026-05-02` is a `datetime.date` to YAML and a string to
    every schema anybody would write for it. Unfolded, the most obvious field
    a record can hold failed validation with a Python repr quoted back at the
    author."""
    (with_record.root / "records" / "hosts.yml").write_text(
        yaml.safe_dump([{"name": "alpha", "role": "control",
                         "last_seen": dt.date(2026, 5, 2)}]), encoding="utf-8")
    (with_record.root / "records" / "hosts.schema.json").write_text(json.dumps({
        "type": "array",
        "items": {"type": "object",
                  "required": ["name", "role", "last_seen"],
                  "properties": {"name": {"type": "string"},
                                 "role": {"type": "string"},
                                 "last_seen": {"type": "string",
                                               "format": "date"}}},
    }), encoding="utf-8")

    main(["-C", str(with_record.root), "sync"])
    capsys.readouterr()
    assert main(["-C", str(with_record.root), "validate"]) == 0
    assert capsys.readouterr().out == ""
    rendered = (with_record.records_dir / "hosts.md").read_text(encoding="utf-8")
    assert "2026-05-02" in rendered


# ---------------------------------------------------------------------------
# An empty project
# ---------------------------------------------------------------------------


def test_sync_on_an_empty_project_produces_readable_views(run, project):
    assert run("sync")[0] == 0
    inbox = (project.views_dir / "inbox.md").read_text(encoding="utf-8")
    assert "Nothing waiting." in inbox
    assert "## Not loaded" not in inbox
