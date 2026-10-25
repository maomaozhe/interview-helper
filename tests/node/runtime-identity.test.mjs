import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from "node:fs";
import path from "node:path";
import os from "node:os";
import {measurePiIdentity,processIdentity} from "../../services/pi-agent/identity.mjs";

test("Pi identity binds runtime and resolved dependency contents independent of install root",()=>{
  const temporary=mkdtempSync(path.join(os.tmpdir(),"pi-identity-"));
  try {
    const roots=["first","second"].map(name=>path.join(temporary,name));
    function fixture(root) {
      const runtime=path.join(root,"services","pi-agent","runtime.mjs");
      mkdirSync(path.dirname(runtime),{recursive:true});
      writeFileSync(runtime,"// frozen business runtime\n");
      for(const name of ["@earendil-works/pi-agent-core","@earendil-works/pi-ai"]) {
        const dependency=path.join(root,"node_modules",name);
        mkdirSync(dependency,{recursive:true});
        writeFileSync(path.join(dependency,"package.json"),JSON.stringify({name,version:"1.0.2",main:"index.js"}));
        writeFileSync(path.join(dependency,"index.js"),"module.exports = {};\n");
      }
      return runtime;
    }
    const [left,right]=roots.map(fixture);
    assert.deepEqual(measurePiIdentity(left),measurePiIdentity(right));
    writeFileSync(path.join(roots[1],"node_modules","@earendil-works","pi-ai","index.js"),"module.exports = {changed:true};\n");
    assert.notEqual(measurePiIdentity(left).sha256,measurePiIdentity(right).sha256);
    writeFileSync(left,"// changed runtime\n");
    assert.notEqual(measurePiIdentity(left).files["pi/runtime.mjs"],measurePiIdentity(right).files["pi/runtime.mjs"]);
  } finally {rmSync(temporary,{recursive:true,force:true});}
});

test("Pi identity fails when a required runtime dependency cannot be resolved",()=>{
  const temporary=mkdtempSync(path.join(os.tmpdir(),"pi-identity-missing-"));
  try {
    const runtime=path.join(temporary,"runtime.mjs");
    writeFileSync(runtime,"// frozen runtime");
    assert.throws(()=>measurePiIdentity(runtime),/PI_IDENTITY_DEPENDENCY_MISSING/);
  } finally {rmSync(temporary,{recursive:true,force:true});}
});

test("Linux process identity has kernel start time and executable without argv",{skip:process.platform!=="linux"},()=>{
  const observed=processIdentity();
  assert.equal(observed.pid,process.pid);
  assert.ok(observed.start_ticks>0 && observed.boot_id && observed.executable.startsWith("/"));
  assert.equal(Object.keys(observed).length,4);
});
