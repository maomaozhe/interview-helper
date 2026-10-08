"""Source-pair decisions made before running the dedup judge."""
from eval.common import read_jsonl

RELATED_DRAFTS = {
    3: "The unnamed processing tasks are not identified; shared parallelism wording does not prove equal scope.",
    9: "Business execution exceeding the lease is a narrower failure condition than any premature release.",
    15: "Spring Boot and generic Spring differ in their circular-dependency defaults and constraints.",
    20: "Team development workflow differs from a person's daily development mode.",
    33: "Explaining two lock mechanisms and their differences is broader than choosing between them.",
    36: "Single-agent tool partitioning and embedding role prompts inside tools have different concrete architectures.",
    40: "Implementing a Redis lock is narrower than introducing the distributed-lock concept.",
    61: "Checking semantic task completion differs from the complete set of agent-loop termination conditions.",
    64: "A person's understanding of AI differs from a broad familiarity question.",
    70: "The first explicitly requests implementation principles rather than a conceptual introduction.",
    75: "Index design depends on different unspecified SQL conditions; similarity is insufficient for equal scope.",
    81: "Confirming endpoint aggregation differs from choosing the product/API granularity of MCP tools.",
    111: "General Java collection mechanics is broader than enumerating algorithms.",
    135: "Internal business use and external business use ask about different adopters.",
    147: "Evaluating generated answers is narrower than a whole evaluation system.",
}
# Similar vocabulary with different technical demands; each pair was inspected.
RELATED_CATALOGUE = [
 (136,414), (94,948), (49,2230), (794,1292), (839,1646), (1301,2175),
 (1355,2022), (1906,2338), (684,1028), (208,763), (1111,1392), (1327,2085),
 (1983,238), (1147,1732), (777,1391), (394,205), (475,1960),
 (238,2433), (538,157), (520,1465), (2061,2372), (1801,1585), (2187,1903),
]
SAME_CATALOGUE = set()
DIFFERENT_CATALOGUE = [
 (190,148), (868,989), (1349,294), (476,15), (16,134), (136,1585),
 (794,1355), (845,538), (475,1111), (49,1147), (1028,1327), (238,187),
 (144,337), (1438,1437), (1950,334), (1877,2023), (394,880), (849,1041),
 (1903,1901), (867,882), (949,947), (1538,1535), (1653,1659), (2065,2061),
 (230,2330), (1238,123), (1801,1802), (1348,1347), (2338,2340), (205,208), (16,2048),
]


def build(workbench, catalog, original, row):
    drafts = read_jsonl(workbench / "dedup.jsonl")
    if len(drafts) != 150:
        raise ValueError("REVIEWED_DEDUP_QUEUE_SIZE_CHANGED")
    values = []
    for index, pair in enumerate(drafts):
        reason = RELATED_DRAFTS.get(index, "Same primary technical demand and answer boundary; wording and interview timing do not change the task.")
        values.append({**row(f"dedup-reviewed-{index}", "test", ["dedup", "source_pair"], reason),
            "left": pair["left"], "right": pair["right"], "label": "RELATED" if index in RELATED_DRAFTS else "SAME"})
    for label, pairs in (("RELATED", RELATED_CATALOGUE), ("DIFFERENT", DIFFERENT_CATALOGUE)):
        for index, (left, right) in enumerate(pairs):
            actual_label = "SAME" if (left,right) in SAME_CATALOGUE else label
            questions = [original[catalog[i]["id"]][0] for i in (left,right)]
            values.append({**row(f"dedup-hard-{label.lower()}-{index}", "test", ["dedup", "hard_negative"],
                "Inspect both source demands: " + catalog[left]["canonical_text"] + " / " + catalog[right]["canonical_text"]),
                "left": {"id": questions[0]["id"], "text": questions[0]["normalized_question"], "source_spans": questions[0]["source_spans"]},
                "right": {"id": questions[1]["id"], "text": questions[1]["normalized_question"], "source_spans": questions[1]["source_spans"]},
                "label": actual_label})
    return values
