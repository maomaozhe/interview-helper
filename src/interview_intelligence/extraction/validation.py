"""One publication contract shared by provider repair, ingestion and stage caches."""
from interview_intelligence.contracts import ExtractionResult


def validate_extraction(result: ExtractionResult, text: str, revision_id: str, taxonomy) -> None:
    if result.schema_version != "v1":
        raise ValueError("unsupported extraction schema version")
    if result.document_kind == "INTERVIEW_REPORT" and not result.interviews:
        raise ValueError("interview report has no sessions")
    if result.document_kind == "INTERVIEW_REPORT" and not any(
        question.evidence_kind == "INTERVIEW_QUESTION"
        for interview in result.interviews for question in interview.questions
    ):
        raise ValueError("interview report has no eligible interview questions")

    def valid(spans):
        return all(span.revision_id == revision_id and span.matches(text) for span in spans)

    session_ids = set()
    for interview in result.interviews:
        if result.document_kind in {"INTERVIEW_REPORT", "MIXED"}:
            if interview.local_id in session_ids:
                raise ValueError("duplicate local interview ID")
            session_ids.add(interview.local_id)
        if not valid(interview.session_spans):
            raise ValueError("session source span invalid")
        if not all(valid(spans) for spans in interview.metadata_evidence.values()):
            raise ValueError("metadata source span invalid")
        question_ids = set()
        for question in interview.questions:
            if bool(question.topic_l1) != bool(question.topic_l2):
                raise ValueError("taxonomy classification must provide both levels")
            if question.topic_l1:
                taxonomy.resolve(question.topic_l1, question.topic_l2)
            if question.local_id in question_ids:
                raise ValueError("duplicate local question ID")
            question_ids.add(question.local_id)
            if not valid(question.source_spans) or not valid(question.algorithm_description_spans):
                raise ValueError("source span does not match immutable text")
            if "\n".join(span.quote for span in question.source_spans) != question.raw_question:
                raise ValueError("raw_question differs from source spans")
        for followup in interview.followups:
            if followup.source_local_id not in question_ids or followup.target_local_id not in question_ids:
                raise ValueError("followup references unknown question")
            if not valid(followup.evidence_spans):
                raise ValueError("followup source span invalid")
        if result.document_kind in {"INTERVIEW_REPORT", "MIXED"}:
            eligible_order = {question.local_id: index for index, question in enumerate(interview.questions)
                              if question.evidence_kind == "INTERVIEW_QUESTION"}
            for followup in interview.followups:
                source, target = followup.source_local_id, followup.target_local_id
                if (source != target and source in eligible_order and target in eligible_order
                        and eligible_order[source] >= eligible_order[target]):
                    raise ValueError("followup must point to a later question")
