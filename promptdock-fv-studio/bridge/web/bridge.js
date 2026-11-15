// PromptDock -> active FreeVideo graph. Does not queue, generate, or modify media files.
import {app} from "../../scripts/app.js";
import {api} from "../../scripts/api.js";

const isGenerate=node=>node?.type==="FreeVideoGenerate" || node?.comfyClass==="FreeVideoGenerate";
const widget=(node,name)=>(node?.widgets||[]).find(w=>w.name===name);
function put(node,name,value) {
  const w=widget(node,name);
  if (!w) throw new Error("Нет настройки FreeVideo: "+name);
  w.value=value;
  w.callback?.(value);
  node.setDirtyCanvas?.(true,true);
}
function currentNode(node,slot) {
  const input=node.inputs?.find(i=>i.name===slot);
  if (!input?.link) return null;
  const link=node.graph?.links?.[input.link];
  return node.graph?.getNodeById?.(link?.origin_id) || null;
}
function loraStack(node) {
  const graph=node.graph, old=currentNode(node,"loras");
  if (old && (old.type==="FreeVideoLoRAStack" || old.comfyClass==="FreeVideoLoRAStack"))
    return old;
  const type=globalThis.LiteGraph?.registered_node_types?.FreeVideoLoRAStack;
  if (!graph || !type) throw new Error("FreeVideo LoRA Stack недоступен. Открой интерфейс FreeVideo.");
  const item=globalThis.LiteGraph.createNode("FreeVideoLoRAStack");
  item.pos=[node.pos[0]-520,node.pos[1]+220];
  graph.add(item);
  const slot=node.inputs?.findIndex(input=>input.name==="loras");
  if (!(slot>=0)) throw new Error("У FreeVideo нет входа LoRA");
  item.connect(0,node,slot);
  return item;
}
async function resolveLoras(rows) {
  // ComfyUI returns registered paths relative to models/loras; a basename can
  // occur in more than one folder. Never silently pick a different adapter.
  const reply=await api.fetchApi("/models/loras",{cache:"no-store"});
  if(!reply.ok) throw new Error("Список LoRA FreeVideo недоступен (HTTP "+reply.status+")");
  const names=await reply.json();
  if(!Array.isArray(names)) throw new Error("Список LoRA повреждён");
  return rows.slice(0,32).map(row=>{
    if(typeof row?.name!=="string" || typeof row.strength!=="number"
      || !Number.isFinite(row.strength) || row.strength===0)
      throw new Error("Некорректные данные LoRA");
    const wanted=row.name.replaceAll("\\","/").toLowerCase();
    const exact=names.filter(name=>typeof name==="string" && name.replaceAll("\\","/").toLowerCase()===wanted);
    if(exact.length===1) return {name:exact[0],strength:row.strength,enabled:true};
    const options=names.filter(name=>typeof name==="string" &&
      name.replaceAll("\\","/").toLowerCase().split("/").at(-1)===wanted.split("/").at(-1));
    if(options.length===1) return {name:options[0],strength:row.strength,enabled:true};
    if(options.length>1)throw new Error("LoRA с одинаковым именем в разных папках: "+row.name);
    throw new Error("В FreeVideo отсутствует LoRA: "+row.name);
  });
}
async function restore(data) {
  const graph=app.graph;
  const node=(graph?._nodes||[]).find(isGenerate);
  if (!node) throw new Error("Сначала открой в FreeVideo окно с генерацией.");
  const payload=data.payload||{};
  // Validate all external values before changing anything in the editor.
  const rows=Array.isArray(payload.loras) ? await resolveLoras(payload.loras) : null;
  const g=payload.geometry;
  const fields=[];
  if(payload.text!==null && typeof payload.text==="string")fields.push("text");
  if(Number.isSafeInteger(payload.seed) && payload.seed>=0) fields.push("seed");
  if(g && typeof g==="object"){
    for(const key of ["width","height","seconds"]){
      if(typeof g[key]==="number" && Number.isFinite(g[key]) && g[key]>0)
        fields.push(key);
    }
  }
  for(const name of fields)if(!widget(node,name))
    throw new Error("У FreeVideo нет настройки "+name);
  if(rows && !globalThis.LiteGraph?.registered_node_types?.FreeVideoLoRAStack
      && !(currentNode(node,"loras")?.type==="FreeVideoLoRAStack"))
    throw new Error("Нужен FreeVideo LoRA Stack, но он не загружен.");
  if(payload.text!==null && typeof payload.text==="string"){
    put(node,"text",payload.text);
    window.dispatchEvent(new CustomEvent("freevideo-reference-prompt",{detail:node.id}));
  }
  if(rows){
    const stack=loraStack(node);
    put(stack,"adapters",JSON.stringify(rows));
    window.dispatchEvent(new CustomEvent("freevideo-loras",{detail:stack.id}));
  }
  if(Number.isSafeInteger(payload.seed) && payload.seed>=0 && payload.seed<=Number.MAX_SAFE_INTEGER)
    put(node,"seed",payload.seed);
  if(g && typeof g==="object"){
    for(const key of ["width","height","seconds"]){
      if(typeof g[key]==="number" && Number.isFinite(g[key]) && g[key]>0) put(node,key,g[key]);
    }
  }
  graph?.change?.();
  app.canvas?.setDirty?.(true,true);
  return "Параметры перенесены в текущий FreeVideo. Генерация НЕ запущена.";
}

let processing=false, lastId=null;
async function poll(){
  if(processing || !document.body) return;
  processing=true;
  try {
    const reply=await api.fetchApi("/promptdock-bridge/pending",{cache:"no-store"});
    if(!reply.ok) return;
    const body=await reply.json();
    const data=body.request;
    if(!body.pending || !data || typeof data.id!=="string" || data.id===lastId) return;
    lastId=data.id;
    let ok=true,message="";
    try{message=await restore(data)}
    catch(e){ok=false;message=String(e?.message||e)}
    const ack=await api.fetchApi("/promptdock-bridge/ack",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:data.id,ok,message})
    });
    if(!ack.ok) lastId=null;
  }catch(_){}
  finally{processing=false}
}
app.registerExtension({name:"PromptDock.FreeVideoBridge",setup(){
  setInterval(poll,1100);
  setTimeout(poll,1600);
}});
