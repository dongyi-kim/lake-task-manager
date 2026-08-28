# -*- coding: utf-8 -*-
"""Product-neutral evaluator gates for deterministic facts and measurements."""

import pytest

from tools.agent_eval_claims import (
    DirectSourceClaim,
    MeasurementAvailability,
    RequiredBoolean,
    SourceAuthority,
    TextRecord,
    UnresolvedViolation,
    date_quantity_consistency_flaws,
    evaluation_claim_consistency_flaws,
    indexed_source_claims,
    measurement_gate_flaws,
    source_claim_consistency_flaws,
)


def test_required_structural_false_and_unresolved_violation_are_hard_failures():
    flaws = measurement_gate_flaws(
        [
            RequiredBoolean("single-source-index", False),
            RequiredBoolean("body-citations", True),
            RequiredBoolean("optional-unavailable", None),
        ],
        [
            UnresolvedViolation("grounding", 2),
            UnresolvedViolation("postcheck", 0),
            UnresolvedViolation("unavailable", None),
        ],
    )

    assert any("single-source-index" in flaw for flaw in flaws)
    assert any("grounding" in flaw and "2" in flaw for flaw in flaws)
    assert not any("body-citations" in flaw or "optional-unavailable" in flaw
                   or "postcheck" in flaw or "unavailable" in flaw for flaw in flaws)
    assert measurement_gate_flaws(
        [RequiredBoolean("rendered-structure", True)],
        [UnresolvedViolation("grounding", 0)],
    ) == []


def test_typed_measurement_unavailability_is_infrastructure_not_product_failure():
    flaws = measurement_gate_flaws(
        [],
        [UnresolvedViolation("grounding", 2)],
        [
            MeasurementAvailability("grounding", True),
            MeasurementAvailability("postcheck", False, "TimeoutError"),
        ],
    )

    assert "unresolved violation count>0: grounding=2" in flaws
    assert (
        "evaluator infrastructure failure: measurement unavailable: "
        "postcheck errorType=TimeoutError"
    ) in flaws


def test_date_and_quantity_math_are_checked_without_case_or_product_literals():
    flaws = date_quantity_consistency_flaws([
        TextRecord(
            "schedule-a",
            "마감일이 2026-08-11에서 2026-08-25로 1주 연기되었습니다.",
        ),
        TextRecord("rollup-b", "전체 8건 중 3건 완료(75%)입니다."),
    ])

    assert any("schedule-a" in flaw and "14" in flaw and "7" in flaw for flaw in flaws)
    assert any("rollup-b" in flaw and "37.5" in flaw and "75" in flaw for flaw in flaws)
    assert date_quantity_consistency_flaws([
        TextRecord("schedule-ok", "마감일이 2026-08-11에서 2026-08-25로 2주 연기되었습니다."),
        TextRecord("rollup-ok", "전체 8건 중 3건 완료(약 38%)입니다."),
        TextRecord("rollup-rounded", "전체 3건 중 2건 완료(66%)입니다."),
        TextRecord(
            "unrelated-percent",
            "전체 8건 중 3건은 재검토 대상이고 75%는 별도 만족도입니다.",
        ),
        TextRecord(
            "unrelated-dates",
            "기록일은 2026-08-11입니다. 마감일은 2026-08-25이고 1주 뒤 재확인합니다.",
        ),
        TextRecord(
            "unrelated-follow-up",
            "마감일을 2026-08-11에서 2026-08-25로 변경했고, 1주 뒤 회의합니다.",
        ),
    ]) == []


def test_date_measurement_accepts_a_natural_inclusive_day_count():
    assert date_quantity_consistency_flaws([
        TextRecord(
            "inclusive-range",
            "기간은 2026-08-18부터 2026-08-20까지 총 3일 기간입니다.",
        ),
    ]) == []


@pytest.mark.parametrize("duration", [
    "약 1주", "대략 1주", "1주 정도", "1주 가량", "1주 내외", "1주 반",
    "1주 정도였습니다", "1주 내외였습니다",
    "about 1 week", "approximately 1 week",
])
def test_date_measurement_does_not_red_an_approximate_duration(duration):
    assert date_quantity_consistency_flaws([TextRecord(
        "approximate-range",
        f"기간은 2026-08-01부터 2026-08-10까지 {duration} 기간입니다.",
    )]) == []


