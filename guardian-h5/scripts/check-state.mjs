import assert from 'node:assert/strict';
import { INITIAL_SUBMISSION, toReady, prepare, timeout, beginCheck, applyQueryResult, conflict, saved, retry } from '../src/state/submission.ts';
import { navigationMemory, clearNavigationMemory } from '../src/state/navigation.ts';
import { TaskCoordinator, isTerminal } from '../src/state/tasks.ts';

// SubmissionCoordinator 状态机
let s = INITIAL_SUBMISSION;
assert.equal(s.phase, 'unavailable');
s = toReady(s);
assert.equal(s.phase, 'ready');
s = prepare(s, 'intent-1', 1, 'req-1');
assert.equal(s.phase, 'submitting');
assert.equal(s.generation, 1);
s = timeout(s);
assert.equal(s.phase, 'uncertain');
s = beginCheck(s);
assert.equal(s.phase, 'checking');
s = applyQueryResult(s, { state: 'committed', receipt: { resource_id: 'r1' } });
assert.equal(s.phase, 'committed');
s = saved(s, '/records/r1');
assert.equal(s.phase, 'saved');
assert.equal(s.recordPath, '/records/r1');

// failed + may_retry 允许重试；failed + !may_retry 保留 uncertain
s = prepare(toReady(INITIAL_SUBMISSION), 'i2', 1, 'r2');
s = applyQueryResult(s, { state: 'failed', may_retry: true });
assert.equal(s.phase, 'failed');
s = retry(s, 2);
assert.equal(s.phase, 'submitting');
assert.equal(s.generation, 2);

s = prepare(toReady(INITIAL_SUBMISSION), 'i3', 1, 'r3');
s = applyQueryResult(s, { state: 'failed', may_retry: false });
assert.equal(s.phase, 'uncertain');

// pending/processing/unknown/not_found 保持 uncertain
s = prepare(toReady(INITIAL_SUBMISSION), 'i4', 1, 'r4');
s = applyQueryResult(s, { state: 'not_found' });
assert.equal(s.phase, 'uncertain');

// conflict
s = conflict(prepare(toReady(INITIAL_SUBMISSION), 'i5', 1, 'r5'));
assert.equal(s.phase, 'conflict');

// NavigationMemory 内存保留与清理
navigationMemory.rememberScroll('k1', 42);
assert.equal(navigationMemory.readScroll('k1'), 42);
navigationMemory.setReturnContext('/home', { pathname: '/records', search: '', key: 'k1', scroll: 42 });
assert.equal(navigationMemory.readReturnContext('/home').scroll, 42);
clearNavigationMemory();
assert.equal(navigationMemory.readScroll('k1'), 0);

// TaskCoordinator 过期结果识别
const coordinator = new TaskCoordinator();
const t1 = { task_id: 't', status: 'running', status_version: 2 };
const t2 = { task_id: 't', status: 'running', status_version: 1 };
assert.equal(coordinator.isStale(t1), false);
assert.equal(coordinator.isStale(t2), true);
assert.equal(isTerminal({ task_id: 't', status: 'completed', status_version: 1 }), true);

console.log(JSON.stringify({ submission: 'PASS', navigation: 'PASS', tasks: 'PASS' }));
