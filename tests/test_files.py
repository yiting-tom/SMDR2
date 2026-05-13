"""FileStore CRUD + lifecycle status transitions."""

from __future__ import annotations

from app.files import ERROR, FileStore, PREPROCESSING, READY


def test_register_and_get(tmp_db):
    fs = FileStore(tmp_db)
    rec = fs.register("abc123", "foo.dxf", 100_000)
    assert rec.status == PREPROCESSING
    got = fs.get("abc123")
    assert got is not None
    assert got.name == "foo.dxf"
    assert got.size == 100_000


def test_update_parsed_moves_to_ready(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("abc", "a.dxf", 1)
    fs.update_parsed("abc", primitive_count=42,
                      bbox=(0.0, 0.0, 10.0, 10.0), background="#fff")
    rec = fs.get("abc")
    assert rec.status == READY
    assert rec.primitive_count == 42
    assert rec.bbox == (0.0, 0.0, 10.0, 10.0)
    assert rec.background == "#fff"


def test_update_status_error(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("xyz", "x.dxf", 1)
    fs.update_status("xyz", ERROR, error="boom")
    rec = fs.get("xyz")
    assert rec.status == ERROR
    assert rec.error == "boom"


def test_list_all_ordered_by_upload_time(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("first", "1.dxf", 1)
    fs.register("second", "2.dxf", 1)
    listed = fs.list_all()
    # DESC by uploaded_at — most-recent first.
    assert listed[0].id == "second"
    assert listed[1].id == "first"


def test_register_idempotent_overwrites(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("dup", "v1.dxf", 100)
    fs.register("dup", "v2.dxf", 200)
    rec = fs.get("dup")
    assert rec.name == "v2.dxf"
    assert rec.size == 200


def test_to_dict_round_trip(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("k", "n.dxf", 5)
    fs.update_parsed("k", 1, (0, 1, 2, 3), "#000000")
    d = fs.get("k").to_dict()
    assert d["status"] == READY
    assert d["bbox"] == [0, 1, 2, 3]
