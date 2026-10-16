// Pure display regression, not browser or backend evidence.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
const {eventTimeLabel,medicalSourceTimeLabel,medicalSourceZoneLabel}=await import('../src/components/recordTimePresentation.ts');
assert.equal(eventTimeLabel('2026-10-11T11:14:14Z'),'2026-10-11 11:14:14 UTC');
assert.equal(eventTimeLabel('2026-10-11T19:14:14+08:00'),'2026-10-11 19:14:14 UTC+08:00');
assert.equal(eventTimeLabel('2026-10-11T11:14:14'),'2026-10-11 11:14:14（时区未明确）');
assert.equal(eventTimeLabel('2026-10-11T11:14:14.622379+00:00'),'2026-10-11 11:14:14.622379 UTC+00:00');
const source={precision:'minute',raw_text:'原文 11:14',timezone_name:'Asia/Shanghai',utc_offset_minutes:480};
assert.equal(medicalSourceTimeLabel(source),'原文 11:14 · Asia/Shanghai（UTC+08:00）');
assert.equal(medicalSourceZoneLabel({...source,timezone_name:null,utc_offset_minutes:null}),'时区未明确');
assert.equal(medicalSourceZoneLabel({...source,timezone_name:null,utc_offset_minutes:-420}),'UTC-07:00');
assert.equal(medicalSourceTimeLabel({...source,precision:'month',raw_text:'2026年10月',timezone_name:null,utc_offset_minutes:null}),'2026年10月 · 时区未明确');
assert.equal(medicalSourceTimeLabel(null),'时间待补充');
assert.equal(source.raw_text,'原文 11:14');
console.log('PASS event instant/offset preserved; source raw text, timezone uncertainty and precision retained without browser-zone conversion');
