'use strict';
const fs=require('node:fs'), path=require('node:path');
const os=require('node:os');
const desktopClientPath=process.env.FREECLAY_DESKTOP_CLIENT||path.join(os.homedir(),'.codex','skills','windows-background-use','scripts','desktop-client.cjs');
if(!fs.existsSync(desktopClientPath))throw new Error('Set FREECLAY_DESKTOP_CLIENT to the WindowsBackgroundUse desktop-client.cjs path.');
const {DesktopClient}=require(desktopClientPath);
const root=path.resolve(__dirname,'..');
const run=process.argv[2]||('gui-worker-'+Date.now());
fs.mkdirSync(path.join(root,'verification'),{recursive:true});
const workerPath=process.env.FREECLAY_DESKTOP_WORKER||path.join(root,'verification','worker','desktop-worker.exe');
if(!fs.existsSync(workerPath))throw new Error('Set FREECLAY_DESKTOP_WORKER to a built WindowsBackgroundUse desktop-worker.exe.');
const output=path.join(root,'verification',run); fs.mkdirSync(output,{recursive:false});
const profile=path.join(output,'profile'); fs.mkdirSync(profile,{recursive:true});
const config={executable:process.env.BLENDER_EXE||'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe',
 arguments:['--factory-startup','--disable-autoexec','--enable-event-simulate','--window-geometry','0','0','1200','850',
 '--python',path.join(root,'tools',process.argv[3]||'test_gui.py'),'--',output],workingDirectory:root,outputDirectory:output,
 appMemoryLimitMiB:4096,dpiAware:true};
// Factory startup and no preference save: the user profile is never written.
const configPath=path.join(output,'config.json'); fs.writeFileSync(configPath,JSON.stringify(config,null,2));
const client=new DesktopClient({hostExecutable:workerPath,configPath,timeoutMs:15000});
(async()=>{
 let stats={};
 try {
  stats.ready=await client.ready; fs.writeFileSync(path.join(output,'worker.json'),JSON.stringify(stats,null,2));
  const result=path.join(output,'result.json');
  const start=Date.now();
  let completed=false;
  while(Date.now()-start<55000){
   await new Promise(r=>setTimeout(r,1000));
   if(fs.existsSync(result)){
    let r;
    try {r=JSON.parse(fs.readFileSync(result,'utf8'));}
    catch(error){
     // Blender may be replacing the progress file at this instant. Reobserve;
     // do not abort and close the owned app while a test is still running.
     if(error instanceof SyntaxError)continue;
     throw error;
    }
    if('passed' in r){console.log(JSON.stringify(r));completed=true;if(!r.passed)process.exitCode=1;break;}
   }
  }
  if(!completed){stats.incomplete='No final test result within 55 seconds';process.exitCode=1;}
  stats.observe=await client.observe();
  stats.stats=await client.request({op:'stats'});
 }catch(error){stats.error=String(error); console.error(error);process.exitCode=1;}
 finally {
  stats.close=await client.close();
  fs.writeFileSync(path.join(output,'worker.json'),JSON.stringify(stats,null,2));
 }
})();
