(function(root){
  "use strict";
  // A provider/SSE packet can contain many characters. Pace its presentation
  // independently of packet boundaries, keeping the checked final answer authoritative.
  function createAnswerStream({render,schedule=setTimeout,cancel=clearTimeout,intervalMs=8}) {
    let visible="",target="",queue=[],head=0,timer=null,closing=false,stopped=false,resolveFinished;
    const finished=new Promise(resolve=>{resolveFinished=resolve;});
    const settle=()=>{if(closing || stopped)resolveFinished(visible);};
    const tick=()=>{
      timer=null;
      if(stopped)return;
      if(head<queue.length){visible+=queue[head++];render(visible);}
      if(head<queue.length)timer=schedule(tick,intervalMs);
      else{queue=[];head=0;settle();}
    };
    const start=()=>{if(timer===null && head<queue.length)timer=schedule(tick,intervalMs);};
    function replace(text){
      target=String(text ?? "");
      if(!target.startsWith(visible)){visible="";render(visible);}
      queue=Array.from(target.slice(visible.length));head=0;
      if(!queue.length){if(timer!==null)cancel(timer);timer=null;settle();}
      else start();
    }
    return {
      append(delta){
        if(closing || stopped || !delta)return;
        target+=String(delta);queue.push(...Array.from(String(delta)));start();
      },
      setText(text){if(!closing && !stopped)replace(text);},
      reset(text=""){
        if(closing || stopped)return;
        if(timer!==null)cancel(timer);timer=null;
        visible="";target="";queue=[];head=0;render(visible);replace(text);
      },
      finish(finalText=target){
        if(stopped || closing)return finished;
        closing=true;replace(finalText);return finished;
      },
      stop(){
        stopped=true;if(timer!==null)cancel(timer);timer=null;queue=[];head=0;settle();return visible;
      },
      get text(){return visible;},
      get pending(){return queue.length-head;}
    };
  }
  const api={createAnswerStream};
  if(typeof module!=="undefined" && module.exports)module.exports=api;
  else root.InterviewAnswerStream=api;
})(typeof window!=="undefined" ? window : globalThis);
