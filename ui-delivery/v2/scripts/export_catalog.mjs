import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {screens,reviewStates,statesFor} from '../src/catalog.js';
const directory=fileURLToPath(new URL('../../../.ui-work/',import.meta.url));
await mkdir(directory,{recursive:true});
await writeFile(directory+'ui2-catalog.json',JSON.stringify({screens:screens.map(screen=>({...screen,states:statesFor(screen.key)})),states:reviewStates},null,2));
console.log(JSON.stringify({screens:screens.length,states:screens.reduce((total,screen)=>total+statesFor(screen.key).length,0)}));
