(function(root){
  "use strict";
  // Reconnect to the committed journal for the same run. Never submit a message here.
  function watchQueryRun({url,snapshot,readRun,onEvent=()=>{},signal,cancel=()=>{},
    EventSourceClass=root.EventSource,timeoutMs=110000,pollMs=750}) {
    return new Promise((resolve,reject)=>{
      let cursor=snapshot.event_cursor || 0,ended=false,source=null,polling=false;
      const cleanup=()=>{ended=true;source?.close();clearTimeout(deadline);signal?.removeEventListener("abort",abort);};
      const fail=(code,partial)=>{cleanup();const error=new Error(code || "查询未完成");error.partial=partial;reject(error);};
      const abort=()=>{cleanup();cancel();reject(new DOMException("查询已取消","AbortError"));};
      const deadline=setTimeout(()=>{cleanup();cancel();reject(new Error("查询超时，请重试。"));},timeoutMs);
      signal?.addEventListener("abort",abort,{once:true});
      const consume=(type,data,sequence)=>{
        if(ended || (Number.isInteger(sequence) && sequence<=cursor))return;
        if(Number.isInteger(sequence))cursor=sequence;
        if(["accepted","stage","clarification_delta","answer_delta","tool_result"].includes(type))onEvent({type,...data});
        if(type==="completed"){cleanup();resolve(data.result);}
        if(["failed","interrupted"].includes(type))return false;
      };
      const inspect=run=>{
        for(const event of run.events || [])consume(event.type,event.data,event.sequence);
        if(ended)return true;
        // A page is at most 200 journal events. Drain every page before deciding
        // the run ended, so recovery cannot skip a final stage or question.
        if(cursor < run.last_sequence)return false;
        if(run.status==="SUCCEEDED"){cleanup();resolve(run.result);return true;}
        if(run.status!=="RUNNING"){fail(run.error_code,run.partial_result);return true;}
        return false;
      };
      const recover=async()=>{
        if(ended || polling)return;
        polling=true;source?.close();
        onEvent({type:"connection",state:"recovering"});
        try{
          while(!ended){
            const run=await readRun(cursor);
            if(ended)return;
            if(inspect(run))return;
            if(cursor < run.last_sequence)continue;
            await new Promise(done=>setTimeout(done,pollMs));
          }
        }catch(error){if(!ended){cleanup();reject(error);}}
      };
      if(signal?.aborted){abort();return;}
      try{
        if(inspect(snapshot))return;
        if(!EventSourceClass){recover();return;}
        const address=new URL(url);address.searchParams.set("after",cursor);
        source=new EventSourceClass(address);
        for(const type of ["accepted","stage","clarification_delta","answer_delta","tool_result","completed","failed","interrupted",
          "model_request","model_result","text_delta","pi_event"]){
          source.addEventListener(type,event=>{
            if(ended)return;
            try{
              const sequence=Number(event.lastEventId);
              const outcome=consume(type,JSON.parse(event.data),event.lastEventId && Number.isInteger(sequence) ? sequence : undefined);
              if(outcome===false)recover();
            }catch(error){cleanup();reject(error);}
          });
        }
        source.onerror=recover;
      }catch(error){cleanup();reject(error);}
    });
  }
  const api={watchQueryRun};
  if(typeof module!=="undefined" && module.exports)module.exports=api;
  else root.InterviewQueryStream=api;
})(typeof window!=="undefined" ? window : globalThis);
