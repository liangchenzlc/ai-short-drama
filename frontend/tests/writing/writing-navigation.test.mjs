import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const source=readFileSync(new URL('../../src/features/creation/writing/sessions/writing-navigation.ts', import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const {installWritingNavigationGuard,hasUnsettledWriting}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
test('pending recovery stays a discard risk when checking the latest text fails or changes its basis', () => {
  assert.equal(hasUnsettledWriting({ loaded: true, dirty: false, busy: false, status: 'saved', recoverable: true }), true);
  assert.equal(hasUnsettledWriting({ loaded: true, dirty: false, busy: false, status: 'saved', recoveryBlocked: true }), true);
});
function setup(override = {}) {
  let index=2, rendered=2, dirty=true, confirmations=0,replacements=0;const listeners=new Map(),waiting=deferred();
  const emit=(type,event)=>{for(const {fn} of [...(listeners.get(type)??[])].sort((a,b)=>Number(b.capture)-Number(a.capture))){fn(event);if(event.stopped)break;}};
  const browser={
    history:{get state(){return {idx:index};},go(delta){queueMicrotask(()=>{index+=delta;emit('popstate',{state:{idx:index},stopImmediatePropagation(){this.stopped=true;}});});}},
    addEventListener(type,fn,capture=false){listeners.set(type,[...(listeners.get(type)??[]),{fn,capture}]);},
    removeEventListener(type,fn){listeners.set(type,(listeners.get(type)??[]).filter(item=>item.fn!==fn));},
  };
  browser.addEventListener('popstate',event=>{rendered=event.state.idx;});
  const navigator={push(){index++;rendered=index;},replace(){replacements++;rendered=index;}};
  const session={getSnapshot:()=>({dirty,busy:false,status:'unsaved',loaded:true,...override}),async flush(){const ok=await waiting.promise;if(ok)dirty=false;return ok;}};
  const remove=installWritingNavigationGuard(navigator,session,browser,undefined,async()=>{confirmations++;return false;});
  return {browser,navigator,waiting,remove,emit,get index(){return index;},get rendered(){return rendered;},get confirmations(){return confirmations;},get replacements(){return replacements;}};
}
test('initial content loading does not guard the first route normalization',()=>{
  const x=setup({dirty:false,busy:true,status:'loading',loaded:false});
  x.navigator.replace('/projects/1/episodes/2/source');
  assert.equal(x.replacements,1);assert.equal(x.confirmations,0);x.remove();
});
test('assistant view query changes preserve drafts without a navigation save or discard prompt',()=>{
  const x=setup({status:'conflict'});
  x.browser.location={href:'https://fixture.test/projects/1/episodes/2/source',pathname:'/projects/1/episodes/2/source'};
  x.navigator.replace({search:'?assistant=open&conversation=9007199254740993'});
  assert.equal(x.replacements,1);assert.equal(x.confirmations,0);
  x.navigator.push('/projects/1/episodes/2/source?assistant=open');
  assert.equal(x.rendered,3);assert.equal(x.confirmations,0);x.remove();
});
test('internal route push changes view immediately while a save is pending',async()=>{
  const x=setup();x.navigator.push('/next');assert.equal(x.rendered,3);x.waiting.resolve(true);await tick();assert.equal(x.rendered,3);assert.equal(x.confirmations,0);x.remove();
});
test('failed save cannot block internal navigation or request a discard confirmation',async()=>{
  const x=setup();x.navigator.push('/next');x.waiting.resolve(false);await tick();assert.equal(x.rendered,3);assert.equal(x.confirmations,0);x.remove();
});
test('browser back renders its destination without waiting for saves',async()=>{
  const x=setup();x.browser.history.go(-1);await tick();assert.equal(x.index,1);assert.equal(x.rendered,1);
  x.waiting.resolve(true);await tick();assert.equal(x.index,1);assert.equal(x.rendered,1);x.remove();
});
test('failed saving does not reverse browser back; memory-only exits remain protected',async()=>{
  const x=setup();x.browser.history.go(-1);await tick();x.waiting.resolve(false);await tick();assert.equal(x.index,1);assert.equal(x.rendered,1);assert.equal(x.confirmations,0);
  const event={preventDefault(){this.prevented=true;}};x.emit('beforeunload',event);assert.equal(event.prevented,true);x.remove();
});
test('back after a route push is not replayed after an older save finishes',async()=>{
  const x=setup();x.navigator.push('/next');x.browser.history.go(-1);await tick();x.waiting.resolve(true);await tick();assert.equal(x.index,2);assert.equal(x.rendered,2);x.remove();
});
test('uncertain save errors never ask for confirmation during internal navigation',async()=>{
  const x=setup({dirty:false,status:'error'});x.navigator.push('/next');assert.equal(x.rendered,3);
  x.waiting.resolve(false);await tick();assert.equal(x.confirmations,0);assert.equal(x.rendered,3);
  const event={preventDefault(){this.prevented=true;}};x.emit('beforeunload',event);assert.equal(event.prevented,true);x.remove();
});
test('an unsettled storyboard does not turn navigation into a network barrier',async()=>{
  let routed=false,flushed=0;
  const listeners=new Map();
  const browser={history:{state:{idx:0},go(){}},addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(){}};
  const navigator={push(){routed=true;},replace(){}};
  const session={getSnapshot:()=>({dirty:false,busy:false,status:'saved',loaded:true}),async flush(){return true;}};
  let unsettled=true;
  const remove=installWritingNavigationGuard(navigator,session,browser,()=>({hasUnsettled:()=>unsettled,async flush(){flushed++;unsettled=false;return true;}}));
  navigator.push('/storyboard-away');assert.equal(routed,true);await tick();assert.equal(flushed,0);remove();
});

test('reliably persisted local input does not trigger a browser exit warning',()=>{
  const listeners=new Map();
  const browser={addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(type){listeners.delete(type);}};
  const navigator={push(){},replace(){}};
  const session={getSnapshot:()=>({loaded:true,dirty:true,busy:false,status:'error'}),hasMemoryOnlyChanges:()=>false};
  const remove=installWritingNavigationGuard(navigator,session,browser);
  const event={preventDefault(){this.prevented=true;}};listeners.get('beforeunload')(event);
  assert.equal(event.prevented,undefined);remove();assert.equal(listeners.size,0);
});
