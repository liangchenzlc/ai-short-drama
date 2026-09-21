import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/projects/writing-navigation.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const {installWritingNavigationGuard}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function setup(override = {}) {
  let index=2, rendered=2, dirty=true, confirmations=0,replacements=0;const listeners=new Map(),waiting=deferred();
  const emit=(type,event)=>{for(const {fn} of [...(listeners.get(type)??[])].sort((a,b)=>Number(b.capture)-Number(a.capture))){fn(event);if(event.stopped)break;}};
  const browser={
    history:{get state(){return {idx:index};},go(delta){queueMicrotask(()=>{index+=delta;emit('popstate',{state:{idx:index},stopImmediatePropagation(){this.stopped=true;}});});}},
    confirm(){confirmations++;return false;},
    addEventListener(type,fn,capture=false){listeners.set(type,[...(listeners.get(type)??[]),{fn,capture}]);},
    removeEventListener(type,fn){listeners.set(type,(listeners.get(type)??[]).filter(item=>item.fn!==fn));},
  };
  browser.addEventListener('popstate',event=>{rendered=event.state.idx;});
  const navigator={push(){index++;rendered=index;},replace(){replacements++;rendered=index;}};
  const session={getSnapshot:()=>({dirty,busy:false,status:'unsaved',loaded:true,...override}),async flush(){const ok=await waiting.promise;if(ok)dirty=false;return ok;}};
  const remove=installWritingNavigationGuard(navigator,session,browser);
  return {browser,navigator,waiting,remove,emit,get index(){return index;},get rendered(){return rendered;},get confirmations(){return confirmations;},get replacements(){return replacements;}};
}
test('initial content loading does not guard the first route normalization',()=>{
  const x=setup({dirty:false,busy:true,status:'loading',loaded:false});
  x.navigator.replace('/projects/1/episodes/2/source');
  assert.equal(x.replacements,1);assert.equal(x.confirmations,0);x.remove();
});
test('internal route push waits for successful flush before changing route',async()=>{
  const x=setup();x.navigator.push('/next');assert.equal(x.rendered,2);x.waiting.resolve(true);await tick();assert.equal(x.rendered,3);assert.equal(x.confirmations,0);x.remove();
});
test('failed save and declined discard preserves current route',async()=>{
  const x=setup();x.navigator.push('/next');x.waiting.resolve(false);await tick();assert.equal(x.rendered,2);assert.equal(x.confirmations,1);x.remove();
});
test('browser back is restored without rendering destination until saves finish',async()=>{
  const x=setup();x.browser.history.go(-1);await tick();assert.equal(x.index,2);assert.equal(x.rendered,2);
  x.waiting.resolve(true);await tick();assert.equal(x.index,1);assert.equal(x.rendered,1);x.remove();
});
test('cancelled browser back preserves route and unload warns about dirty draft',async()=>{
  const x=setup();x.browser.history.go(-1);await tick();x.waiting.resolve(false);await tick();assert.equal(x.index,2);assert.equal(x.rendered,2);
  const event={preventDefault(){this.prevented=true;}};x.emit('beforeunload',event);assert.equal(event.prevented,true);x.remove();
});
test('back during a pending route push cannot start a second navigation',async()=>{
  const x=setup();x.navigator.push('/next');x.browser.history.go(-1);await tick();x.waiting.resolve(true);await tick();assert.equal(x.index,3);assert.equal(x.rendered,3);x.remove();
});
test('unresolved save error guards navigation and unload even when draft equals stale server snapshot',async()=>{
  const x=setup({dirty:false,status:'error'});x.navigator.push('/next');assert.equal(x.rendered,2);
  x.waiting.resolve(false);await tick();assert.equal(x.confirmations,1);assert.equal(x.rendered,2);
  const event={preventDefault(){this.prevented=true;}};x.emit('beforeunload',event);assert.equal(event.prevented,true);x.remove();
});
test('storyboard barrier joins internal navigation after writing is already saved',async()=>{
  let routed=false,flushed=0;
  const listeners=new Map();
  const browser={history:{state:{idx:0},go(){}},confirm(){return false;},addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(){}};
  const navigator={push(){routed=true;},replace(){}};
  const session={getSnapshot:()=>({dirty:false,busy:false,status:'saved',loaded:true}),async flush(){return true;}};
  let unsettled=true;
  const remove=installWritingNavigationGuard(navigator,session,browser,()=>({hasUnsettled:()=>unsettled,async flush(){flushed++;unsettled=false;return true;}}));
  navigator.push('/storyboard-away');assert.equal(routed,false);await tick();assert.equal(flushed,1);assert.equal(routed,true);remove();
});
