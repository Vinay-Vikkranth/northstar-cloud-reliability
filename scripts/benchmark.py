"""Measure an isolated local synthetic workflow. Never contacts a real CRM.

Both baselines are scripted, not observed human task times or customer savings.
The direct protocol reads/compares/writes/verifies; the application includes
durable investigation and a scripted separate-approver decision plus polling.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import html
import ipaddress
import json
import os
from pathlib import Path
import statistics
import time
from urllib.parse import urlsplit
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[1]

def local_url(value: str) -> str:
    parsed=urlsplit(value)
    if parsed.scheme!="http" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"","/"}:
        raise argparse.ArgumentTypeError("Only credential-free localhost HTTP endpoints are permitted")
    try:allowed=ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:allowed=parsed.hostname=="localhost"
    if not allowed:raise argparse.ArgumentTypeError("Benchmark is restricted to isolated loopback simulators")
    return value.rstrip("/")

def stats(values):
    ordered=sorted(values)
    def pct(q):
        pos=(len(ordered)-1)*q;low=int(pos);high=min(low+1,len(ordered)-1)
        return round(ordered[low]+(ordered[high]-ordered[low])*(pos-low),3)
    return {"samples":len(values),"p50_ms":pct(.5),"p95_ms":pct(.95),"mean_ms":round(statistics.mean(values),3),"min_ms":round(min(values),3),"max_ms":round(max(values),3)}

def login(base,record,role):
    client=httpx.Client(base_url=base,timeout=10,follow_redirects=False)
    username=record["users"].get(role,"northstar."+role)
    response=client.post("/api/v1/auth/login",json={"username":username,"password":record["passwords"][role]})
    response.raise_for_status();body=response.json()
    workspace=next(row for row in body["workspaces"] if row["slug"]=="northstar-demo" and row["synthetic"])
    client.headers.update({"X-CSRF-Token":body["csrf_token"],"X-Workspace-Id":workspace["id"]})
    return client

def wait_for(client,path,predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        response=client.get(path);response.raise_for_status();body=response.json()
        if predicate(body):return body
        time.sleep(.05)
    raise TimeoutError("Workflow did not reach expected state; inspect Operations")

def write_chart(report,path):
    names=[("Scripted direct protocol",report["scripted_direct_protocol"]),("Application workflow",report["application_workflow"]),("HTTP health load",report["health_load"])]
    maximum=max(value["p95_ms"] for _,value in names)
    rows=[]
    for index,(name,value) in enumerate(names):
        y=115+index*86
        for j,(key,color) in enumerate((("p50_ms","#1b8985"),("p95_ms","#dfaa56"))):
            width=max(2,value[key]/maximum*410)
            rows.append(f'<rect x="290" y="{y+j*25}" width="{width:.2f}" height="17" rx="3" fill="{color}"/><text x="{300+width:.2f}" y="{y+13+j*25}" fill="#ecf3f8" font-size="12">{value[key]:.1f} ms</text>')
        rows.append(f'<text x="30" y="{y+17}" fill="#ecf3f8" font-size="15">{html.escape(name)}</text>')
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="420" viewBox="0 0 900 420"><rect width="900" height="420" rx="18" fill="#12212e"/><text x="30" y="42" fill="white" font-family="sans-serif" font-size="25">Northstar measured synthetic timings</text><text x="30" y="71" fill="#b5cad4" font-family="sans-serif" font-size="13">'+html.escape(report["measured_at"])+ ' · teal p50 · amber p95</text><g font-family="sans-serif">'+''.join(rows)+'<text x="30" y="397" fill="#b5cad4" font-size="12">Scripted local protocols, not human timings. Application includes queue, approval and polling overhead.</text></g></svg>',encoding="utf-8")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",type=local_url,default="http://127.0.0.1:28601")
    parser.add_argument("--crm-url",type=local_url,default="http://127.0.0.1:28611")
    parser.add_argument("--support-url",type=local_url,default="http://127.0.0.1:28621")
    parser.add_argument("--credentials",type=Path,default=ROOT/"var/local-credentials.json")
    parser.add_argument("--output",type=Path,default=ROOT/"evidence/benchmark.json")
    parser.add_argument("--cases",type=int,default=8)
    parser.add_argument("--requests",type=int,default=100)
    parser.add_argument("--concurrency",type=int,default=4)
    parser.add_argument("--allow-reset",action="store_true",help="Explicitly permit reset of this isolated synthetic demo between benchmark cases")
    args=parser.parse_args()
    if not args.allow_reset:parser.error("Pass --allow-reset only against a dedicated synthetic stack not being used for practice")
    if not 1<=args.cases<=100 or not 5<=args.requests<=10000 or not 1<=args.concurrency<=32:parser.error("Benchmark bounds exceeded")
    record=json.loads(args.credentials.read_text(encoding="utf-8"))
    clients={role:login(args.base_url,record,role) for role in ("operator","approver","admin")}
    admin=clients["admin"];operator=clients["operator"];approver=clients["approver"]
    meta=admin.get("/api/v1/meta").json()
    if not meta.get("synthetic_demo") or meta.get("source_mode")!="synthetic_crm":raise SystemExit("Refusing to benchmark an external CRM or non-synthetic workspace")
    crm_token=os.environ.get("CRM_READ_TOKEN","crm-read-local")
    support_token=os.environ.get("SUPPORT_TOKEN","support-local")
    def reset():
        response=admin.post("/api/v1/demo/reset");response.raise_for_status()
    def account():
        response=httpx.get(args.support_url+"/v1/accounts/sup_alex",headers={"Authorization":"Bearer "+support_token},timeout=5);response.raise_for_status();return response.json()
    direct=[];automated=[];request_times=[];failures=0;writes=[]
    for _ in range(args.cases):
        reset();started=time.perf_counter()
        source=httpx.get(args.crm_url+"/v1/customers/cus_alex",headers={"Authorization":"Bearer "+crm_token},timeout=5);source.raise_for_status();source=source.json()
        destination=account()
        assert source["plan"]=="premium" and destination["plan"]=="basic"
        patched=httpx.patch(args.support_url+"/v1/accounts/sup_alex",headers={"Authorization":"Bearer "+support_token,"Idempotency-Key":"benchmark-"+uuid.uuid4().hex,"If-Match":str(destination["revision"])},json={"plan":source["plan"],"expected_revision":destination["revision"],"source_revision":source["revision"]},timeout=5)
        patched.raise_for_status();verified=account();assert verified["plan"]=="premium" and verified["write_count"]==1
        direct.append((time.perf_counter()-started)*1000)
        reset();started=time.perf_counter()
        operator.post("/api/v1/reconcile").raise_for_status()
        incident=next(row["id"] for row in operator.get("/api/v1/incidents").json()["items"] if row.get("account")=="Alex Rivera" and row.get("state")=="OPEN");path=f"/api/v1/incidents/{incident}"
        operator.post(path+"/investigate").raise_for_status()
        detail=wait_for(operator,path,lambda body:body.get("proposal") is not None)
        proposal=detail["proposal"]
        approver.post(f"/api/v1/proposals/{proposal['id']}/approve",json={"proposal_hash":proposal["proposal_hash"],"reason":"Separate scripted benchmark approver verified both revisions."}).raise_for_status()
        wait_for(operator,path,lambda body:body["state"]=="RESOLVED")
        observed=account();assert observed["plan"]=="premium" and observed["write_count"]==1;writes.append(observed["write_count"])
        automated.append((time.perf_counter()-started)*1000)
    def health(_):
        started=time.perf_counter()
        try:
            response=httpx.get(args.base_url+"/health/ready",timeout=10)
            return (time.perf_counter()-started)*1000,response.status_code==200
        except httpx.HTTPError:return (time.perf_counter()-started)*1000,False
    wall=time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for duration,success in pool.map(health,range(args.requests)):
            request_times.append(duration);failures+=not success
    elapsed=time.perf_counter()-wall
    report={"measured_at":datetime.now(timezone.utc).isoformat(),"data":"fictional Northstar simulator only","cases":args.cases,
        "scripted_direct_protocol":stats(direct),"application_workflow":stats(automated),"health_load":{**stats(request_times),"concurrency":args.concurrency,"failures":failures,"requests_per_second":round(args.requests/elapsed,3)},
        "verified_destination_writes_per_automated_case":writes,
        "operations":operator.get("/api/v1/operations").json(),
        "cost":{"paid_api_calls":0,"cloud_bill_usd":None,"cloud_bill_status":"not deployed or measured","local_electricity_cost_usd":None},
        "methodology":"Both task timings are executed scripts over real localhost HTTP. Direct protocol reads CRM and support, compares, writes once and verifies. Application includes investigation worker, approval by a separately authenticated scripted approver, execution and 50ms status polling. No human think time, labor saving, causal business benefit or production-load claim. Fresh HTTP clients add connection overhead. Health load covers readiness requests, not a sustained production throughput benchmark."}
    # Public evidence contains aggregates, not user names, credentials or job payloads.
    report["operations"].pop("jobs",None)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    write_chart(report,args.output.with_suffix(".svg"))
    print(json.dumps({"output":str(args.output),"cases":args.cases,"health_failures":failures,"application_p95_ms":report["application_workflow"]["p95_ms"]}))
    for client in clients.values():client.close()
    if failures:raise SystemExit(1)

if __name__=="__main__":main()
