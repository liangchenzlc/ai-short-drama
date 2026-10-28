import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function moduleUrl(file, imports = {}) {
  let code = ts.transpileModule(readFileSync(new URL(`../../src/${file}.ts`, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  for (const [name, url] of Object.entries(imports)) code = code.replaceAll(`'${name}'`, `'${url}'`);
  return `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
}
const editingUrl = moduleUrl('features/creation/assembly/sessions/assembly-editing');
const documentUrl = moduleUrl('features/creation/assembly/sessions/assembly-document', { '@host/features/creation/assembly/sessions/assembly-editing': editingUrl });
const { followClips, normalizeSound, emptySound, sourceAudio, splitAudio, resegmentSubtitles, assemblyOutputAspect, aspectRatio } = await import(documentUrl);
const { splitClip, moveClip, buildTimeline, toMs } = await import(editingUrl);
const { audioGain, audioSourceTime, frameGeometry, waveformSlice } = await import(moduleUrl('features/creation/assembly/player/assembly-playback'));
const { parseSrt, exportSrt, splitSubtitle } = await import(moduleUrl('features/creation/assembly/subtitles/assembly-subtitles'));
const creationStub = `data:text/javascript;base64,${Buffer.from("export const isStoredDraft = v => v?.format === 1 && typeof v.base_version === 'string' && /^\\d+$/.test(v.base_version) && 'document' in v;").toString('base64')}`;
const { assemblyVersion, assemblyDraft, restoreAssemblyDraft, validAssemblyDraft } = await import(moduleUrl('features/creation/assembly/sessions/assembly-recovery', { '@host/features/creation/shared/recovery/creation-recovery': creationStub, '@host/features/creation/assembly/sessions/assembly-document': documentUrl }));
const clip = { id: 'a', media_id: '101', shot_id: '10', included: true, position: 1, trim_in_ms: 0, trim_out_ms: 6000, duration_ms: 6000, has_audio: true, muted: false, script: 'A' };
const speech = { id: 'caption', text: '你好，世界。', start_ms: 1000, end_ms: 5000, anchor_clip_id: 'a', anchor_source_in_ms: 1000 };

test('splitting and rearranging pictures follows linked sound and captions without moving fixed music', () => {
  const doc = normalizeSound({ ...emptySound(), schema_version: 1, subtitles: [speech] }, [clip]);
  const music = { ...sourceAudio(clip, 0, 6000), id: 'music', kind: 'music', anchor_clip_id: null };
  doc.audio_clips.push(music);
  const split = splitClip([clip], 'a', 90, 'b');
  const followed = followClips([clip], split, doc);
  assert.deepEqual(followed.subtitles.map(s => [s.start_ms, s.end_ms, s.anchor_clip_id]), [[1000, 3000, 'a'], [3000, 5000, 'b']]);
  assert.equal(followed.audio_clips.filter(c => c.kind === 'original').length, 2);
  const reordered = followClips(split, moveClip(split, 'b', 0), followed);
  assert.deepEqual(reordered.subtitles.map(s => [s.start_ms, s.end_ms]), [[0, 2000], [4000, 6000]]);
  assert.equal(reordered.audio_clips.find(c => c.id === 'music').start_ms, 0);
  assert.equal(followClips(split, [], followed).audio_clips.length, 1);
});
test('second splits keep their lineage and do not associate a duplicate source with an earlier clip', () => {
  const first = splitClip([clip], 'a', 60, 'b'), doc = followClips([clip], first, normalizeSound(undefined, [clip]));
  const second = splitClip(first, 'b', 100, 'c'), again = followClips(first, second, doc);
  const lengths = again.audio_clips.map(c => c.duration_ms);
  assert.equal(lengths.reduce((a, b) => a + b, 0), 6000);
  assert.equal(again.audio_clips.length, 3);
  const duplicate = { ...clip, id: 'd', source_clip_id: 'a', position: 4 };
  const added = followClips(second, [...second, duplicate], again);
  assert.equal(added.audio_clips.length, 4);
  assert.equal(added.audio_clips.find(c => c.anchor_clip_id === 'd').start_ms, 6000);
});
test('replacement updates the linked original; images remove original sound; detached sound survives removal', () => {
  const doc = normalizeSound(undefined, [clip]);
  const changed = followClips([clip], [{ ...clip, media_id: '202' }], doc);
  assert.equal(changed.audio_clips[0].media_id, '202');
  assert.equal(followClips([clip], [{ ...clip, media_id: '303', source_type: 'image' }], doc).audio_clips.length, 0);
  assert.equal(followClips([clip], [], { ...doc, audio_clips: doc.audio_clips.map(c => ({ ...c, anchor_clip_id: null })) }).audio_clips.length, 1);
});
test('frame rounding never extends the audio source beyond its real duration', () => {
  const source = { ...clip, duration_ms: 1017, trim_out_ms: null };
  const length = toMs(buildTimeline([source])[0].end);
  assert.ok(sourceAudio(source, 0, length).trim_out_ms <= 1017);
});
test('restoring a picture trim restores the full linked original, retaining its own mute', () => {
  const initial = normalizeSound(undefined, [clip]); initial.audio_clips[0].muted = true;
  const trimmed = [{ ...clip, trim_in_ms: 1000, trim_out_ms: 4000 }];
  const sound = followClips([clip], trimmed, initial);
  const restored = followClips(trimmed, [clip], sound);
  assert.equal(restored.audio_clips[0].duration_ms, 6000);
  assert.equal(restored.audio_clips[0].muted, true);
});
test('SRT import/export preserves Unicode and millisecond times, and rejects overlap', () => {
  const srt = '1\n00:00:00,123 --> 00:00:02,345\n你好，世界。\n第二行\n';
  const parsed = parseSrt(srt);
  assert.equal(exportSrt(parsed), srt);
  assert.throws(() => parseSrt(srt + '\n2\n00:00:01,000 --> 00:00:03,000\n重叠\n'), /重叠/);
  assert.throws(() => parseSrt('1\n00:61:00,000 --> 00:62:00,000\n错误'), /时间格式/);
  assert.equal(splitSubtitle(parsed, parsed[0].id, 1000).length, 2);
  const segments = resegmentSubtitles([{ ...speech, text: '第一句。第二句！第三句，继续。' }], 4);
  assert.equal(segments[0].start_ms, 1000); assert.equal(segments.at(-1).end_ms, 5000);
  assert.equal(segments.map(s => s.text).join(''), '第一句。第二句！第三句，继续。');
});
test('preview mix uses trim loops, fades, gain and the same voice ducking envelope', () => {
  const music = { ...sourceAudio(clip, 0, 6000), kind: 'music', volume: .5, gain_db: 0, ducking: true, loop: true, fade_in_ms: 1000, fade_out_ms: 1000 };
  const sound = { ...emptySound(), audio_clips: [music], native_ducking: [{ start_ms: 2000, end_ms: 3000, text: '对白' }] };
  assert.equal(audioGain(music, 500, sound), .25);
  assert.equal(audioGain(music, 2500, sound), .125);
  assert.equal(audioGain(music, 6000, sound), 0);
  assert.equal(audioSourceTime({ ...music, trim_in_ms: 1000, trim_out_ms: 3000 }, 4500), 1.5);
  assert.deepEqual(frameGeometry({ fit: 'cover', scale: 1, x: 1, y: .5 }, 100, 100, 200, 100), { width: 200, height: 100, left: -100, top: 0 });
});

test('music ducking combines native speech and audible dialogue without double attenuation', () => {
  const music = { ...sourceAudio(clip, 0, 6000), id: 'music', kind: 'music', ducking: true };
  const dialogue = { ...sourceAudio(clip, 2000, 1000), id: 'dialogue', kind: 'dialogue' };
  const sound = { ...emptySound(), audio_clips: [music, dialogue], native_ducking: [{ start_ms: 0, end_ms: 1000, text: '原生对白' }] };
  assert.equal(audioGain(music, 500, sound), .25);
  assert.equal(audioGain(music, 2500, sound), .25);
  assert.equal(audioGain(music, 3500, sound), 1);
  assert.equal(audioGain(music, 2500, { ...sound, native_ducking: [{ start_ms: 2000, end_ms: 3000 }] }), .25);
  assert.equal(audioGain(music, 2500, { ...sound, audio_clips: [music, { ...dialogue, muted: true }] }), 1);
  assert.equal(audioGain(music, 2500, { ...sound, tracks: [{ id: 'dialogue', muted: true, locked: false }] }), 1);
});

test('waveforms show the trimmed source and repeat its selected range for loops', () => {
  const source = sourceAudio(clip, 0, 6000);
  const trimmed = { ...source, trim_in_ms: 2000, trim_out_ms: 4000, duration_ms: 2000 };
  assert.deepEqual(waveformSlice([0, .1, .2, .3, .4, .5], trimmed, 6000, 2), [.2, .3]);
  assert.deepEqual(waveformSlice([0, .1, .2, .3, .4, .5], { ...trimmed, loop: true, duration_ms: 4000 }, 6000, 4), [.2, .3, .2, .3]);
  const loop = { ...trimmed, loop: true, duration_ms: 5000 };
  const split = splitAudio([loop], loop.id, 2500, 'second');
  assert.equal(audioSourceTime(split[1], 2500), audioSourceTime(loop, 2500));
  assert.equal(audioSourceTime(split[1], 3600), audioSourceTime(loop, 3600));
});

test('combined versions remain decimal BigInts and recovery retains sound and imported media', () => {
  const version = '9007199254740993';
  const state = { assembly: { id: '100', row_version: version, aspect: 'auto', resolution: '720p' }, clips: [{ ...clip, url: '/temporary', poster: null }], sound: { row_version: '5', document: emptySound(), media: {} } };
  const draft = assemblyDraft(state);
  assert.equal(BigInt(assemblyVersion(state)), (BigInt(version) << 64n) + 5n);
  assert.ok(validAssemblyDraft(draft)); assert.equal(draft.document.clips[0].url, null);
  assert.equal(validAssemblyDraft({ ...draft, base_version: `${version}:5` }), false);
  const material = { media_id: '101', kind: 'video', name: '导入视频', url: '/refreshed', poster: null, duration_ms: 6000, has_audio: true };
  const restored = restoreAssemblyDraft({ ...state, clips: [], materials: [material] }, draft);
  assert.equal(restored.clips[0].url, '/refreshed'); assert.equal(restored.clips[0].id, clip.id);
  assert.deepEqual(restored.sound.document, draft.document.sound);
});

test('auto preview uses actual output dimensions and manual aspect remains independent', () => {
  const value = { assembly: { aspect: 'auto', output_width: 1080, output_height: 1440 } };
  assert.equal(assemblyOutputAspect(value), '1080:1440');
  assert.equal(aspectRatio(assemblyOutputAspect(value)), .75);
  assert.equal(assemblyOutputAspect({ ...value, assembly: { ...value.assembly, aspect: '16:9' } }), '16:9');
  assert.equal(assemblyOutputAspect({ assembly: { aspect: 'auto' }, clips: [
    { included: true, source_type: 'video', width: 1920, height: 1080 },
    { included: true, source_type: 'shot', width: 720, height: 1280 },
  ] }), '720:1280');
});
