import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/timeline-view.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const {filmstripCells,zoomScroll,tickInterval,boundZoom,snapFrame,adjacentStart}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
test('boundary snapping uses screen distance and stays precise at wide zoom',()=>{
  assert.equal(snapFrame(88, [0,90,180], 100),90);
  assert.equal(snapFrame(86, [0,90,180], 100),86);
  assert.equal(snapFrame(88, [0,90,180], 600),88);
  assert.equal(snapFrame(81, [0,90,180], .02),81);
  assert.equal(snapFrame(84, [0,90,180], .02),90);
  assert.equal(snapFrame(88, [87,90],100),87);
});
test('clip navigation handles interior positions, exact cuts and track ends',()=>{
  const starts=[0,90,180];
  assert.equal(adjacentStart(starts,100,-1),90);
  assert.equal(adjacentStart(starts,90,-1),0);
  assert.equal(adjacentStart(starts,90,1),180);
  assert.equal(adjacentStart(starts,0,-1),undefined);
  assert.equal(adjacentStart(starts,200,1),undefined);
  assert.equal(adjacentStart([],0,1),undefined);
});
test('filmstrip shows source times after a split and only renders visible cells',()=>{
  const cells=filmstripCells(900,20,20,300,2000,6000,1000,12,100);
  assert.deepEqual(cells,[{x:0,index:2},{x:100,index:3},{x:200,index:3}]);
  assert.deepEqual(filmstripCells(900,10000,0,800,0,9000,1000,9,100),[]);
  assert.ok(filmstripCells(1000000,0,900000,1600,0,3600000,150000,24,96).length<=19);
  assert.deepEqual(filmstripCells(900,0,0,800,0,9000,0,9,100),[]);
});
test('zoom retains the playhead screen position and brings an offscreen position back',()=>{
  const left=zoomScroll(5,100,150,200,800);
  assert.equal(20+5*150-left,20+5*100-200);
  const far=zoomScroll(500,100,150,0,800);
  assert.equal(20+500*150-far,400);
  assert.equal(zoomScroll(0,100,150,0,800),0);
});
test('ruler intervals stay legible at frame zoom and hour overview',()=>{
  for(const zoom of [.02,.1,1,10,100,600]) assert.ok(tickInterval(zoom)*zoom>=72);
  assert.ok(tickInterval(600)<1);
  assert.equal(boundZoom(10000),600);
  assert.equal(boundZoom(0),.02);
});
