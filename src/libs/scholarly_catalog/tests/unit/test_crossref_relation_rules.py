from libs.scholarly_catalog.domain.services.crossref_relation_rules import (
    CrossrefRelationRules,
)


def test_multiple_invalid_relation_predicates_get_distinct_gap_paths() -> None:
    entries, gaps = CrossrefRelationRules.extract(
        {
            "relation": {
                "a\n": [{"id-type": "doi", "id": "10.1000/a"}],
                "b\t": [{"id-type": "doi", "id": "10.1000/b"}],
            }
        }
    )

    assert entries == ()
    assert [(gap.path, gap.error_code) for gap in gaps] == [
        ("relation[predicate:0]", "relation_predicate_invalid"),
        ("relation[predicate:1]", "relation_predicate_invalid"),
    ]
    assert gaps[0].raw_json != gaps[1].raw_json