@pytest.mark.parametrize("source, exact_days", [
    ("Deadline moved from 2026-08-11 to 2026-08-25 by 1 week.", 14),
    ("Period from 2026-08-01 to 2026-08-20 lasted 1 week.", 19),
    ("Period 2026-08-01 to 2026-08-06 20.5 days duration.", 5),
    ("Period 2026-08-01 to 2026-09-05 1.5 weeks duration.", 35),
])
def test_date_measurement_checks_english_relation_before_duration(source, exact_days):
    flaws = date_quantity_consistency_flaws([TextRecord("english-range", source)])

    assert any(
        "english-range" in flaw and f"exact={exact_days}" in flaw
        for flaw in flaws
    )


def test_percentage_measurement_does_not_bind_a_comma_joined_other_subject():
    assert date_quantity_consistency_flaws([TextRecord(
        "separate-survey",
        "We reviewed 3 items out of 8 items, 75% of users were satisfied.",
    )]) == []
    flaws = date_quantity_consistency_flaws([TextRecord(
        "same-metric", "We reviewed 3 items out of 8 items (75%).",
    )])
    assert any("same-metric" in flaw and "37.5" in flaw for flaw in flaws)


def test_direct_source_quantity_claim_uses_typed_source_identity_and_unit():
    authority = SourceAuthority(
        "ticket:ACME-41",
        "초기 시험은 12개 후보 중 4개 표본으로 수행했습니다.",
    )
    wrong = DirectSourceClaim(
        "2-a", "ticket:ACME-41", "초기 시험은 12개 표본으로 수행했습니다.",
    )
    correct = DirectSourceClaim(
        "2-a", "ticket:ACME-41", "초기 시험은 4개 표본으로 수행했습니다.",
    )

    flaws = source_claim_consistency_flaws([authority], [wrong])

    assert any("2-a" in flaw and "ticket:ACME-41" in flaw and "12" in flaw
               and "sample" in flaw for flaw in flaws)
    assert source_claim_consistency_flaws([authority], [correct]) == []


def test_korean_quantity_particles_and_unit_order_share_one_typed_value():
    equivalents = (
        ("초기 시험은 4개의 표본으로 수행했습니다.", "초기 시험은 4개 표본으로 수행했습니다."),
        ("검토 대상 표본은 4개입니다.", "검토 대상은 4개 표본입니다."),
        ("검토 작업은 3건입니다.", "3건의 작업을 검토합니다."),
    )

    for index, (source, claim) in enumerate(equivalents):
        source_id = f"document:generic-{index}"
        assert source_claim_consistency_flaws(
            [SourceAuthority(source_id, source)],
            [DirectSourceClaim(f"claim-{index}", source_id, claim)],
        ) == []

    flaws = source_claim_consistency_flaws(
        [SourceAuthority("document:generic-red", "검토 대상 표본은 4개입니다.")],
        [DirectSourceClaim("claim-red", "document:generic-red", "5개 표본을 검토합니다.")],
    )
    assert any("claim-red" in flaw and "quantity=5 sample" in flaw for flaw in flaws)


def test_direct_source_date_claim_must_match_the_same_typed_authority():
    authority = SourceAuthority(
        "document:plan-9", "검토 회의의 확정 기한은 2031-04-02입니다.",
    )
    wrong = DirectSourceClaim(
        "3-a", "document:plan-9", "확정 기한은 2031-04-03입니다.",
    )

    flaws = source_claim_consistency_flaws([authority], [wrong])

    assert any("3-a" in flaw and "date=2031-04-03" in flaw for flaw in flaws)
    assert source_claim_consistency_flaws([authority], [DirectSourceClaim(
        "3-a", "document:plan-9", "확정 기한은 2031-04-02입니다.",
    )]) == []


def test_r30_s6_date_claim_replay_is_red():
    flaws = evaluation_claim_consistency_flaws(
        "마감일은 2026-08-11에서 2026-08-25로 1주 연기되었습니다.",
        {"evidence": [{
            "key": "DL-9090",
            "title": "마감일이 2026-08-11에서 2026-08-25로 1주 연기되었습니다.",
        }]},
    )

    assert any("date" in flaw.lower() and "14" in flaw for flaw in flaws)


