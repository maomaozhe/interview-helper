"""Materialize independently reviewed scenario/relevance labels on corpus r293.

The indices below name rows in the SHA-bound, ID-sorted source catalogue. They
are explicit reviewer selections, not rankings or automatic keyword labels.
Human verification is never claimed. Development feedback stays in dev.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, write_json, write_jsonl, read_json
from eval.oracle import frequency_list

FACTS_HASH = "0b8fd1b94127c90f0c0c463295c1dc8045184d73eb8475bb91f6eca88c67501a"
AUTHORIZATION = {"policy": "delegated_agent", "thread_id": "01a10b68-5c0c-7b33-a585-16faefe7c1a3",
                 "user_instruction": "涉及人工的决定，你来做。我需要项目完善好，然后可以写到简历上，有数据支撑"}

# Query families are held out together. Directly relevant=2, nearby but explicitly
# related=1. All other source questions are irrelevant to the stated narrow task.
RETRIEVAL_TEST = [
 ("pool-params", "线程池的七个配置参数分别起什么作用", [49,432,1519,1932,1990,2344], [230,851]),
 ("pool-reject", "线程和等待队列都用满以后新任务会怎么处理", [94,2230,2256,2344], [948,1798]),
 ("pool-queue-concurrency", "线程池里两个工作线程怎样避免拿到同一个任务", [1903,2187], [488]),
 ("pool-submit", "提交线程池任务拿返回结果的两种方法有什么区别", [1316], [1679]),
 ("pool-tuning", "流量突然增大应该怎样调整线程池容量", [49,255,432,851,1519], [230,1798]),
 ("pool-caller-runs", "池里的工作线程忙完之前让提交任务的调用者自己执行怎么配置", [948], [94,2230,2256]),
 ("future-executor", "CompletableFuture默认在哪个线程池运行", [1165], []),
 ("unbounded-queue", "线程池等待任务使用无限容量队列有什么隐患", [1798], [94,255,851]),
 ("lru-implementation", "写一个增删查都是常数复杂度的最近最少使用缓存", [794,2041,2151], [436,441,695,1678]),
 ("redis-lru", "Redis内部怎样实现近似LRU淘汰", [618,1292], [436]),
 ("deadlock-conditions", "形成死锁必须同时满足哪四个条件", [136,1540], [414,840]),
 ("deadlock-prevention", "Java多线程拿锁时怎样防止循环等待", [414,840], [136,1348,1540]),
 ("deadlock-code", "用两个Java线程写出必然发生死锁的例子", [1540], [136,414]),
 ("api-idempotence", "重复提交同一接口请求怎么避免重复执行", [475,1110], [1960]),
 ("redis-lock", "基于Redis实现互斥锁要使用什么命令和参数", [839,1535,2175], [128,1296,2443]),
 ("lock-holder-crash", "持有Redis锁的进程突然退出怎么办", [1348,1492], [335]),
 ("lock-ownership", "旧请求释放Redis锁时如何避免删除新请求的锁", [1438,1646], [1492]),
 ("lock-alternatives", "不使用Redis的SET NX还能用什么实现分布式互斥", [1301,2220], [1535]),
 ("tcp-syn-rationale", "TCP建连为何不能只交换两次报文", [466,568,1102,1250,1355,1995,2049,2112,2329,2361], []),
 ("tcp-third-data", "TCP建立连接的第三个报文能携带应用数据吗", [2022], [466,1355]),
 ("tcp-close", "TCP连接关闭的四个报文分别是谁发送的", [1250,1906,2049,2112,2338], []),
 ("tcp-close-merge", "断开TCP连接时中间两次报文为什么不能合成一次", [1906], [2338]),
 ("tls-handshake", "HTTPS建立加密连接时公钥和对称密钥分别怎样使用", [845,2065], []),
 ("bplus-rationale", "数据库为什么选B+树而不用B树或哈希作为索引", [684,755,827,879,881,1256,1425,1901,2169], [1408]),
 ("bplus-delete-space", "B+树删掉数据以后磁盘空间会马上归还吗", [1028], []),
 ("bplus-weakness", "哪些工作负载不适合B+树索引", [1403], []),
 ("mvcc-versions", "MySQL如何通过历史版本链实现多版本并发读", [299,538,681,763,2203], [208]),
 ("readview-time", "MySQL读取快照的ReadView何时生成", [208,681], [763,2203]),
 ("transaction-isolation", "MySQL四种事务隔离级别以及默认级别有什么差别", [79,157,273,1538,1961], [681]),
 ("index-unusable", "SQL明明有索引却无法使用通常有哪些原因", [520,1584,2208], []),
 ("force-index", "优化器选错索引时怎么强制切换成另一个索引", [1465], []),
 ("gc-roots", "JVM垃圾回收从哪些根对象开始追踪", [1111,1585], [1392]),
 ("g1-pause", "G1设置最大停顿50毫秒是否能保证每次都不超过它", [908], []),
 ("g1-cms", "G1和CMS的回收策略各有什么优劣", [2085], [907,1136,1327,1445,1653]),
 ("cms-phases", "CMS收集器的完整回收阶段是什么", [1327,1653], [907,2085]),
 ("fullgc-investigation", "Java线上服务不断触发Full GC怎么诊断", [2084], [416,621,1566,2345,2393]),
 ("gc-reachable", "哪些Java对象即使发生GC也不会被回收", [1801], [1392,1393]),
 ("volatile-visibility", "volatile如何让一个线程看到其他线程修改的变量", [1983,2340], [238]),
 ("singleton-safe", "实现线程安全单例时为什么双重检查需要volatile", [238,867], [2433]),
 ("zero-copy", "网络IO为什么要使用零拷贝技术", [16], []),
 ("consistent-hash", "一致性哈希如何减少扩容导致的节点重映射", [1716,2048], []),
 ("rate-algorithms", "除固定窗口外还可以用哪些算法限制请求速率", [224,330,1659,2275], [1792]),
 ("rate-dimensions", "除了IP和用户还能按什么维度限流", [2195,2332], [101]),
 ("global-rate", "不同机房各有Redis时怎样实现统一限流", [101], [1659]),
 ("circuit-breaker", "服务熔断器有几个状态以及怎样恢复正常", [2061,2372], []),
 ("kafka-idempotence", "Kafka重复发送或消费消息的幂等怎样实现", [1960], [475]),
 ("bloom-principle", "布隆过滤器为什么会误判及其底层数据结构", [1147,1221,1747,2440], [1732]),
 ("bloom-size", "十亿条数据的布隆过滤器要多大空间如何继续压缩", [777,1898], [1732]),
 ("bloom-persistence", "Java进程重启以后内存里的布隆过滤器丢了怎么恢复", [1391], []),
 ("threadlocal-leak", "ThreadLocal中的对象为什么可能泄漏如何清理", [144,394,429,1034], [1275,2330]),
]
RETRIEVAL_DEV = [
 ("feedback-harness", "harness相关问题", [134,341,566,638,882,978,1056,1437,1730,1748,2023,2140,2211,
   5,148,170,182,284,294,682,710,989,1299,1642,1745,1802,2045,2306,
   15,26,69,86,123,192,221,257,337,344,405,500,513,553,588,880,947,1001,1006,1062,
   1302,1303,1320,1328,1423,1472,1542,1562,1564,1574,1637,1718,1843,1915,2082,2148,2160,
   2216,2236,2349,2383,2387], [187,493,622,1117,1468,1975,2050]),
 ("feedback-oom", "oom的常见问法有哪些", [476,503,698,787,1977], [557,574,1345,1410]),
 ("dev-memory-rise", "线上内存不断上涨但还没OOM怎么无影响排查", [1977], [503,557,698,1410]),
 ("dev-slow-sql", "慢SQL如何定位", [735,949,1007,1023,1465,2421], [520,1584]),
 ("dev-no-hit", "量子纠错码相关的面试题", [], []),
]


def stamp(basis):
    return {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "guide_version": "project_quality_agent_v1", "basis": basis}


def row(key, split, tags, basis):
    return {"id": key, "group_id": key, "split": split, "tags": tags, "human_verified": False,
            "agent_verified": True, "review": stamp(basis)}


def materialize(snapshot, output, workbench=None):
    facts = read_json(snapshot / "facts.json")
    if digest(facts) != FACTS_HASH:
        raise ValueError("REVIEWED_SOURCE_CATALOGUE_CHANGED")
    output.mkdir(parents=True, exist_ok=False)
    catalog = sorted(facts["canonicals"], key=lambda q: q["id"])
    qmap = {q["id"]: q for q in catalog}
    original = {}
    for question in facts["questions"]:
        original.setdefault(question["canonical_question_id"], []).append(question)
    selected, retrieval = {}, []
    for split, definitions in (("test", RETRIEVAL_TEST), ("dev", RETRIEVAL_DEV)):
        for key, query, direct, related in definitions:
            relevance = {}
            for grade, indices in ((1, related), (2, direct)):
                for index in indices:
                    canonical = catalog[index]
                    selected[index] = canonical
                    relevance[f"gold-q{index}"] = grade
            retrieval.append({**row(key, split, ["retrieval", key], "Review of the immutable full source catalogue; explicit narrow-topic relevance decisions"),
                "query": query, "filters": {}, "sample_kind": "positive" if relevance else "negative", "relevance": relevance})
    for i, query in enumerate(["拓扑量子计算中的编织门", "CRISPR脱靶检测", "电池固态电解质", "植物光合作用实验", "古汉语音韵重构"]):
        retrieval.append({**row(f"heldout-negative-{i}", "test", ["negative"], "No relevant question in the full r293 source catalogue"),
                          "query": query, "filters": {}, "sample_kind": "negative", "relevance": {}})
    evidence = []
    for index, canonical in sorted(selected.items()):
        source_questions = original.get(canonical["id"], [])
        if not source_questions:
            raise ValueError("RELEVANCE_MAPPING_WITHOUT_ACTIVE_EVIDENCE")
        evidence.append({"gold_question_id": f"gold-q{index}", "canonical_question_id": canonical["id"],
            "equivalence_group": f"gold-q{index}", "review": stamp("Confirmed the query-relevant text and active source evidence"),
            "canonical_text": canonical["canonical_text"],
            "source_evidence": [{"occurrence_id": q["id"], "raw_question": q["raw_question"], "source_spans": q["source_spans"]} for q in source_questions]})
    write_jsonl(output / "retrieval.jsonl", retrieval)
    write_json(output / "reviewed_source_evidence.json", evidence)
    manifest = {"version": output.name, "status": "frozen", "kind": "corpus", "snapshot": facts["snapshot"],
        "physical_indices": facts["physical_indices"], "review_authorization": AUTHORIZATION,
        "annotation_guide": "evals/project_quality/review_protocol.json", "models": {}, "facts_sha256": FACTS_HASH,
        "id_namespace": "gold", "canonical_mapping": {"corpus_revision": facts["snapshot"]["corpus_revision"],
            "items": evidence, "review": stamp("Explicit source-grounded relevance identities mapped to r293 active canonical identities")},
        "retrieval": "retrieval.jsonl"}
    sql = []
    companies = sorted({s["company_normalized"] for s in facts["interviews"] if s.get("company_normalized")})
    requests = [{"page_size": 31}, {"top_n": 35, "page_size": 8}, {"coding_focus": "ALGORITHM", "page_size": 19},
        {"coding_focus": "ENGINEERING", "response_form": "CODE", "page_size": 4}, {"response_form": "SQL", "page_size": 3},
        {"round": "FIRST", "page_size": 23}, {"round": "THIRD", "page_size": 11},
        {"topic_l1": "Redis", "page_size": 9}, {"topic_l1": "数据库", "page_size": 14},
        {"topic_l1": "Java", "page_size": 21}]
    requests += [{"company": c, "page_size": 13} for c in companies[:10]]
    for i, request in enumerate(requests):
        request["annotation_status"] = "KNOWN"
        truth = frequency_list(facts, request)
        sql.append({**row(f"sql-scope-{i}", "test", ["sql", "full_pagination"], "Independent exported-facts grouping/counting oracle; no production SQL or rankings reused"),
            "request": request, "all_pages": True, "assertions": [
                {"id": "ids", "path": "result.rows.*.canonical_question_id", "op": "eq", "value": truth["ids"]},
                {"id": "counts", "path": "result.rows.*.occurrence_count", "op": "eq", "value": truth["counts"]},
                {"id": "unique", "path": "result.rows.*.canonical_question_id", "op": "unique"},
                {"id": "zero_models", "path": "model_calls", "op": "length", "value": 0, "dimension": "efficiency"}]})
    write_jsonl(output / "sql.jsonl", sql)
    manifest["sql"] = "sql.jsonl"
    if workbench:
        from evals.project_quality.reviewed_dedup import build as dedup_gold
        from evals.project_quality.reviewed_routing import build as routing_gold
        dedup = dedup_gold(workbench, catalog, original, row)
        routing = routing_gold(facts, row)
        write_jsonl(output / "dedup.jsonl", dedup)
        write_jsonl(output / "routing.jsonl", routing)
        manifest.update(dedup="dedup.jsonl", routing="routing.jsonl", dedup_candidate_queue_sha256=digest((workbench/"dedup.jsonl").read_bytes()))
    write_json(output / "manifest.json", manifest)
    from eval.validate_gold import load_dataset
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {file.name:digest(file.read_bytes()) for file in sorted(output.iterdir()) if file.is_file() and file.name!="freeze.json"}})
    print({"dataset": str(output), "retrieval_test_positive": len(RETRIEVAL_TEST), "sql_test": len(sql), "human_verified": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workbench", type=Path)
    args = parser.parse_args()
    materialize(args.snapshot, args.output, args.workbench)
