"""Bounded, read-only HTTP benchmark. Run in the API container on the shared DB.

Reports this development machine only; it does not certify production capacity.
No provider requests, imports, review writes or task annotation changes are made.
"""
import argparse,concurrent.futures,json,math,platform,time
from pathlib import Path
import httpx
from sqlalchemy import func,select
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database,ModelCall,CorpusState

parser=argparse.ArgumentParser()
parser.add_argument("--requests",type=int,default=100)
parser.add_argument("--concurrency",type=int,default=4)
args=parser.parse_args()
if not 10<=args.requests<=500 or not 1<=args.concurrency<=8: raise ValueError("bounded benchmark parameters required")
db=create_database(load_settings().database_url,create_tables=False)
def model_count():
    with db.session() as s:return s.scalar(select(func.count()).select_from(ModelCall))
baseline=model_count()
cases=[{"coding_focus":"ALGORITHM","top_n":40,"page_size":40},
       {"coding_focus":"ENGINEERING","response_form":"CODE","page_size":20},
       {"sort":"importance","page_size":20},{"sort":"gap","page_size":20}]
def run(i):
    started=time.perf_counter()
    with httpx.Client(trust_env=False,timeout=20) as client:
        r=client.get("http://127.0.0.1:8000/api/questions/list",params=cases[i%len(cases)])
        r.raise_for_status();p=r.json()
        return {"case":i%len(cases),"ms":round((time.perf_counter()-started)*1000,3),
                "total":p["meta"]["pagination"]["total"],"returned":len(p["data"])}
with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
    # Cold here means this script's first pass, not an OS/PG cache flush.
    cold=list(pool.map(run,range(4)))
    started=time.perf_counter();samples=list(pool.map(run,range(args.requests)));elapsed=time.perf_counter()-started
ordered=sorted(x["ms"] for x in samples)
with db.session() as s:
    corpus=s.get(CorpusState,1)
    revisions={"corpus":corpus.current_revision,"annotations":corpus.task_annotation_revision,"index":corpus.indexed_revision}
report={"requests":args.requests,"concurrency":args.concurrency,"revisions":revisions,
    "platform":platform.platform(),"cold_definition":"first pass; PG/OS caches not flushed",
    "first_pass":cold,"warm_p50_ms":ordered[math.ceil(len(ordered)*.5)-1],
    "warm_p95_ms":ordered[math.ceil(len(ordered)*.95)-1],"max_ms":ordered[-1],
    "requests_per_second":round(len(samples)/elapsed,3),"model_calls_delta":model_count()-baseline,
    "samples":samples}
assert report["model_calls_delta"]==0,"Concurrent provider activity invalidates the no-model measurement"
Path("data/reports").mkdir(parents=True,exist_ok=True)
Path("data/reports/query-sql-benchmark-20261005.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k!="samples"},ensure_ascii=False,indent=2))
