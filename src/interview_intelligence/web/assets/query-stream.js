(function(root){
  "use strict";
  function queryWatchdogMs(snapshot,now=Date.now()) {
    const lease=Date.parse(snapshot?.lease_until);
    // The durable lease includes the host deadline plus 10 seconds. Leave
    // another 20 seconds for transport and always inspect committed state.
    return Number.isFinite(lease) ? Math.max(20000,lease-now+20000) : 630000;
  }
  // Reconnect to the committed journal for the same run. Never submit a message here.
  function watchQueryRun({url,snapshot,readRun,onEvent=()=>{},signal,cancel=()=>{},
    EventSourceClass=root.EventSource,timeoutMs=queryWatchdogMs(snapshot),pollMs=750,statusPollMs=8000,statusReadTimeoutMs=12000}) {
    return new Promise((resolve,reject)=>{
      let cursor=snapshot.event_cursor || 0,ended=false,source=null,polling=false;
      let statusTimer=null,statusChecking=false,statusPollingStalled=false,deadlineChecking=false,readQueue=Promise.resolve();
      const cleanup=()=>{ended=true;source?.close();clearTimeout(deadline);clearTimeout(statusTimer);
        signal?.removeEventListener("abort",abort);};
      const fail=(code,partial)=>{cleanup();const messages={
        QUERY_RELEVANCE_UNAVAILABLE:"本次语义核验未完成，结果未作为成功查询保存，请重试。",
        WORKSET_BATCH_OUTCOME_UNAVAILABLE:"本批核验结果未能保存，原范围已保留，请重新查询。",
        SEMANTIC_SCOPE_INCOMPLETE:"当前范围的全量算法核验尚未完成，暂时无法提供完整列表。"};
        const error=new Error(messages[code] || code || "查询未完成");error.code=code;error.partial=partial;reject(error);};
      const abort=()=>{cleanup();cancel();reject(new DOMException("查询已取消","AbortError"));};
      // Serialize journal reads across the SSE health check, recovery, and
      // deadline check. A stalled request cannot block the queue indefinitely.
      const readCommitted=()=>{
        const next=readQueue.catch(()=>{}).then(()=>{
          if(ended)return null;
          let timer;
          const timedOut=new Promise((_,reject)=>{timer=setTimeout(()=>{
            const error=new Error("查询状态读取超时，请重试。");error.code="RUN_STATUS_TIMEOUT";reject(error);
          },statusReadTimeoutMs);});
          return Promise.race([Promise.resolve().then(()=>readRun(cursor)),timedOut])
            .finally(()=>clearTimeout(timer));
        });
        readQueue=next;
        return next;
      };
      // The server may use its full configured query deadline. Check the
      // committed result before cancelling a run whose SSE completion was late.
      const deadline=setTimeout(async()=>{
        if(ended)return;
        deadlineChecking=true;clearTimeout(statusTimer);statusTimer=null;
        source?.close();
        try{
          while(!ended){
            const run=await readCommitted();
            if(ended)return;
            if(inspect(run))return;
            if(cursor<run.last_sequence)continue;
            break;
          }
        }catch(error){if(!ended){cleanup();reject(error);}return;}
        if(!ended){cleanup();cancel();reject(new Error("查询超时，请重试。"));}
      },timeoutMs);
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
      const scheduleStatusCheck=()=>{
        if(!ended && !polling && !deadlineChecking && !statusChecking && !statusPollingStalled && statusTimer===null)
          statusTimer=setTimeout(checkPersisted,statusPollMs);
      };
      const checkPersisted=async()=>{
        statusTimer=null;
        if(ended || polling || deadlineChecking)return;
        statusChecking=true;
        try{
          while(!ended && !polling && !deadlineChecking){
            const run=await readCommitted();
            if(ended || polling || deadlineChecking)return;
            if(inspect(run) || cursor>=run.last_sequence)return;
          }
        }catch(error){
          // A timed-out fetch cannot be aborted through the current readRun
          // callback. Avoid piling up more requests while SSE is still open.
          if(error?.code==="RUN_STATUS_TIMEOUT")statusPollingStalled=true;
        }
        finally{statusChecking=false;scheduleStatusCheck();}
      };
      const recover=async()=>{
        if(ended || polling)return;
        polling=true;clearTimeout(statusTimer);statusTimer=null;source?.close();
        onEvent({type:"connection",state:"recovering"});
        try{
          while(!ended && !deadlineChecking){
            const run=await readCommitted();
            if(ended || deadlineChecking)return;
            if(inspect(run))return;
            if(cursor < run.last_sequence)continue;
            await new Promise(done=>setTimeout(done,pollMs));
          }
        }catch(error){if(!ended && !deadlineChecking){cleanup();reject(error);}}
        finally{polling=false;}
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
        scheduleStatusCheck();
      }catch(error){cleanup();reject(error);}
    });
  }
  const api={watchQueryRun,queryWatchdogMs};
  if(typeof module!=="undefined" && module.exports)module.exports=api;
  else root.InterviewQueryStream=api;
})(typeof window!=="undefined" ? window : globalThis);
