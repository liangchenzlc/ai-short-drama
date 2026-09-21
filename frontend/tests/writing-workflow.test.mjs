import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/projects/writing-workflow.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const {projectWritingWorkflow,retainLocalWorkflow}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const local=()=>({novel:'legacy novel',scriptDraft:'legacy script',scriptCandidates:[{id:'demo',value:'demo script'}],approvedScript:{text:'legacy script',aspect:'16:9',style:'old'},aspect:'16:9',style:'new',reviews:{source:'confirmed',script:'confirmed',assets:'review',storyboard:'not_started'},models:{script:'old model'},assets:[],shots:[]});
test('demo view uses current writing text and confirmation instead of stale legacy approvals',()=>{
  const old=local();const view=projectWritingWorkflow(old,{loaded:true,novel:'server novel',script:'server script',confirmed:true});
  assert.equal(view.scriptDraft,'server script');assert.equal(view.novel,'server novel');assert.equal(view.reviews.script,'confirmed');
  assert.deepEqual(view.approvedScript,{text:'server script',aspect:'16:9',style:'new'});assert.deepEqual(view.scriptCandidates,[]);assert.equal(old.scriptDraft,'legacy script');
  const unconfirmed=projectWritingWorkflow(old,{loaded:true,novel:'server novel',script:'edited script',confirmed:false});assert.equal(unconfirmed.approvedScript,null);assert.equal(unconfirmed.reviews.script,'review');
});
test('empty and unloaded server writing never falls back to browser legacy text',()=>{
  for(const loaded of [true,false]){const view=projectWritingWorkflow(local(),{loaded,novel:'',script:'',confirmed:false});assert.equal(view.scriptDraft,'');assert.equal(view.novel,'');assert.equal(view.approvedScript,null);assert.equal(view.reviews.script,'not_started');}
});
test('local demo persistence retains generated shots and model changes without copying projected writing',()=>{
  const old=local();const view=projectWritingWorkflow(old,{loaded:true,novel:'server novel',script:'server script',confirmed:true});
  const next=retainLocalWorkflow(old,{...view,models:{script:'new model'},shots:[{id:'shot',script:view.scriptDraft}],reviews:{...view.reviews,storyboard:'review'}});
  assert.equal(next.shots[0].script,'server script');assert.equal(next.models.script,'new model');assert.equal(next.reviews.storyboard,'review');
  for(const field of ['novel','scriptDraft','scriptCandidates','approvedScript'])assert.deepEqual(next[field],old[field]);
  assert.equal(next.reviews.source,old.reviews.source);assert.equal(next.reviews.script,old.reviews.script);
});