def test_r30_s8_misbound_direct_quantity_claim_replay_is_red():
    reply = """### 근거

[4] {{ticket-detail:DL-9200}}
- [4-a] 본문에서 단계적 검증 후 운영 반영
- [4-b] 문서 본문에서 1차 시험 대상은 20개 표본
"""
    evaluation_evidence = {
        # The synthesized evidence row is the claim under review, never its own authority.
        "evidence": [{
            "key": "DL-9200",
            "_source_id": "ticket:DL-9200",
            "observations": [{
                "source": "document",
                "text": "1차 시험 대상은 20개 표본",
            }],
        }],
        "queryResults": [{
            "id": "read-ticket",
            "source": "jira",
            "result": {"ticketDetails": [{
                "key": "DL-9200",
                "description": "writer 생성과 reader 소비 가능성을 단계적으로 검증한다.",
            }]},
        }, {
            "id": "read-document",
            "source": "confluence",
            "result": {"documents": [{
                "id": "doc-7",
                "url": "https://docs.example.test/pages/doc-7",
                "excerpt": "1차 시험 대상은 20개 후보 중 5개 표본",
            }]},
        }],
    }

    flaws = evaluation_claim_consistency_flaws(reply, evaluation_evidence)

    assert any("4-b" in flaw and "ticket:DL-9200" in flaw and "20" in flaw
               and "sample" in flaw for flaw in flaws)


def test_indexed_direct_claim_with_matching_authority_stays_green():
    reply = """### 근거

[1] {{ticket-detail:ACME-52}}
- [1-a] 본문에서 12개 후보 중 4개 표본으로 시험 수행
"""
    evidence = {"queryResults": [{
        "source": "jira",
        "result": {"ticketDetails": [{
            "key": "ACME-52",
            "description": "초기 시험은 12개 후보 중 4개 표본으로 수행했습니다.",
        }]},
    }]}

    assert evaluation_claim_consistency_flaws(reply, evidence) == []


def test_indexed_source_claims_stop_at_the_next_markdown_section():
    reply = """### 근거

[1] {{ticket-detail:ACME-52}}
- 4개 표본 확인

### 다음 단계

- 10개 표본 추가 계획
"""

    claims = indexed_source_claims(reply)

    assert [(claim.source_id, claim.text) for claim in claims] == [
        ("ticket:ACME-52", "4개 표본 확인"),
    ]


def test_indexed_source_claims_support_a_bold_source_section_boundary():
    reply = """**근거**

[1] {{ticket-detail:ACME-52}}
- 4개 표본 확인

**다음 단계**

- 10개 표본 추가 계획
"""

    claims = indexed_source_claims(reply)

    assert [(claim.source_id, claim.text) for claim in claims] == [
        ("ticket:ACME-52", "4개 표본 확인"),
    ]


def test_body_numeric_citation_is_checked_against_its_indexed_typed_source():
    reply = """결론은 20개 표본입니다 [1].

### 근거

[1] {{ticket-detail:ACME-61}}
- 5개 표본 확인
"""
    evidence = {"queryResults": [{
        "source": "jira",
        "result": {"ticketDetails": [{
            "key": "ACME-61",
            "description": "결론에서 확인된 대상은 5개 표본입니다.",
        }]},
    }]}

    flaws = evaluation_claim_consistency_flaws(reply, evidence)

    assert any("body:" in flaw and "ticket:ACME-61" in flaw
               and "quantity=20 sample" in flaw for flaw in flaws)


def test_body_numeric_citation_checks_a_date_against_an_exact_url_authority():
    reply = """확정 기한은 2031-04-03입니다 [2].

### 근거
[2] [검토 계획](https://docs.example.test/plans/review)
"""
    evidence = {"queryResults": [{
        "source": "documents",
        "result": {"documents": [{
            "url": "https://docs.example.test/plans/review",
            "text": "확정 기한은 2031-04-02입니다.",
        }]},
    }]}

    flaws = evaluation_claim_consistency_flaws(reply, evidence)

    assert any("body:" in flaw and "url:https://docs.example.test/plans/review" in flaw
               and "date=2031-04-03" in flaw for flaw in flaws)


def test_body_citation_keeps_a_decimal_quantity_inside_one_bounded_clause():
    evidence = {"queryResults": [{
        "source": "jira",
        "result": {"ticketDetails": [{
            "key": "ACME-63", "description": "Selection is 20.5 samples.",
        }]},
    }]}
    matching = """Selection is 20.5 samples [1].

### Evidence
[1] {{ticket-detail:ACME-63}}
- Selection is 20.5 samples.
"""
    mismatch = matching.replace(
        "Selection is 20.5 samples [1].", "Selection is 20.6 samples [1].", 1,
    )

    assert evaluation_claim_consistency_flaws(matching, evidence) == []
    assert any("body:" in flaw and "quantity=20.6 sample" in flaw
               for flaw in evaluation_claim_consistency_flaws(mismatch, evidence))


