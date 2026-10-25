// Credential-free identity of the actual Pi runtime and resolved dependencies.
import {createHash} from "node:crypto";
import {existsSync,readFileSync,readdirSync,realpathSync,statSync} from "node:fs";
import {createRequire} from "node:module";
import path from "node:path";
import {fileURLToPath,pathToFileURL} from "node:url";

const hash=value=>createHash("sha256").update(value).digest("hex");
const digest=value=>hash(JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0))));

function walk(root,relative="") {
  return readdirSync(path.join(root,relative),{withFileTypes:true}).sort((a,b)=>a.name<b.name?-1:1).flatMap(entry=>{
    if(["node_modules",".git","__pycache__","test","tests","__tests__","docs","examples","benchmark","benchmarks",
        ".github","coverage",".cache"].includes(entry.name)) return [];
    if(/\.(?:ts|mts|cts|map|md|mdx)$/i.test(entry.name) || entry.name===".env" || entry.name.startsWith(".env.")
        || entry.name===".npmrc" || entry.name.endsWith(".private.json")) return [];
    const name=path.join(relative,entry.name),full=path.join(root,name);
    if(entry.isDirectory()) return walk(root,name);
    if(entry.isFile()) return [name];
    if(entry.isSymbolicLink() && statSync(full).isFile()) return [name];
    return [];
  });
}

function packageRoot(name,from) {
  const require=createRequire(path.join(from,"__identity_probe__.cjs"));
  let resolved;
  try {resolved=require.resolve(name);} catch {
    // Some ESM packages expose no require condition. Locate the package through
    // Node's search roots, resolving workspace links before hashing its contents.
    const found=(require.resolve.paths(name)||[]).map(root=>path.join(root,name,"package.json")).find(existsSync);
    if(!found) throw new Error(`PI_IDENTITY_DEPENDENCY_MISSING: ${name}`);
    resolved=found;
  }
  let folder=path.dirname(realpathSync(resolved));
  while(folder!==path.dirname(folder)) {
    const manifest=path.join(folder,"package.json");
    if(existsSync(manifest) && JSON.parse(readFileSync(manifest,"utf8")).name===name) return folder;
    folder=path.dirname(folder);
  }
  throw new Error(`PI_IDENTITY_PACKAGE_ROOT_MISSING: ${name}`);
}

export function measurePiIdentity(runtimePath=fileURLToPath(new URL("./runtime.mjs",import.meta.url))) {
  runtimePath=realpathSync(runtimePath);
  const files={"pi/runtime.mjs":hash(readFileSync(runtimePath))},seen=new Map();
  for(const name of ["server.mjs","identity.mjs"]) {
    const file=path.join(path.dirname(runtimePath),name);
    if(existsSync(file)) files[`pi/${name}`]=hash(readFileSync(file));
  }
  function dependency(name,from,optional=false) {
    let root;
    try {root=packageRoot(name,from);} catch(error) {if(optional)return;throw error;}
    const key=`${name}:${root}`;
    if(seen.has(key)) return;
    const manifest=JSON.parse(readFileSync(path.join(root,"package.json"),"utf8"));
    // Distinct nested versions keep distinct canonical keys; roots never enter
    // the identity so installed and workspace layouts compare identically.
    const versions=[...seen.keys()].filter(value=>value.startsWith(`${name}:`));
    const prefix=versions.length?`${name}@${manifest.version}`:name;
    seen.set(key,prefix);
    for(const relative of walk(root)) {
      const canonical=`dependencies/${prefix}/${relative.split(path.sep).join("/")}`;
      const value=hash(readFileSync(path.join(root,relative)));
      if(files[canonical] && files[canonical]!==value) throw new Error("PI_IDENTITY_DEPENDENCY_COLLISION");
      files[canonical]=value;
    }
    for(const child of Object.keys(manifest.dependencies||{}).sort()) dependency(child,root,child in (manifest.optionalDependencies||{}));
    for(const child of Object.keys(manifest.optionalDependencies||{}).sort()) dependency(child,root,true);
  }
  for(const name of ["@earendil-works/pi-agent-core","@earendil-works/pi-ai"]) dependency(name,path.dirname(runtimePath));
  return {schema:"runtime_file_identity_v1",files,sha256:digest(files)};
}

export function processIdentity(pid=process.pid) {
  if(process.platform!=="linux" || !Number.isInteger(pid) || pid<=0) throw new Error("LINUX_PROCESS_IDENTITY_REQUIRED");
  const stat=readFileSync(`/proc/${pid}/stat`,"utf8");
  const fields=stat.slice(stat.lastIndexOf(")")+2).trim().split(/\s+/);
  return {pid,start_ticks:Number(fields[19]),boot_id:readFileSync("/proc/sys/kernel/random/boot_id","utf8").trim(),
    executable:realpathSync(`/proc/${pid}/exe`)};
}

export function createIdentityObserver() {
  const startup={identity:measurePiIdentity(),process:processIdentity()};
  return ()=>{
    const current={identity:measurePiIdentity(),process:processIdentity()};
    if(JSON.stringify(current)!==JSON.stringify(startup)) throw new Error("PI_RUNTIME_CHANGED_SINCE_STARTUP");
    return current;
  };
}

if(process.argv[1] && import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  const runtime=process.argv[2] || fileURLToPath(new URL("./runtime.mjs",import.meta.url));
  const pid=process.argv[3]===undefined?process.pid:Number(process.argv[3]);
  console.log(JSON.stringify({identity:measurePiIdentity(runtime),process:processIdentity(pid)}));
}
