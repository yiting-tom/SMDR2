"""Regression tests: a per-binding unit override must survive
re-preprocessing.

Bug: `submit_reprocess_all` dispatched `_preprocess_worker` without the
binding's stored `user_unit_override`, so any reprocess-all re-ran the
auto-detector and clobbered the operator's unit on every restart. These
tests pin the dispatch wiring under the versioned model (the worker also
receives the version's library and writes version-scoped artifact paths).

Removed test: test_startup_migration_is_a_noop_after_auto_rescale_removed —
the one-shot startup unit-rescale migration itself was REMOVED (openspec
add-product-versioning, REMOVED "One-shot legacy migration on startup");
its absence is pinned in tests/test_dxf_auto_rescale.py.
"""

from __future__ import annotations

from app import jobs
from app.files import FileStore


# Worker positional signature, for indexing the captured submit() args:
#   0 version_id, 1 file_id, 2 src, 3 parsed_dst, 4 prematch_dst,
#   5 library_id, 6 selected_layers, 7 transient_primitives,
#   8 dev_overrides_snapshot, 9 user_unit_override, 10 layout_name
_VERSION_ARG = 0
_FILE_ARG = 1
_LIBRARY_ARG = 5
_OVERRIDE_ARG = 9


class _FakeFuture:
    def add_done_callback(self, cb):  # noqa: D401 - no-op, we don't run callbacks
        pass


class _FakeExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def submit(self, fn, *args):
        self.calls.append(args)
        return _FakeFuture()


def _ready_binding(fs: FileStore, version_id: str, file_id: str, *,
                   insunits: int, bbox):
    fs.register_content(file_id, f"{file_id}.dxf", 1)
    fs.bind(version_id, "BD", file_id)
    fs.update_parsed(version_id, file_id, 1, bbox, "#000",
                     insunits=insunits, applied_scale=1.0)


class _FakeVersion:
    def __init__(self, vid, library_id):
        self.id = vid
        self.library_id = library_id
        self.is_signed_off = False


def _patch_version_lookup(monkeypatch, mapping):
    class _FakeVersionStore:
        def get(self, vid):
            return mapping.get(vid)
    monkeypatch.setattr("app.versions.VERSION_STORE", _FakeVersionStore())


def test_reprocess_all_threads_override_and_version(tmp_path, monkeypatch):
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)
    _patch_version_lookup(monkeypatch, {"v1": _FakeVersion("v1", "lib1")})

    _ready_binding(fs, "v1", "f1", insunits=4, bbox=(0, 0, 300, 300))
    fs.set_user_unit_override("v1", "f1", "mm")

    fake = _FakeExecutor()
    monkeypatch.setattr(jobs, "_get_executor", lambda: fake)

    jobs.submit_reprocess_all()

    assert len(fake.calls) == 1
    args = fake.calls[0]
    assert args[_VERSION_ARG] == "v1"
    assert args[_FILE_ARG] == "f1"
    assert args[_LIBRARY_ARG] == "lib1", "version's library must reach the worker"
    assert args[_OVERRIDE_ARG] == "mm", "stored unit override must reach the worker"


def test_reprocess_all_passes_none_override_for_unset_binding(tmp_path, monkeypatch):
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)
    _patch_version_lookup(monkeypatch, {"v1": _FakeVersion("v1", "lib1")})

    _ready_binding(fs, "v1", "f2", insunits=4, bbox=(0, 0, 300, 300))

    fake = _FakeExecutor()
    monkeypatch.setattr(jobs, "_get_executor", lambda: fake)

    jobs.submit_reprocess_all()

    assert len(fake.calls) == 1
    args = fake.calls[0]
    assert args[_OVERRIDE_ARG] is None


def test_reprocess_all_skips_signed_off_versions(tmp_path, monkeypatch):
    """Bindings on signed-off versions are frozen — reprocess-all must
    not dispatch a worker for them (their artifacts must not change)."""
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    frozen = _FakeVersion("vs", "lib-signed")
    frozen.is_signed_off = True
    _patch_version_lookup(monkeypatch, {
        "vs": frozen,
        "vo": _FakeVersion("vo", "lib-open"),
    })

    _ready_binding(fs, "vs", "ff", insunits=4, bbox=(0, 0, 300, 300))
    _ready_binding(fs, "vo", "fo", insunits=4, bbox=(0, 0, 300, 300))

    fake = _FakeExecutor()
    monkeypatch.setattr(jobs, "_get_executor", lambda: fake)

    parent_id = jobs.submit_reprocess_all()

    assert len(fake.calls) == 1
    assert fake.calls[0][_VERSION_ARG] == "vo"
    assert jobs._jobs[parent_id]["skipped"] >= 1
