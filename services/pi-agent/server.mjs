import http from "node:http";
import {timingSafeEqual} from "node:crypto";
import {runQuery} from "./runtime.mjs";

const token=process.env.INTERNAL_AGENT_TOKEN;
if(!token) throw new Error("INTERNAL_AGENT_TOKEN is required");
const hostUrl=process.env.AGENT_HOST_URL || "http://api:8000";
function send(res,status,body){res.writeHead(status,{"Content-Type":"application/json"});res.end(JSON.stringify(body));}
const server=http.createServer(async(req,res)=>{
  if(req.url==="/health" && req.method==="GET") return send(res,200,{status:"ready",pi_version:"1.0.2"});
  const supplied=Buffer.from(req.headers.authorization || ""), expected=Buffer.from(`Bearer ${token}`);
  if(supplied.length!==expected.length || !timingSafeEqual(supplied,expected)) return send(res,401,{error:"unauthorized"});
  if(req.url!=="/run" || req.method!=="POST") return send(res,404,{error:"not_found"});
  const controller=new AbortController();
  res.on("close",()=>{if(!res.writableEnded)controller.abort();});
  let timer;
  try {
    const chunks=[];let size=0;
    for await(const chunk of req){size+=chunk.length;if(size>512_000)throw new Error("CONTEXT_TOO_LARGE");chunks.push(chunk);}
    const input=JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const timeout=Math.max(1,Math.min(180_000,input.timeout_ms || 60_000));
    timer=setTimeout(()=>controller.abort(),timeout);
    const result=await runQuery(input,{hostUrl,token,signal:controller.signal,onEvent:async event=>{
      const result=await fetch(`${hostUrl.replace(/\/$/,"")}/internal/agent/runs/${encodeURIComponent(input.run_id)}/events`,{
        method:"POST",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
        body:JSON.stringify(event),signal:controller.signal});
      if(!result.ok) throw new Error("QUERY_EVENT_PERSISTENCE_FAILED");
    }});
    send(res,200,result);
  } catch(error){if(!res.destroyed)send(res,503,{error:{code:"PI_QUERY_FAILED",message:String(error.message).slice(0,500)}});}
  finally{clearTimeout(timer);}
});
server.listen(Number(process.env.PORT || 8787),process.env.HOST || "0.0.0.0");
