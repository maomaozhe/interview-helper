import {spawnSync} from "node:child_process";
import {cpSync, existsSync} from "node:fs";
import {fileURLToPath} from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const sourceData=path.join(root,"vendor/pi-model-data");
cpSync(sourceData,path.join(root,"vendor/pi/packages/ai/src/providers/data"),{recursive:true});
for (const name of ["telemetry", "ai", "agent"]) {
  const project = path.join(root, "vendor/pi/packages", name, "tsconfig.build.json");
  const compiler = path.join(root, "node_modules/typescript/bin/tsc");
  const result = spawnSync(process.execPath, [compiler, "-p", project], {cwd: root, stdio: "inherit", windowsHide:true});
  if (result.error) throw result.error;
  if (result.status !== 0) {console.error(`Pi ${name} compilation failed: ${result.signal || result.status}`);process.exit(result.status || 1);}
}
const data = path.join(root, "vendor/pi/packages/ai/src/providers/data");
if (existsSync(data)) cpSync(data, path.join(root, "vendor/pi/packages/ai/dist/providers/data"), {recursive: true});
