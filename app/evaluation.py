from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag import RAGService


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    question: str
    expected_sources: tuple[str, ...] = ()
    expected_answer_terms: tuple[str, ...] = ()
    should_have_answer: bool = True
    user_roles: tuple[str, ...] = ("public",)


DEFAULT_EVALUATION_CASES = [
    EvaluationCase(
        id="sample_reimbursement",
        question="差旅报销需要哪些材料？",
        expected_sources=("示例内部问答 FAQ",),
        expected_answer_terms=("报销",),
    ),
    EvaluationCase(
        id="unknown_policy",
        question="火星出差补贴标准是什么？",
        should_have_answer=False,
    ),
]


def run_evaluation(
    service: RAGService,
    cases: list[EvaluationCase] | None = None,
) -> dict[str, Any]:
    selected_cases = DEFAULT_EVALUATION_CASES if cases is None else cases
    results = [_run_case(service, case) for case in selected_cases]
    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "results": results,
    }


def evaluation_case_from_dict(item: dict[str, Any]) -> EvaluationCase:
    return EvaluationCase(
        id=str(item.get("id") or item.get("question") or "case"),
        question=str(item["question"]),
        expected_sources=tuple(str(source) for source in item.get("expected_sources", [])),
        expected_answer_terms=tuple(
            str(term) for term in item.get("expected_answer_terms", [])
        ),
        should_have_answer=bool(item.get("should_have_answer", True)),
        user_roles=tuple(str(role) for role in item.get("user_roles", ["public"])),
    )


def _run_case(service: RAGService, case: EvaluationCase) -> dict[str, Any]:
    response = service.answer(
        case.question,
        session_id=f"eval:{case.id}",
        user_roles=list(case.user_roles),
    )
    sources = response.get("sources", [])
    source_titles = [str(source.get("title", "")) for source in sources]
    answer = str(response.get("answer", ""))

    source_ok = all(
        any(expected in title for title in source_titles)
        for expected in case.expected_sources
    )
    terms_ok = all(term in answer for term in case.expected_answer_terms)
    answer_found = bool(sources)
    answer_ok = answer_found if case.should_have_answer else not answer_found
    passed = source_ok and terms_ok and answer_ok

    return {
        "id": case.id,
        "question": case.question,
        "passed": passed,
        "source_ok": source_ok,
        "answer_terms_ok": terms_ok,
        "answer_expectation_ok": answer_ok,
        "expected_sources": list(case.expected_sources),
        "source_titles": source_titles,
        "expected_answer_terms": list(case.expected_answer_terms),
        "should_have_answer": case.should_have_answer,
        "route": response.get("route", {}),
    }
