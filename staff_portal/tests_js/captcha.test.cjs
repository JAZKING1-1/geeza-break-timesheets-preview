const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/staff_portal/captcha.js'), 'utf8');

function harness(width = 320) {
  const events = {};
  const submit = { disabled: true };
  const status = { textContent: '', focus() { this.focused = true; } };
  const retry = { hidden: true, addEventListener(name, callback) { events.retry = callback; } };
  const reload = { hidden: true };
  const elements = { '[data-captcha-submit]': submit, '#captcha-status': status,
    '[data-captcha-retry]': retry, '[data-captcha-reload]': reload };
  const form = { querySelector: selector => elements[selector],
    addEventListener(name, callback) { events.submit = callback; } };
  const container = { closest: () => form, clientWidth: width,
    dataset: { sitekey: 'public-test-key', action: 'login' } };
  const state = { response: '', expired: false, resets: 0 };
  const turnstile = {
    render(element, options) { assert.equal(element, container); state.options = options; return 'widget-id'; },
    getResponse: () => state.response, isExpired: () => state.expired,
    reset(id) { assert.equal(id, 'widget-id'); state.resets++; state.response = ''; },
  };
  const window = { turnstile, addEventListener(name, callback) { events[name] = callback; } };
  const document = { querySelector: () => container,
    getElementById: () => ({ addEventListener(name, callback) { events.loadError = callback; } }) };
  vm.runInNewContext(source, { window, document,
    setTimeout(callback) { events.slow = callback; return 1; }, clearTimeout() {} });
  function send() {
    let blocked = false;
    events.submit({ preventDefault() { blocked = true; } });
    return blocked;
  }
  function success() { state.response = 'fictional-token'; state.options.callback(); }
  return { events, submit, status, retry, reload, window, state, send, success };
}

test('early button or Enter submission is blocked until a token is ready', () => {
  const h = harness();
  assert.equal(h.send(), true);
  assert.equal(h.submit.disabled, true);
  h.window.portalTurnstileReady();
  assert.equal(h.send(), true);
  h.success();
  assert.equal(h.submit.disabled, false);
  assert.equal(h.send(), false);
});

test('expired or missing responses cannot submit despite an earlier success', () => {
  for (const change of [h => h.state.expired = true, h => h.state.response = '']) {
    const h = harness(); h.window.portalTurnstileReady(); h.success(); change(h);
    assert.equal(h.send(), true);
    assert.equal(h.submit.disabled, true);
  }
});

test('failed, timed-out and expired checks disable submission and allow retry', () => {
  for (const callback of ['error-callback', 'expired-callback', 'timeout-callback']) {
    const h = harness(); h.window.portalTurnstileReady(); h.success();
    h.state.options[callback]();
    assert.equal(h.submit.disabled, true);
    assert.equal(h.retry.hidden, false);
    h.events.retry();
    assert.equal(h.state.resets, 1);
    assert.equal(h.send(), true);
    h.success();
    assert.equal(h.send(), false);
  }
});

test('blocked or slow provider script offers recovery and never unlocks submission', () => {
  for (const callback of ['loadError', 'slow']) {
    const h = harness(); h.events[callback]();
    assert.equal(h.submit.disabled, true);
    assert.equal(h.reload.hidden, false);
    assert.equal(h.retry.hidden, true);
    assert.equal(h.send(), true);
  }
});

test('browser back restores require a new token; narrow screens use compact widget', () => {
  const h = harness(240); h.window.portalTurnstileReady(); h.success();
  assert.equal(h.state.options.size, 'compact');
  assert.equal(h.state.options.action, 'login');
  h.events.pageshow({ persisted: true });
  assert.equal(h.state.resets, 1);
  assert.equal(h.submit.disabled, true);
  assert.equal(h.send(), true);
});
