"""Regression tests: a per-file unit override must survive re-preprocessing.

Bug: `submit_reprocess_all` dispatched `_preprocess_worker` without the file's
stored `user_unit_override` (or `product_id`), so any reprocess-all — including
the startup unit-rescale migration — re-ran the auto-detector and clobbered the
operator's unit on every restart. These tests pin the dispatch wiring and the
migration's exclusion of overridden files.
"""

from __future__ import annotations

from app import jobs
from app import main as main_mod
from app.files import FileStore


# Worker positional signature, for indexing the captured submit() args:
#   0 file_id, 1 src, 2 parsed_dst, 3 prematch_dst, 4 library_id,
#   5 selected_layers, 6 transient_primitives, 7 dev_overrides_snapshot,
#   8 user_unit_override, 9 product_id
_OVERRIDE_ARG = 8
_PRODUCT_ARG = 9


class _FakeFuture:
    def add_done_callback(self, cb):  # noqa: D401 - no-op, we don't run callbacks
        pass


class _FakeExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def submit(self, fn, *args):
        self.calls.append(args)
        return _FakeFuture()


def _ready_file(fs: FileStore, file_id: str, *, insunits: int, bbox, product_id=None):
    fs.register(file_id, f"{file_id}.dxf", 1, product_id=product_id)
    fs.update_parsed(file_id, 1, bbox, "#000", insunits=insunits, applied_scale=1.0)


def test_reprocess_all_threads_override_and_product_id(tmp_path, monkeypatch):
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    _ready_file(fs, "f1", insunits=4, bbox=(0, 0, 300, 300), product_id="P1")
    fs.set_user_unit_override("f1", "mm")

    fake = _FakeExecutor()
    monkeypatch.setattr(jobs, "_get_executor", lambda: fake)

    jobs.submit_reprocess_all()

    assert len(fake.calls) == 1
    args = fake.calls[0]
    assert args[0] == "f1"
    assert args[_OVERRIDE_ARG] == "mm", "stored unit override must reach the worker"
    assert args[_PRODUCT_ARG] == "P1", "product scope must reach the worker"


def test_reprocess_all_passes_none_override_for_unset_file(tmp_path, monkeypatch):
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    _ready_file(fs, "f2", insunits=4, bbox=(0, 0, 300, 300))

    fake = _FakeExecutor()
    monkeypatch.setattr(jobs, "_get_executor", lambda: fake)

    jobs.submit_reprocess_all()

    assert len(fake.calls) == 1
    args = fake.calls[0]
    assert args[_OVERRIDE_ARG] is None
    assert args[_PRODUCT_ARG] is None


def test_startup_migration_excludes_overridden_file(tmp_path, monkeypatch):
    """A unit-suspect file (detector factor != 1.0, applied_scale == 1.0) that
    carries an explicit override must NOT be re-queued by the boot migration;
    an equivalent file without an override still is."""
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)
    monkeypatch.setattr("app.main.FILE_STORE", fs)

    # Both are legacy unitless 1000x-too-big (detector would pick 0.001).
    _ready_file(fs, "needs", insunits=0, bbox=(0, 0, 42_000, 42_000))
    _ready_file(fs, "pinned", insunits=0, bbox=(0, 0, 42_000, 42_000))
    fs.set_user_unit_override("pinned", "mm")  # operator pinned → authority

    captured: dict = {}

    def fake_submit(file_id_filter=None, *, kind="reprocess-all"):
        captured["filter"] = set(file_id_filter or ())
        return "fake-job-id"

    monkeypatch.setattr("app.main.jobs.submit_reprocess_all", fake_submit)

    main_mod._submit_unit_rescale_migration()

    assert captured["filter"] == {"needs"}, (
        "overridden file must be excluded from the auto-rescale migration"
    )