def test_body_child_citation_maps_to_its_numeric_root_authority():
    evidence = {"queryResults": [{
        "source": "jira",
        "result": {"ticketDetails": [{
            "key": "ACME-64", "description": "확인된 대상은 5개 표본입니다.",
        }]},
    }]}
    matching = """확인된 대상은 5개 표본입니다 [1-a].

### 근거
[1] {{ticket-detail:ACME-64}}
- [1-a] 확인된 대상은 5개 표본
"""
    mismatch = matching.replace("5개 표본입니다 [1-a]", "20개 표본입니다 [1-a]", 1)

    assert evaluation_claim_consistency_flaws(matching, evidence) == []
    assert any("body:" in flaw and "quantity=20 sample" in flaw
               for flaw in evaluation_claim_consistency_flaws(mismatch, evidence))


def test_body_numeric_citation_accepts_matching_authority_and_skips_multi_source_clause():
    evidence = {"queryResults": [{
        "source": "jira",
        "result": {"ticketDetails": [
            {"key": "ACME-61", "description": "대상은 5개 표본입니다."},
            {"key": "ACME-62", "description": "대상은 8개 표본입니다."},
        ]},
    }]}
    matching = """대상은 5개 표본입니다 [1].

### 근거
[1] {{ticket-detail:ACME-61}}
"""
    multi_source = """대상은 20개 표본입니다 [1] [2].

### 근거
[1] {{ticket-detail:ACME-61}}
[2] {{ticket-detail:ACME-62}}
"""

    assert evaluation_claim_consistency_flaws(matching, evidence) == []
    assert evaluation_claim_consistency_flaws(multi_source, evidence) == []


def test_conflicting_source_index_ordinal_is_not_treated_as_direct_authority():
    reply = """대상은 20개 표본입니다 [1].

### 근거
[1] {{ticket-detail:ACME-61}}
[1] {{ticket-detail:ACME-62}}
"""
    evidence = {"queryResults": [{
        "source": "jira",
        "result": {"ticketDetails": [
            {"key": "ACME-61", "description": "대상은 5개 표본입니다."},
            {"key": "ACME-62", "description": "대상은 8개 표본입니다."},
        ]},
    }]}

    assert evaluation_claim_consistency_flaws(reply, evidence) == []

    unresolved_duplicate = reply.replace(
        "[1] {{ticket-detail:ACME-62}}", "[1] 대화 기록",
    )
    assert evaluation_claim_consistency_flaws(unresolved_duplicate, evidence) == []


def test_typed_count_ratios_cover_value_first_unit_first_and_mixed_order():
    flawed = date_quantity_consistency_flaws([
        TextRecord("value-first", "20개 후보 중 5개 표본, 완료율 80%입니다."),
        TextRecord("unit-first", "후보 20개 중 표본 5개, 완료율 80%입니다."),
        TextRecord("mixed-a", "20개 후보 중 표본 5개, 완료율 80%입니다."),
        TextRecord("mixed-b", "후보 20개 중 5개 표본, 완료율 80%입니다."),
    ])

    assert len(flawed) == 4
    assert all("exact=25%" in flaw and "stated=80%" in flaw for flaw in flawed)
    assert date_quantity_consistency_flaws([
        TextRecord("matching", "후보 20개 중 표본 5개, 완료율 25%입니다."),
        TextRecord("unrelated", "후보 20개, 표본 5개, 완료율 80%입니다."),
    ]) == []


def test_typed_count_ratio_respects_english_part_of_total_orientation():
    assert date_quantity_consistency_flaws([
        TextRecord("of-green", "5 samples of 20 candidates, progress 25%."),
        TextRecord("out-of-green", "5 samples out of 20 candidates, progress 25%."),
    ]) == []

    flaws = date_quantity_consistency_flaws([
        TextRecord("of-red", "5 samples of 20 candidates, progress 80%."),
        TextRecord("out-of-red", "5 samples out of 20 candidates, progress 80%."),
    ])
    assert len(flaws) == 2
    assert all("exact=25%" in flaw and "stated=80%" in flaw for flaw in flaws)
