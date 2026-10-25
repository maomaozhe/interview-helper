"""Single editable source of static host/Pi tool declarations.

Run ``python scripts/sync-tool-contract.py`` after changes. Pi's generated data
stays inside runtime.mjs so standalone requests and existing deployment mounts
need no new runtime resource. Host business validators and dynamic permissions
remain independent. pi_order and pi_required_extra preserve existing Pi behavior.
"""

COMMON_REQUIRED = ("action", "filters", "sort", "top_n", "page_size")

TOOL_DEFINITIONS = {'list_questions': {'actions': ['LIST', 'NEXT', 'CLARIFY'],
                    'pi_order': 0,
                    'fields': ['semantic_task',
                               'final',
                               'clarification',
                               'clarification_options',
                               'review_statuses',
                               'review_order'],
                    'required': [],
                    'hint': ' Use LIST when the user requests a question list. If the current user goal is COUNT '
                            'and the message only corrects or restates its subject, continue COUNT even if the '
                            'previous count already used that subject; do not infer a new list request. For LIST '
                            'without an explicit requested N, set top_n=null and use page_size for one page. '
                            'Never infer Top 20 from a default page_size of 20; keep the complete scope pageable. '
                            'Broad algorithm lists use semantic_task=ALGORITHM_PROBLEM (LIST); this is a complete '
                            'evidence-audited SQL collection, not semantic retrieval Top50. '
                            'All engineering/system-design questions use semantic_task=ENGINEERING_DESIGN '
                            'with LIST and top_n=null unless an N is explicit; preserve original user scope, including AI/Agent '
                            'engineering tasks. NEXT preserves the current scope and pages either the audited SQL '
                            'collection or the host-stored bounded semantic candidate result; semantic candidates '
                            'are never a corpus-complete set. Missing or unresolved '
                            'source audits cannot be presented as a complete set.'},
 'search_questions': {'actions': ['SEARCH'],
                      'pi_order': 1,
                      'fields': ['final',
                                 'search_query',
                                 'relevance_query',
                                 'lexical_facets',
                                 'preferred_question_type',
                                 'pipeline'],
                      'required': ['search_query', 'pipeline'],
                      'hint': ' SEARCH requires top_n=null; use page_size (1–50) for the requested result count.',
                      'replace_properties': {'top_n': {'type': 'null'}},
                      'merge_properties': {'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 50}}},
 'get_question_stats': {'actions': ['STATS'],
                        'pi_order': 2,
                        'fields': ['semantic_task',
                                   'final',
                                   'group_by',
                                   'topic_level',
                                   'review_statuses',
                                   'review_order'],
                        'required': ['group_by'],
                        'hint': ' Group authoritative counts by topic, company or round. Use topic_level=L2 for a '
                                'breakdown within one L1 topic such as Redis; use L1 for broad topic '
                                'distribution.'},
 'get_question_count': {'actions': ['COUNT'],
                        'pi_order': 4,
                        'fields': ['semantic_task', 'final', 'review_statuses'],
                        'required': [],
                        'hint': ' Count the complete current discussion scope within filters, not a result page. '
                                'A bare total question inherits the current topic; only an explicit whole-corpus '
                                'request clears it. When the current goal is COUNT, subject corrections or '
                                'restatements remain COUNT even after an already-correct count. One COUNT returns '
                                'both counts.canonical_questions (distinct questions) and counts.occurrences '
                                '(appearances). For a direct total question, including asking both numbers, call '
                                'only this tool once with final=true; do not call STATS for appearances or ANSWER '
                                'to restate counts for a DIRECT_RESULT goal. For a SYNTHESIS user_goal, this is '
                                'evidence only: continue with further authorized reads as needed, then ANSWER. '
                                'Interpret the conversational objective independently of the tool action.',
                        'replace_properties': {'top_n': {'type': 'null'}}},
 'get_question_details': {'actions': ['DETAILS'],
                          'pi_order': 3,
                          'fields': ['final', 'question_ids', 'scope'],
                          'required': ['question_ids'],
                          'hint': ''},
 'answer_question': {'actions': ['ANSWER'],
                     'pi_order': 5,
                     'fields': ['final', 'answer_text', 'answer_kind', 'answer_basis', 'question_ids', 'evidence_tool_ordinals'],
                     'required': ['answer_text'],
                     'hint': ' Finish with a direct explanation, comparison, solution, study plan or conversation '
                             'response. Set final=true and identify the knowledge basis. Cite only question_ids '
                             'returned by host tools; corpus sources establish interview questions, not the '
                             'correctness of a generated answer.',
                     'pi_required_extra': ['answer_kind', 'answer_basis', 'final'],
                     'replace_properties': {'final': {'type': 'boolean', 'const': True}},
                     'merge_existing_properties': {'question_ids': {'maxItems': 10}}},
 'get_review_state': {'actions': ['REVIEW_STATE'],
                      'pi_order': 6,
                      'fields': ['final', 'question_ids', 'scope'],
                      'required': ['question_ids'],
                      'hint': ''},
 'record_review': {'actions': ['RECORD_REVIEW'],
                   'pi_order': 7,
                   'fields': ['final', 'review_items', 'scope'],
                   'required': ['review_items'],
                   'hint': ''}}

for _definition in TOOL_DEFINITIONS.values():
    _definition["fields"].append("user_goal")

TOOL_ACTIONS = {name: set(tool["actions"]) for name, tool in TOOL_DEFINITIONS.items()}
ACTION_REQUIRED_FIELDS = {action: tuple(tool["required"])
                          for tool in TOOL_DEFINITIONS.values() for action in tool["actions"]}
