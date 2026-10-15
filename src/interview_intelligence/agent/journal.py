"""Committed query events and tool intentions, independent of live Agent objects."""
import hashlib
import json
from sqlalchemy import select,or_,func
from interview_intelligence.domain.models import AgentTurn, AgentEvent, ToolInvocation, now_utc
from interview_intelligence.agent.preferences import aware

class QueryJournal:
    def __init__(self,database,user_id):
        self.database,self.user_id=database,user_id
    def _turn(self,s,run_id):
        t=s.get(AgentTurn,run_id,with_for_update=True)
        if not t or t.user_id!=self.user_id: raise KeyError("RUN_NOT_FOUND")
        return t
    @staticmethod
    def append(s,t,kind,payload):
        t.event_sequence+=1
        s.add(AgentEvent(run_id=t.id,sequence=t.event_sequence,kind=kind,payload=payload))
    def event(self,run_id,kind,payload):
        with self.database.session() as s,s.begin():
            t=self._turn(s,run_id)
            self.append(s,t,kind,payload)
            # Pi can reject invalid/blocked calls before the host executor runs.
            # Preserve that failed attempt, without duplicating host tool errors.
            if kind=="pi_event" and payload.get("type")=="tool_execution_end" and payload.get("isError"):
                start=s.scalar(select(AgentEvent).where(AgentEvent.run_id==run_id,
                    AgentEvent.kind=="pi_event",AgentEvent.payload["type"].as_string()=="tool_execution_start",
                    AgentEvent.payload["toolCallId"].as_string()==payload.get("toolCallId")).order_by(AgentEvent.sequence.desc()))
                if start:
                    previous=s.scalar(select(AgentEvent.id).where(AgentEvent.run_id==run_id,
                        AgentEvent.sequence>start.sequence,AgentEvent.kind=="tool_error",
                        AgentEvent.payload["tool"].as_string()==payload.get("toolName")))
                    if not previous:
                        self.append(s,t,"tool_error",{"tool":payload.get("toolName"),"action_id":None,
                            "tool_call_id":payload.get("toolCallId"),"arguments":start.payload.get("args"),
                            "error_code":payload.get("error") or "PI_TOOL_FAILED",
                            "status":"CANCELLED" if t.cancel_requested else "FAILED","source":"pi"})
    def recover(self):
        with self.database.session() as s,s.begin():
            expired=list(s.scalars(select(AgentTurn).where(AgentTurn.status=="RUNNING",
                or_(AgentTurn.lease_until.is_(None),AgentTurn.lease_until<=now_utc())).with_for_update()))
            for t in expired:
                if t.lease_until and aware(t.lease_until)>now_utc(): continue
                t.status,t.error_code="INTERRUPTED","QUERY_INTERRUPTED"
                t.finished_at=now_utc()
                self.close_tools(s,t,"INTERRUPTED",t.error_code)
                self.append(s,t,"interrupted",{"code":t.error_code})
    def view(self,run_id,after=0):
        self.recover()
        with self.database.session() as s:
            t=self._turn(s,run_id)
            events=[{"sequence":e.sequence,"type":e.kind,"data":e.payload}
                for e in s.scalars(select(AgentEvent).where(AgentEvent.run_id==t.id,
                    AgentEvent.sequence>after).order_by(AgentEvent.sequence).limit(200))]
            return {"run_id":t.id,"request_id":t.request_id,"conversation_id":t.conversation_id,
                "status":t.status,"error_code":t.error_code,"cancel_requested":t.cancel_requested,
                "result":t.response if t.status=="SUCCEEDED" else None,
                "partial_result":t.response if t.status!="SUCCEEDED" and t.response else None,
                "events":events,"last_sequence":t.event_sequence,
                "event_cursor":max(0,(s.scalar(select(func.max(AgentEvent.sequence)).where(
                    AgentEvent.run_id==t.id,AgentEvent.kind=="accepted")) or 1)-1)}
    def prepare(self,run,ordinal,name,plan):
        args=plan.model_dump(mode="json")
        digest=hashlib.sha256(json.dumps(args,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.database.session() as s,s.begin():
            t=self._turn(s,run.id)
            if t.status!="RUNNING" or t.owner_id!=run.owner_id or t.cancel_requested:
                raise ValueError("QUERY_CANCELLED")
            invocation=s.scalar(select(ToolInvocation).where(ToolInvocation.run_id==run.id,
                ToolInvocation.ordinal==ordinal))
            if invocation:
                if invocation.payload_hash!=digest or invocation.tool_name!=name:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return invocation.id,invocation.result if invocation.status=="COMPLETED" else None
            invocation=ToolInvocation(run_id=run.id,ordinal=ordinal,tool_name=name,payload_hash=digest,
                arguments=args,status="STARTED",result={})
            s.add(invocation);s.flush()
            self.append(s,t,"stage",{"stage":"tool","tool":name,"action":plan.action,
                                     "ordinal":ordinal,"action_id":invocation.id})
            return invocation.id,None
    def complete(self,run,invocation_id):
        with self.database.session() as s,s.begin():
            t=self._turn(s,run.id)
            invocation=s.get(ToolInvocation,invocation_id)
            invocation.status,invocation.result="COMPLETED",run.result
            t.response,t.state_after=run.result,run.state
            self.append(s,t,"tool_result",{"tool":invocation.tool_name,"ordinal":invocation.ordinal,
                "action_id":invocation.id,"result":run.result})
    def fail_tool(self,run,name,plan,error):
        with self.database.session() as s,s.begin():
            t=self._turn(s,run.id)
            if t.status!="RUNNING":
                # A cancelled worker thread can unwind after the run is closed.
                # Its in-flight tool already has a durable terminal outcome.
                return
            invocation=s.scalar(select(ToolInvocation).where(ToolInvocation.run_id==run.id,
                ToolInvocation.ordinal==run.tool_calls,ToolInvocation.tool_name==name,
                ToolInvocation.status=="STARTED"))
            code=str(error or "QUERY_TOOL_FAILED")[:128]
            cancelled=code=="QUERY_CANCELLED" or type(error).__name__=="CancelledError"
            if invocation:
                invocation.status="CANCELLED" if cancelled else "FAILED"
                invocation.result={"error_code":code}
            self.append(s,t,"tool_error",{"tool":name,"ordinal":run.tool_calls,
                "action_id":invocation.id if invocation else None,"error_code":code,
                "status":"CANCELLED" if cancelled else "FAILED",
                "arguments":plan.model_dump(mode="json")})
    @classmethod
    def close_tools(cls,s,t,status,code):
        for invocation in s.scalars(select(ToolInvocation).where(
                ToolInvocation.run_id==t.id,ToolInvocation.status=="STARTED")):
            invocation.status=status
            invocation.result={"error_code":code}
            cls.append(s,t,"tool_error",{"tool":invocation.tool_name,"ordinal":invocation.ordinal,
                "action_id":invocation.id,"error_code":code,"status":status})
    def resume(self,run_id):
        with self.database.session() as s:
            t=self._turn(s,run_id)
            inv=list(s.scalars(select(ToolInvocation).where(ToolInvocation.run_id==run_id).order_by(ToolInvocation.ordinal)))
            writes=[i for i in inv if i.tool_name=="record_review"]
            return {"result":t.response,"state":t.state_after,"write":writes[-1].arguments if writes else None,
                    "parts":[i.result for i in inv if i.status=="COMPLETED"],
                    "write_ordinal":writes[-1].ordinal if writes else None,
                    "write_done":bool(writes and writes[-1].status=="COMPLETED"),
                    "ordinal":inv[-1].ordinal if inv else 0}
    def cancel(self,run_id):
        with self.database.session() as s,s.begin():
            t=self._turn(s,run_id)
            if t.status=="RUNNING":
                t.cancel_requested=True
                self.append(s,t,"stage",{"stage":"cancel_requested"})
        return self.view(run_id)
    def request_view(self,request_id):
        with self.database.session() as s:
            t=s.scalar(select(AgentTurn).where(AgentTurn.user_id==self.user_id,AgentTurn.request_id==request_id))
            if not t: raise KeyError("REQUEST_NOT_FOUND")
            run_id=t.id
        return self.view(run_id)
    def is_cancelled(self,run_id):
        with self.database.session() as s:
            return self._turn(s,run_id).cancel_requested
