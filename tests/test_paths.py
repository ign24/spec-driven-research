import pytest

from sdr.paths import resolve_child, resolve_root, resolve_segment


def test_resolve_root_accepts_configured_absolute_path(tmp_path):
    assert resolve_root(tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize(
    "relative", ["/tmp/outside", "C:/outside", "../outside", "safe/../../outside"]
)
def test_resolve_child_rejects_absolute_and_parent_traversal(tmp_path, relative):
    with pytest.raises(ValueError, match="invalid relative path"):
        resolve_child(tmp_path, relative)


@pytest.mark.parametrize("segment", ["nested/slug", "nested\\slug"])
def test_resolve_segment_rejects_path_separators(tmp_path, segment):
    with pytest.raises(ValueError, match="invalid segment"):
        resolve_segment(tmp_path, segment)


def test_resolve_child_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside the allowed root"):
        resolve_child(root, "linked/secret.txt")


def test_resolve_child_allows_nested_path_inside_root(tmp_path):
    assert resolve_child(tmp_path, "notes/source.md") == tmp_path / "notes" / "source.md"
