from tydomio.error import CommunicationError, leaf_type_names


def test_leaf_type_names_flattens_nested_groups() -> None:
    group = ExceptionGroup(
        "outer",
        [ValueError("a"), ExceptionGroup("inner", [CommunicationError("b")])],
    )

    assert list(leaf_type_names(group)) == ["ValueError", "CommunicationError"]
